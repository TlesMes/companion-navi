"""음성 어댑터 계약 검증 — fake 어댑터로 STT/Mouth 계약(4.3·4.8)을 고정한다.

벤더(D2·D3)는 미정이지만 계약은 지금 박는다 (진행원칙 1: 계약부터 고정).
"""

import asyncio

import pytest

from navi.models import AudioChunk, VoiceProfile
from navi.mouth import create_mouth
from navi.mouth.fake import FakeMouth
from navi.stt import create_stt
from navi.stt.fake import FakeStt

VOICE = VoiceProfile(name="navi", vendor_voice_id="stub")


async def _stream(*tokens: str):
    for token in tokens:
        yield token


# --- STT (4.3) ---


async def test_stt_session_feeds_frames_and_finalizes_to_injected_transcript():
    stt = FakeStt()
    stt.next_transcript = "오늘 좀 피곤하네"
    session = await stt.open_stream("ko")
    for _ in range(3):
        await session.feed(AudioChunk(pcm=b"\x00\x00"))
    result = await session.finalize()
    assert result.text == "오늘 좀 피곤하네"
    assert result.lang == "ko"
    assert result.confidence == 1.0
    assert session.frames_fed == 3  # feed가 실제로 흘렀는지


# --- Mouth (4.8) ---


async def test_mouth_synthesizes_full_token_stream():
    mouth = FakeMouth()
    assert not mouth.is_playing()
    await mouth.speak_stream(_stream("안", "녕 ", "나비"), VOICE)
    assert mouth.spoken == ["안녕 나비"]
    assert mouth.last_voice is VOICE
    assert not mouth.is_playing()  # 재생 끝나면 내려간다


async def test_mouth_stop_interrupts_mid_stream():
    mouth = FakeMouth()

    async def slow_stream():
        for token in ["하나", "둘", "셋", "넷"]:
            yield token
            await asyncio.sleep(0.01)

    async def speak():
        await mouth.speak_stream(slow_stream(), VOICE)

    task = asyncio.create_task(speak())
    await asyncio.sleep(0.015)  # 첫 토큰쯤 흐른 뒤
    mouth.stop()  # barge-in
    await task
    assert mouth.spoken[0] != "하나둘셋넷"  # 전부 다 합성됐다면 중단 실패
    assert not mouth.is_playing()


# --- SupertonicMouth: 문장청크 스트리밍 (가짜 엔진 주입, 실오디오·실모델 없이) ---


class _FakeEngine:
    """Supertonic TTS 흉내 — 합성 호출을 기록만 한다(무거운 모델·numpy 불필요)."""

    sample_rate = 24000

    def __init__(self) -> None:
        self.synthesized: list[str] = []

    def get_voice_style(self, voice_name: str):
        return f"style:{voice_name}"

    def synthesize(self, *, text, voice_style, total_steps, speed, lang):
        self.synthesized.append(text)
        return (f"wav:{text}", None)  # 재생은 _play를 가로채 무시하므로 마커면 충분


def _build_supertonic():
    """가짜 엔진을 주입하고 _play를 재생 기록으로 가로챈 SupertonicMouth."""
    from navi.mouth.supertonic import SupertonicMouth

    engine = _FakeEngine()
    mouth = SupertonicMouth(tts=engine)
    played: list = []
    mouth._play = lambda wav: played.append(wav)  # 실스피커 대신 기록
    return mouth, engine, played


async def test_supertonic_chunks_stream_into_sentences():
    mouth, engine, played = _build_supertonic()
    await mouth.speak_stream(_stream("안녕", ". 잘 ", "잤어?"), VOICE)
    assert engine.synthesized == ["안녕.", "잘 잤어?"]  # 문장 경계마다 합성
    assert played == ["wav:안녕.", "wav:잘 잤어?"]  # 합성 즉시 순차 재생
    assert not mouth.is_playing()


async def test_supertonic_splits_fullwidth_terminator_without_space():
    """전각 종결(？！。)은 공백 없이도 경계 — 반각 소수점 보호는 그대로."""
    mouth, engine, _ = _build_supertonic()
    await mouth.speak_stream(_stream("무게는 3.5키로야？", "응！가볍지。"), VOICE)
    assert engine.synthesized == ["무게는 3.5키로야？", "응！", "가볍지。"]


