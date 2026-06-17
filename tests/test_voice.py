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
        # (text, prompt_language, text_language, how_to_cut)
        self.calls: list[tuple] = []

    def __call__(self, *, ref_wav_path, prompt_text, prompt_language, text,
                 text_language, how_to_cut):
        import numpy as np

        self.calls.append((text, prompt_language, text_language, how_to_cut))
        yield (32000, np.zeros(8, dtype=np.int16))


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


async def test_gptsovits_tail_without_terminator_is_spoken():
    mouth, fake, played = _build_gptsovits()
    await mouth.speak_stream(_stream("しゅうけつ ", "ふごう ", "なし"), VOICE)
    assert [c[0] for c in fake.calls] == ["しゅうけつ ふごう なし"]  # 꼬리말도 합성
    assert len(played) == 1


async def test_gptsovits_passes_language_and_cut_args():
    # tts_fn 주입 시 i18n이 없으므로 소스 문자열을 그대로 전달한다.
    mouth, fake, _ = _build_gptsovits(ref_lang="ja", gen_lang="ko")
    await mouth.speak_stream(_stream("ひとつ."), VOICE)
    assert fake.calls[0][1] == "日文"  # prompt_language (ref=ja)
    assert fake.calls[0][2] == "韩文"  # text_language (gen=ko)
    assert fake.calls[0][3] == "不切"  # how_to_cut — 청킹은 우리가 함


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


def test_unknown_vendor_raises_value_error():
    with pytest.raises(ValueError):
        create_stt("nope")
    with pytest.raises(ValueError):
        create_mouth("nope")