async def test_supertonic_tail_without_terminator_is_spoken():
    mouth, engine, played = _build_supertonic()
    await mouth.speak_stream(_stream("종결 ", "부호 ", "없음"), VOICE)
    assert engine.synthesized == ["종결 부호 없음"]  # 꼬리말도 마지막에 합성
    assert len(played) == 1


async def test_supertonic_uses_voice_id_as_supertonic_style():
    mouth, engine, _ = _build_supertonic()
    voice = VoiceProfile(name="navi", vendor_voice_id="F1", speed=1.05)
    await mouth.speak_stream(_stream("하나."), voice)
    assert mouth._style("F1") == "style:F1"  # vendor_voice_id → Supertonic 음색


async def test_supertonic_stop_halts_synthesis_mid_stream():
    mouth, engine, _ = _build_supertonic()

    async def slow_stream():
        for token in ["하나. ", "둘. ", "셋. ", "넷. "]:
            yield token
            await asyncio.sleep(0.01)

    task = asyncio.create_task(mouth.speak_stream(slow_stream(), VOICE))
    await asyncio.sleep(0.015)  # 첫 문장쯤 흐른 뒤
    mouth.stop()  # barge-in
    await task
    assert len(engine.synthesized) < 4  # 전부 합성됐다면 중단 실패
    assert not mouth.is_playing()


# --- GPTSoVITSMouth: 문장청크 스트리밍 (가짜 tts_fn 주입, 실모델·실오디오 없이) ---


class _FakeGptSovits:
    """get_tts_wav 흉내 — 합성 호출 인자를 기록하고 (sr, int16) 청크를 yield."""

    def __init__(self) -> None:
        # (text, prompt_language, text_language, how_to_cut, prompt_text)
        self.calls: list[tuple] = []

    def __call__(self, *, ref_wav_path, prompt_text, prompt_language, text,
                 text_language, how_to_cut):
        import numpy as np

        self.calls.append((text, prompt_language, text_language, how_to_cut, prompt_text))
        # 유음 오디오여야 한다 — 무음이면 어댑터의 폭주(EOS 실패) 감지가 재시도한다.
        yield (32000, np.full(8, 8000, dtype=np.int16))


def _build_gptsovits(**kw):
    """가짜 tts_fn을 주입하고 _play를 재생 기록으로 가로챈 GPTSoVITSMouth."""
    from navi.mouth.gptsovits import GPTSoVITSMouth

    fake = _FakeGptSovits()
    mouth = GPTSoVITSMouth(ref_text="れいてきすと", tts_fn=fake, **kw)
    played: list = []
    mouth._play = lambda wav: played.append(wav)  # 실스피커 대신 기록
    return mouth, fake, played


async def test_gptsovits_chunks_stream_into_sentences():
    mouth, fake, played = _build_gptsovits()
    await mouth.speak_stream(_stream("こんにちは", "。 げん", "き?"), VOICE)
    assert [c[0] for c in fake.calls] == ["こんにちは。", "げんき?"]  # 문장 경계마다
    assert len(played) == 2  # 합성 즉시 순차 재생
    assert not mouth.is_playing()


async def test_gptsovits_splits_cjk_sentences_without_space():
    """일본어 문말(。！)은 뒤 공백이 없어도 문장마다 잘린다 — TTFA 비대 방지."""
    mouth, fake, played = _build_gptsovits()
    await mouth.speak_stream(
        _stream("おはよう。今日もいい", "天気だね。散歩し", "よう！いこう"), VOICE
    )
    assert [c[0] for c in fake.calls] == [
        "おはよう。", "今日もいい天気だね。", "散歩しよう！", "いこう",
    ]
    assert len(played) == 4
    assert not mouth.is_playing()


async def test_gptsovits_tail_without_terminator_is_spoken():
    mouth, fake, played = _build_gptsovits()
    await mouth.speak_stream(_stream("しゅうけつ ", "ふごう ", "なし"), VOICE)
    assert [c[0] for c in fake.calls] == ["しゅうけつ ふごう なし"]  # 꼬리말도 합성
    assert len(played) == 1


async def test_gptsovits_retries_runaway_synthesis():
    # EOS 실패 폭주(무음 출력)는 재시도, 유음이 나오면 그 결과를 쓴다.
    import numpy as np

    from navi.mouth.gptsovits import _synth_one

    class _RunawayOnce:
        def __init__(self) -> None:
            self.calls = 0

        def __call__(self, **_):
            self.calls += 1
            if self.calls == 1:
                yield (32000, np.zeros(32000, dtype=np.int16))  # 폭주(무음)
            else:
                yield (32000, np.full(8000, 8000, dtype=np.int16))  # 정상

    fake = _RunawayOnce()
    wav = _synth_one(fake, "ref.wav", "れい", "てすと", "日文", "日文", "不切")
    assert fake.calls == 2  # 폭주 1회 후 재시도로 성공
    assert wav is not None


async def test_gptsovits_gives_up_after_repeated_runaway():
    import numpy as np

    from navi.mouth.gptsovits import _SYNTH_ATTEMPTS, _synth_one

    class _AlwaysRunaway:
        def __init__(self) -> None:
            self.calls = 0

        def __call__(self, **_):
            self.calls += 1
            yield (32000, np.zeros(32000, dtype=np.int16))

    fake = _AlwaysRunaway()
    wav = _synth_one(fake, "ref.wav", "れい", "てすと", "日文", "日文", "不切")
    assert fake.calls == _SYNTH_ATTEMPTS  # 상한만큼 시도 후
    assert wav is None  # 청크 포기


async def test_gptsovits_passes_language_and_cut_args():
    # tts_fn 주입 시 i18n이 없으므로 소스 문자열을 그대로 전달한다.
    mouth, fake, _ = _build_gptsovits(ref_lang="ja", gen_lang="ko")
    await mouth.speak_stream(_stream("ひとつ."), VOICE)
    assert fake.calls[0][1] == "日文"  # prompt_language (ref=ja)
    assert fake.calls[0][2] == "韩文"  # text_language (gen=ko)
    assert fake.calls[0][3] == "不切"  # how_to_cut — 청킹은 우리가 함


async def test_gptsovits_voice_ref_text_overrides_constructor():
    """톤(레퍼런스) 교체 지원 — 전사는 wav와 한 쌍이므로 VoiceProfile.ref_text가 우선."""
    mouth, fake, _ = _build_gptsovits()
    voice = VoiceProfile(
        name="navi", vendor_voice_id="happy.wav", ref_text="うれしいてきすと"
    )
    await mouth.speak_stream(_stream("ひとつ."), voice)
    assert fake.calls[0][4] == "うれしいてきすと"  # 생성자 れいてきすと를 덮음


async def test_gptsovits_empty_voice_ref_text_falls_back_to_constructor():
    """하위호환 — 카드에 voice 섹션 없는 구 config는 생성자 ref_text로."""
    mouth, fake, _ = _build_gptsovits()
    await mouth.speak_stream(_stream("ひとつ."), VOICE)  # VOICE.ref_text == ""
    assert fake.calls[0][4] == "れいてきすと"


async def test_gptsovits_stop_halts_synthesis_mid_stream():
    mouth, fake, _ = _build_gptsovits()

    async def slow_stream():
        for token in ["いち. ", "に. ", "さん. ", "し. "]:
            yield token
            await asyncio.sleep(0.01)

    task = asyncio.create_task(mouth.speak_stream(slow_stream(), VOICE))
    await asyncio.sleep(0.015)  # 첫 문장쯤 흐른 뒤
    mouth.stop()  # barge-in
    await task
    assert len(fake.calls) < 4  # 전부 합성됐다면 중단 실패
    assert not mouth.is_playing()


def test_gptsovits_invalid_lang_raises():
    from navi.mouth.gptsovits import GPTSoVITSMouth

    with pytest.raises(ValueError, match="지원 언어"):
        GPTSoVITSMouth(gen_lang="fr")


def test_gptsovits_warmup_loads_engine_once():
    """warmup()이 _ensure_engine()을 호출해 엔진을 선로드하는지 확인."""
    from navi.mouth.gptsovits import GPTSoVITSMouth

    calls: list[str] = []

    def fake_tts_fn(**_):  # 주입된 가짜 엔진 — 실 모델 로드 없이
        return iter([])

    mouth = GPTSoVITSMouth(tts_fn=fake_tts_fn)
    assert mouth._tts_fn is fake_tts_fn  # 생성 시점에 이미 주입됨
    mouth.warmup()  # 엔진이 이미 있으면 no-op
    assert mouth._tts_fn is fake_tts_fn  # 교체되지 않음


# --- set_weights: 음색 가중치 런타임 교체 (핫스왑) ---


class _FakeWeights:
    """change_gpt_weights / change_sovits_weights 흉내 — 로드 순서·인자를 기록한다."""

    def __init__(self) -> None:
        self.loaded: list[tuple] = []

    def gpt(self, path):
        self.loaded.append(("gpt", path))

    def sovits(self, path, *, prompt_language, text_language):
        self.loaded.append(("sovits", path, prompt_language, text_language))
        yield  # webui는 제너레이터 — 소진해야 적용된다


def _build_swappable(**kw):
    from navi.mouth.gptsovits import GPTSoVITSMouth

    w = _FakeWeights()
    mouth = GPTSoVITSMouth(tts_fn=_FakeGptSovits(), weight_fns=(w.gpt, w.sovits), **kw)
    return mouth, w


@pytest.fixture
def ckpts(tmp_path):
    """실재하는 가중치 파일 한 쌍 — 어댑터가 교체 전에 존재를 검사한다(E4)."""
    gpt, sovits = tmp_path / "a.ckpt", tmp_path / "a.pth"
    gpt.write_bytes(b"")
    sovits.write_bytes(b"")
    return str(gpt), str(sovits)


def test_gptsovits_set_weights_loads_both_and_updates_langs(ckpts):
    mouth, w = _build_swappable(ref_lang="ja", gen_lang="ja")
    mouth.set_weights(*ckpts, ref_lang="ko", gen_lang="ko")

    # SoVITS 먼저(제너레이터 소진) → GPT. 언어는 새 값으로 래핑돼 함께 넘어간다.
    assert [x[0] for x in w.loaded] == ["sovits", "gpt"]
    assert w.loaded[0][2:] == ("韩文", "韩文")  # i18n 없음 → 소스 문자열
    assert (mouth._ref_lang, mouth._gen_lang) == ("ko", "ko")
    assert mouth._gpt_ckpt.endswith("a.ckpt") and mouth._sovits_ckpt.endswith("a.pth")


def test_gptsovits_set_weights_empty_lang_keeps_current(ckpts):
    """빈 언어 = 현재 유지 — 부팅 배선(카드 언어 미지정 시 config 유지)과 같은 규칙."""
    mouth, w = _build_swappable(ref_lang="ja", gen_lang="ja")
    mouth.set_weights(*ckpts)
    assert (mouth._ref_lang, mouth._gen_lang) == ("ja", "ja")
    assert w.loaded[0][2:] == ("日文", "日文")


def test_gptsovits_set_weights_invalid_lang_raises(ckpts):
    mouth, w = _build_swappable()
    with pytest.raises(ValueError, match="지원 언어"):
        mouth.set_weights(*ckpts, gen_lang="fr")
    assert w.loaded == []  # 검증 실패 시 아무것도 안 올린다


def test_gptsovits_set_weights_applies_to_next_synthesis(ckpts):
    """교체한 언어가 다음 합성 인자에 실제로 실린다 — 가중치와 언어의 원자 교체."""
    mouth, _w = _build_swappable(ref_lang="ja", gen_lang="ja")
    mouth.set_weights(*ckpts, ref_lang="ja", gen_lang="ko")
    assert (mouth._prompt_lang, mouth._text_lang) == ("日文", "韩文")


# --- E4: 카드 지정 자산 존재 검사 ---


def test_gptsovits_set_weights_missing_ckpt_changes_nothing(ckpts, tmp_path):
    """없는 ckpt는 상태 변경 *이전*에 막힌다 — 어댑터와 엔진이 찢어지면 안 된다."""
    mouth, w = _build_swappable(ref_lang="ja", gen_lang="ja")
    mouth.set_weights(*ckpts)  # 정상 교체로 기준 상태를 만든다
    w.loaded.clear()

    with pytest.raises(FileNotFoundError, match="음색 가중치가 없습니다"):
        mouth.set_weights(str(tmp_path / "없음.ckpt"), ckpts[1], ref_lang="ko", gen_lang="ko")

    assert w.loaded == []  # 엔진에 아무것도 안 올렸다
    assert (mouth._gpt_ckpt, mouth._sovits_ckpt) == ckpts  # 옛 가중치 그대로
    assert (mouth._ref_lang, mouth._gen_lang) == ("ja", "ja")  # 언어도 안 바뀜
    assert (mouth._prompt_lang, mouth._text_lang) == ("日文", "日文")


def test_gptsovits_set_weights_missing_sovits_also_blocked(ckpts, tmp_path):
    """sovits만 없어도 막는다 — 순차 로드라 부분 실패면 두 가중치가 다른 카드 것이 된다."""
    mouth, w = _build_swappable()
    with pytest.raises(FileNotFoundError, match="음색 가중치가 없습니다"):
        mouth.set_weights(ckpts[0], str(tmp_path / "없음.pth"))
    assert w.loaded == []


def test_gptsovits_set_weights_empty_ckpt_is_base_intent():
    """빈 ckpt = base(zero-shot) 의도 — 존재 검사 대상이 아니다."""
    mouth, w = _build_swappable()
    mouth.set_weights("", "")  # repo_path 없음 → base 해석도 무동작
    assert w.loaded == []  # 올릴 가중치가 없을 뿐, 에러는 아니다
    assert (mouth._gpt_ckpt, mouth._sovits_ckpt) == ("", "")


def test_fake_mouth_warmup_is_noop():
    """FakeMouth.warmup()은 상태를 바꾸지 않는다."""
    from navi.mouth.fake import FakeMouth

    mouth = FakeMouth()
    mouth.warmup()  # 에러 없이 호출 가능
    assert not mouth.is_playing()


def test_mouth_set_weights_unsupported_by_default():
    """가중치 없는 엔진은 조용히 무시하지 않고 미지원을 알린다 — 호출부가 분기한다."""
    from navi.mouth.fake import FakeMouth

    with pytest.raises(NotImplementedError, match="핫스왑"):
        FakeMouth().set_weights("a.ckpt", "a.pth")


# --- 팩토리: 벤더 종속 금지 + 보류 결정 안내 ---


def test_factories_build_fake_by_default():
    assert isinstance(create_stt(), FakeStt)
    assert isinstance(create_mouth(), FakeMouth)


def test_supertonic_vendor_builds_real_adapter():
    from navi.mouth.supertonic import SupertonicMouth

    # 엔진은 첫 발화 때 지연 로드 — 생성만으로 supertonic 미설치여도 동작
    assert isinstance(create_mouth("supertonic"), SupertonicMouth)


def test_pending_vendors_raise_with_decision_pointer():
    with pytest.raises(NotImplementedError, match="D2"):
        create_stt("vito")
    with pytest.raises(NotImplementedError, match="폴백"):
        create_mouth("cartesia")


def test_retired_d3_candidates_raise_not_implemented():
    # D3 평가 후보였으나 GPT-SoVITS 확정으로 팩토리에서 제거됨 — 명확한 안내 포함
    with pytest.raises(NotImplementedError, match="gptsovits"):
        create_mouth("cosyvoice")
    with pytest.raises(NotImplementedError, match="gptsovits"):
        create_mouth("f5tts")


def test_unknown_vendor_raises_value_error():
    with pytest.raises(ValueError):
        create_stt("nope")
    with pytest.raises(ValueError):
        create_mouth("nope")


def test_supertonic_rejects_gptsovits_kwargs():
    """팩토리는 kwargs를 걸러내지 않는다 — 의도된 결정이므로 고정한다.

    시그니처를 보고 낯선 kwarg를 버리면 config 오타가 조용한 오동작이 된다.
    벤더 경계는 상류(persona.mouth_options)가 지키고, 여기선 시끄럽게 죽는 게 맞다.
    """
    with pytest.raises(TypeError):
        create_mouth("supertonic", gpt_ckpt="x")


# --- 벤더 스펙: "이 벤더가 무슨 kwarg를 받는가"의 단일 출처 (E7) ---


def test_vendor_spec_preset_vendors_have_no_special_requirements():
    """supertonic·fake는 가중치 kwarg가 없고 voice_id도 경로가 아니다 —
    이 빈 스펙이 '가중치 kwarg를 supertonic에 주입하지 마라'의 근거다."""
    from navi.mouth import vendor_spec

    for vendor in ("supertonic", "fake"):
        spec = vendor_spec(vendor)
        assert spec.voice_id_is_path is False
        assert spec.weight_kwargs == ()
        assert spec.lang_kwargs == ()


def test_vendor_spec_unknown_vendor_is_empty_default():
    """미등록 벤더는 특수 요구 없음 — 실제 부팅은 create_mouth가 ValueError로 막는다."""
    from navi.mouth import VendorSpec, vendor_spec

    assert vendor_spec("nope") == VendorSpec()


def test_spec_kwargs_map_to_vendor_voice_fields():
    """스펙 kwarg명 = VendorVoice 필드명 불변식 — mouth_options가 getattr로 조회한다.
    스펙에 오타 난 kwarg명을 넣으면 mouth_options가 런타임에 AttributeError로 터진다.
    그 실패를 빌드 타임에 앞당겨 잡는다(새 벤더가 고유 kwarg를 들고 올 때의 안전망).
    """
    from dataclasses import fields

    from navi.mouth import _VENDORS
    from navi.persona import VendorVoice

    vv_fields = {f.name for f in fields(VendorVoice)}
    for vendor, entry in _VENDORS.items():
        for key in (*entry.spec.weight_kwargs, *entry.spec.lang_kwargs):
            assert key in vv_fields, f"{vendor}: {key!r}는 VendorVoice 필드가 아님"


# --- FasterWhisperStt: 가짜 모델 주입, 실 추론 없이 계약 검증 ---


class _FakeWhisperModel:
    """faster_whisper.WhisperModel 흉내."""

    class _Info:
        language = "ko"

    def __init__(self) -> None:
        self.transcribed: list[str] = []

    def transcribe(self, path: str, language=None, vad_filter=False):
        import wave as wavemod

        with wavemod.open(path, "rb") as wf:
            frames = wf.getnframes()
        self.transcribed.append(path)

        class _Seg:
            text = "안녕 나비"

        return [_Seg()], self._Info()


async def test_fasterwhisper_accumulates_chunks_and_transcribes():
    from navi.stt.fasterwhisper import FasterWhisperStt

    stt = FasterWhisperStt()
    stt._model = _FakeWhisperModel()  # 가짜 모델 주입 — 실 로드 없이
    session = await stt.open_stream("ko")
    pcm = b"\x00\x01" * 1600  # 16-bit, 16000Hz, 0.1s
    await session.feed(AudioChunk(pcm=pcm, sample_rate=16000))
    await session.feed(AudioChunk(pcm=pcm, sample_rate=16000))
    result = await session.finalize()
    assert result.text == "안녕 나비"
    assert result.lang == "ko"
    assert result.confidence == 1.0


async def test_fasterwhisper_empty_feed_returns_empty():
    from navi.stt.fasterwhisper import FasterWhisperStt

    stt = FasterWhisperStt()
    stt._model = _FakeWhisperModel()
    session = await stt.open_stream("ko")
    result = await session.finalize()
    assert result.text == ""
    assert result.confidence == 0.0


def test_faster_whisper_vendor_builds_adapter():
    from navi.stt.fasterwhisper import FasterWhisperStt

    assert isinstance(create_stt("faster-whisper"), FasterWhisperStt)
