"""GPT-SoVITS V2 실어댑터 — 두뇌 토큰 스트림을 로컬에서 합성·재생한다 (D3 유력안).

음색은 fine-tune 가중치(.pth/.ckpt)가 안정적으로 담당하고, 톤·억양은 레퍼런스 오디오로
제어한다(D3 결론 — 메모리 gptsovits-d3-prosody). GPT-SoVITS는 Web UI 중심 설계라 라이브러리로
직접 쓰려면 repo가 PYTHONPATH에 있어야 하고, inference_webui가 import 시점에 가중치를 즉시
로드하므로 베이스/가중치 경로를 env로 못박아야 한다.

WSL이 아니라 **Windows native 단일 프로세스**에서 in-process로 돈다(ROCm 불가 확정 → CPU torch는
Windows에서 동일 동작 → WSL 유지 이유 없음). GPU 백엔드(CUDA/DirectML)는 device 인자로 추후 교체.

환경 셋업 (Windows, 전용 3.12 venv):
  git clone --depth 1 https://github.com/RVC-Boss/GPT-SoVITS C:\\gptsovits   # ASCII 경로
  pip install torch torchaudio --index-url https://download.pytorch.org/whl/cpu  # torchcodec 제외
  pip install -r C:\\gptsovits\\requirements.win-cpu.txt   # onnxruntime(CPU)·opencc wheel
  # 베이스 모델: huggingface_hub.snapshot_download("lj1995/GPT-SoVITS",
  #   allow_patterns=["chinese-hubert-base/*","chinese-roberta-wwm-ext-large/*"],
  #   local_dir="C:\\gptsovits\\GPT_SoVITS\\pretrained_models")
  #   ↑ hubert/roberta는 전처리(feature/BERT)일 뿐 — TTS 가중치가 아니다.
  #   fine-tune 없이 base(zero-shot)로 돌리려면 v2ProPlus base(s1v3.ckpt +
  #   v2Pro/s2Gv2ProPlus.pth + sv/*, 약 460MB)도 같은 repo에서 받아야 한다. ckpt를
  #   안 넘기면 어댑터가 이 base 경로를 명시 지정한다 — inference_webui 자체 폴백은
  #   weight.json(마지막 사용 가중치)이라 믿을 수 없음(2026.07.14 실측).
  #   personas/example_jp.yaml이 그 경로.
  # mkdir C:\\gptsovits\\GPT_SoVITS\\pretrained_models\\fast_langdetect  (lid.176.bin 다운로드 대비)

사용:
  create_mouth(
      "gptsovits",
      repo_path=r"C:\\gptsovits",
      gpt_ckpt=r"...\\voice_ref\\arisu-e15.ckpt",
      sovits_ckpt=r"...\\voice_ref\\arisu_e8_s352.pth",
      ref_text="레퍼런스 오디오 전사",  # ref_lang 언어로
      ref_lang="ja", gen_lang="ja",
  )
  # VoiceProfile.vendor_voice_id = 레퍼런스 wav 경로
"""

from __future__ import annotations

import asyncio
import logging
import os
import sys
import time
from collections.abc import AsyncIterator
from typing import Any

from navi.models import VoiceProfile
from navi.mouth.base import MouthAdapter
from navi.mouth.sentence import SENTENCE_END

logger = logging.getLogger(__name__)

_OUTPUT_SR = 32_000  # GPT-SoVITS 기본 출력 샘플레이트

# 언어 코드 → i18n 소스 문자열 (dict_language 키와 일치)
_GPTSOVITS_LANG = {"ja": "日文", "ko": "韩文", "zh": "中文", "en": "英文"}


class GPTSoVITSMouth(MouthAdapter):
    def __init__(
        self,
        *,
        ref_text: str = "",
        repo_path: str | None = None,
        gpt_ckpt: str | None = None,
        sovits_ckpt: str | None = None,
        ref_lang: str = "ja",
        gen_lang: str = "ja",
        device: str = "cpu",  # 백엔드 추상화 자리 — 추후 cuda/dml로 교체
        tts_fn: Any = None,  # 테스트 주입용 (실모델·실오디오 없이 고정)
        weight_fns: Any = None,  # 테스트 주입용 (change_gpt, change_sovits) — 핫스왑 검증
    ) -> None:
        if ref_lang not in _GPTSOVITS_LANG or gen_lang not in _GPTSOVITS_LANG:
            raise ValueError(
                f"지원 언어: {sorted(_GPTSOVITS_LANG)} (ref={ref_lang}, gen={gen_lang})"
            )
        self._ref_text = ref_text
        self._repo_path = repo_path
        self._gpt_ckpt = gpt_ckpt
        self._sovits_ckpt = sovits_ckpt
        self._ref_lang = ref_lang
        self._gen_lang = gen_lang
        self._device = device
        self._tts_fn = tts_fn
        # 엔진 심볼 — _ensure_engine이 import해 채운다(테스트는 weight_fns로 주입).
        self._i18n: Any = None
        self._change_gpt_weights, self._change_sovits_weights = (
            weight_fns if weight_fns is not None else (None, None)
        )
        # i18n으로 래핑된 인자 — _ensure_engine에서 채운다. 테스트 주입 시엔
        # i18n이 없으므로 소스 문자열을 그대로 쓴다.
        self._prompt_lang = _GPTSOVITS_LANG[ref_lang]
        self._text_lang = _GPTSOVITS_LANG[gen_lang]
        self._how_to_cut = "不切"  # 자르지 않음 — 청킹은 우리가 문장 경계로 함
        self._playing = False
        self._stopped = False

    def _ensure_engine(self) -> Any:
        if self._tts_fn is not None:
            return self._tts_fn

        # ckpt 경로를 먼저 절대경로로 고정한다 — 아래에서 CWD를 repo로 바꾸면
        # 호출부가 넘긴 상대경로(navi 기준)가 깨지기 때문.
        if self._gpt_ckpt:
            self._gpt_ckpt = os.path.abspath(self._gpt_ckpt)
        if self._sovits_ckpt:
            self._sovits_ckpt = os.path.abspath(self._sovits_ckpt)

        # repo 루트(= `from GPT_SoVITS.x`, `import config`)·GPT_SoVITS/ 하위(= 내부
        # `from text.x`)·eres2net(= sv.py의 `from ERes2NetV2 import`) 셋 다 path에 필요.
        if self._repo_path:
            # chdir 전에 고정한다 — 상대경로면 chdir 뒤 abspath가 달라진다.
            repo = self._repo_path = os.path.abspath(self._repo_path)
            sys.path.insert(0, os.path.join(repo, "GPT_SoVITS", "eres2net"))
            sys.path.insert(0, os.path.join(repo, "GPT_SoVITS"))
            sys.path.insert(0, repo)

            # GPT-SoVITS는 곳곳에서 os.getcwd() 기준 상대경로를 쓴다(sv.py의 sv_path,
            # eres2net append 등). WSL은 CWD=repo로 돌렸으므로 동일하게 맞춘다.
            os.chdir(repo)

            # inference_webui는 import 시점에 베이스/가중치를 즉시 로드한다(모듈 레벨).
            pre = os.path.join(repo, "GPT_SoVITS", "pretrained_models")
            os.environ.setdefault(
                "cnhubert_base_path", os.path.join(pre, "chinese-hubert-base")
            )
            os.environ.setdefault(
                "bert_path", os.path.join(pre, "chinese-roberta-wwm-ext-large")
            )
            # fast_langdetect는 이 디렉토리가 없으면 lid.176.bin 다운로드 전에 죽는다.
            # 첫 추론 시 fasttext 125MB + open_jtalk 사전이 자동으로 여기로 받아진다.
            os.makedirs(os.path.join(pre, "fast_langdetect"), exist_ok=True)

            # pyopenjtalk(일본어 G2P)의 mecab 사전이 venv(한글 경로) 안에 있으면 mecab C++가
            # 경로를 ANSI로 해석해 못 연다(repo를 ASCII 경로에 둔 것과 같은 함정 — 단축경로도
            # 한글이 남아 무력). 사전을 ASCII 경로(repo 옆)로 복사해두고 OPEN_JTALK_DICT_DIR로
            # 가리킨다. pyopenjtalk가 import 시점에 이 env를 읽으므로 inference_webui import
            # 전에 설정해야 한다. (한국어 합성은 mecab을 쓰지 않아 무관)
            jtalk_dic = os.path.join(repo, "open_jtalk_dic_utf_8-1.11")
            if os.path.isdir(jtalk_dic):
                os.environ.setdefault("OPEN_JTALK_DICT_DIR", jtalk_dic)

            self._resolve_base_ckpts()
        if self._gpt_ckpt:
            os.environ.setdefault("gpt_path", self._gpt_ckpt)
        if self._sovits_ckpt:
            os.environ.setdefault("sovits_path", self._sovits_ckpt)

        # tqdm이 asyncio.to_thread 안에서 \r을 터미널에 쓰려다 WinError 1이 난다.
        # 데몬에서 progress bar는 불필요하므로 전역으로 끈다.
        os.environ["TQDM_DISABLE"] = "1"

        # 최신 huggingface_hub(0.36+)가 제거한 옛 심볼들을 복원하는 shim. GPT-SoVITS의
        # 핀(transformers==4.50.0, gradio<5)이 옛 hf_hub API를 기대하는데, 베이스 모델
        # 다운로드 등으로 hf_hub가 최신이라 import가 깨진다. inference_webui가 이들을
        # module level에서 import하므로 import 전에 채워둔다.
        import huggingface_hub as _hf_hub

        if not hasattr(_hf_hub, "HfFolder"):  # gradio/oauth.py가 사용
            class _HfFolder:
                @staticmethod
                def get_token() -> str | None:
                    return _hf_hub.get_token()
                @staticmethod
                def save_token(_token: str) -> None:
                    pass
            _hf_hub.HfFolder = _HfFolder  # type: ignore[attr-defined]

        if not hasattr(_hf_hub, "is_offline_mode"):  # transformers 4.50 hub.py가 사용
            _hf_hub.is_offline_mode = (  # type: ignore[attr-defined]
                lambda: bool(_hf_hub.constants.HF_HUB_OFFLINE)
            )

        # text/chinese.py·tone_sandhi.py가 jieba_fast(C확장)를 하드 import하는데
        # Windows wheel이 없다. 순수 파이썬 jieba가 API 호환이라 alias로 대체한다
        # (중국어 전처리 전용 — JA/KO 경로엔 영향 없음).
        try:
            import jieba_fast  # noqa: F401
        except ImportError:
            import jieba
            import jieba.posseg

            sys.modules["jieba_fast"] = jieba
            sys.modules["jieba_fast.posseg"] = jieba.posseg

        # torchcodec(GLIBCXX/ffmpeg 의존)를 피하려고 torchaudio.load를 soundfile로 교체.
        # OGG/Vorbis도 libsndfile이 직접 읽는다. 반드시 inference_webui import 전에 적용.
        import soundfile as sf
        import torch as _torch
        import torchaudio as _ta

        def _sf_load(filename: Any, *_a: Any, **_k: Any) -> Any:
            data, sr0 = sf.read(str(filename), dtype="float32", always_2d=True)
            return _torch.from_numpy(data.T), sr0

        _ta.load = _sf_load

        try:
            from GPT_SoVITS.inference_webui import (  # type: ignore[import]
                change_gpt_weights,
                change_sovits_weights,
                get_tts_wav,
                i18n,
            )
        except ImportError as exc:
            raise ImportError(
                "GPT-SoVITS를 찾을 수 없습니다.\n"
                "  git clone https://github.com/RVC-Boss/GPT-SoVITS C:\\gptsovits\n"
                "  create_mouth('gptsovits', repo_path=r'C:\\gptsovits', ...)"
            ) from exc

        self._i18n = i18n
        self._change_gpt_weights = change_gpt_weights
        self._change_sovits_weights = change_sovits_weights

        # i18n으로 언어 인자 래핑 (webui 내부 dict_language 키와 일치시켜야 함).
        self._apply_langs()
        self._how_to_cut = i18n("不切")
        self._load_weights()

        self._tts_fn = get_tts_wav
        logger.info("GPT-SoVITS 준비 완료 (device=%s).", self._device)
        return self._tts_fn

    def _resolve_base_ckpts(self) -> None:
        """빈 ckpt를 base(zero-shot) 경로로 채운다 — 부팅·핫스왑 공용. repo_path 없으면 무동작.

        ckpt 미지정 = base(zero-shot) 의도인데, inference_webui는 env 미설정 시
        weight.json(마지막으로 쓴 가중치 기억)으로 폴백한다 — 이전 실행이 fine-tune을
        남겼으면 base가 아니라 그 음색이 돼 카드 의도가 깨진다. base 경로를 명시해
        "생략 = base"를 weight.json 상태와 무관한 결정론으로 만든다.
        base는 v2ProPlus — v2 base는 EOS 실패(폭주·조기 종료)가 잦아 기각
        (문장 단위 10/10 정상 vs v2 폭주 빈발, 2026.07.15 실측). v2ProPlus는
        화자 임베딩(sv) 모델이 추가로 필요하다(inference_webui가 자동 로드).
        """
        if not self._repo_path or (self._gpt_ckpt and self._sovits_ckpt):
            return
        # _repo_path는 _ensure_engine이 chdir 전에 절대경로로 고정해 둔다.
        pre = os.path.join(self._repo_path, "GPT_SoVITS", "pretrained_models")
        base_gpt = os.path.join(pre, "s1v3.ckpt")
        base_sovits = os.path.join(pre, "v2Pro", "s2Gv2ProPlus.pth")
        base_sv = os.path.join(pre, "sv", "pretrained_eres2netv2w24s4ep4.ckpt")
        missing = [p for p in (base_gpt, base_sovits, base_sv) if not os.path.isfile(p)]
        if missing:
            raise FileNotFoundError(
                "base(zero-shot) 가중치가 없습니다: "
                + ", ".join(os.path.basename(p) for p in missing)
                + "\n다운로드:\n"
                "  huggingface_hub.snapshot_download('lj1995/GPT-SoVITS',\n"
                "    allow_patterns=['s1v3.ckpt', 'v2Pro/s2Gv2ProPlus.pth', 'sv/*'],\n"
                f"    local_dir=r'{pre}')"
            )
        if not self._gpt_ckpt:
            self._gpt_ckpt = base_gpt
        if not self._sovits_ckpt:
            self._sovits_ckpt = base_sovits

    def _apply_langs(self) -> None:
        """언어 인자 래핑 — 엔진 로드 전(테스트 주입)엔 i18n이 없어 소스 문자열을 그대로 쓴다."""
        wrap = self._i18n or (lambda s: s)
        self._prompt_lang = wrap(_GPTSOVITS_LANG[self._ref_lang])
        self._text_lang = wrap(_GPTSOVITS_LANG[self._gen_lang])

    def _load_weights(self) -> None:
        """현재 _gpt_ckpt·_sovits_ckpt를 엔진에 올린다 (부팅·핫스왑 공용).

        SoVITS는 제너레이터라 소진해야 적용된다. prompt/text_language를 넘겨야 마지막
        yield가 미설정 prompt_text_update를 참조해 죽지 않는다.
        """
        if self._change_sovits_weights is None or self._change_gpt_weights is None:
            raise RuntimeError("엔진 미로드 — warmup() 후에 가중치를 올릴 수 있습니다")
        if self._sovits_ckpt:
            for _ in self._change_sovits_weights(
                self._sovits_ckpt,
                prompt_language=self._prompt_lang,
                text_language=self._text_lang,
            ):
                pass
        if self._gpt_ckpt:
            self._change_gpt_weights(self._gpt_ckpt)

    # --- 계약 ---------------------------------------------------------

    async def speak_stream(
        self, tokens: AsyncIterator[str], voice: VoiceProfile
    ) -> None:
        tts_fn = await asyncio.to_thread(self._ensure_engine)
        ref_path = voice.vendor_voice_id
        # 톤(레퍼런스) 교체 지원: 전사는 wav와 한 쌍이므로 VoiceProfile이 우선,
        # 빈값이면 생성자 ref_text(하위호환 — 카드에 voice 섹션 없는 구 config).
        ref_text = voice.ref_text or self._ref_text

        self._stopped = False
        self._playing = True
        audio_q: asyncio.Queue[Any] = asyncio.Queue()
        _DONE = object()
        # 속도 계측 기준점 — run_turn의 스트림 시작(≈ STT 완료 직후)과 사실상 같다
        t0 = time.perf_counter()
        first_sentence_done = False
        first_synth_done = False

        def _synth(text: str) -> Any:
            return _synth_one(
                tts_fn, ref_path, ref_text, text,
                self._prompt_lang, self._text_lang, self._how_to_cut,
            )

        async def _synth_and_queue(text: str) -> None:
            nonlocal first_sentence_done, first_synth_done
            if not first_sentence_done:
                first_sentence_done = True
                logger.debug(
                    "첫 문장 확정 +%.0fms (%d자)",
                    (time.perf_counter() - t0) * 1000, len(text),
                )
            wav = await asyncio.to_thread(_synth, text)
            if wav is not None:
                if not first_synth_done:
                    first_synth_done = True
                    logger.debug(
                        "첫 문장 합성 완료 +%.0fms (%.2fs 오디오)",
                        (time.perf_counter() - t0) * 1000, len(wav) / _OUTPUT_SR,
                    )
                await audio_q.put(wav)

        async def _synth_worker() -> None:
            buf = ""
            async for tok in tokens:
                if self._stopped:
                    break
                buf += tok
                while True:
                    m = SENTENCE_END.match(buf)
                    if not m:
                        break
                    chunk_text = m.group(0).strip()  # 문장 사이 공백 제거(supertonic과 일관)
                    buf = buf[m.end():]
                    if chunk_text:
                        await _synth_and_queue(chunk_text)
                    if self._stopped:
                        break
            if buf.strip() and not self._stopped:
                await _synth_and_queue(buf.strip())
            await audio_q.put(_DONE)

        async def _play_loop() -> None:
            first_play = True
            while True:
                item = await audio_q.get()
                if item is _DONE:
                    break
                if self._stopped:
                    continue
                if first_play:
                    first_play = False
                    logger.info(
                        "첫 오디오 재생 +%.0fms (TTFA)", (time.perf_counter() - t0) * 1000
                    )
                await asyncio.to_thread(self._play, item)

        try:
            await asyncio.gather(
                asyncio.create_task(_synth_worker()),
                asyncio.create_task(_play_loop()),
            )
        finally:
            self._playing = False

    def warmup(self) -> None:
        """GPT-SoVITS 가중치를 미리 로드한다 (첫 발화 ~71s 지연 제거)."""
        self._ensure_engine()

    def set_weights(
        self, gpt_ckpt: str, sovits_ckpt: str, *, ref_lang: str = "", gen_lang: str = ""
    ) -> None:
        """음색 가중치를 런타임 교체한다 — 페르소나 번들(가중치+언어)의 원자 교체.

        webui의 change_*_weights를 재호출할 뿐이다(엔진은 그대로 — 엔진 핫스왑 아님).
        블로킹(torch.load 수 초) — 호출부가 스레드로 넘긴다. 빈 ckpt는 base(zero-shot)
        의도이므로 _ensure_engine과 같은 규칙으로 base 경로를 명시 해석하고, 빈 언어는
        현재 값을 유지한다.
        """
        for name, lang in (("ref_lang", ref_lang), ("gen_lang", gen_lang)):
            if lang and lang not in _GPTSOVITS_LANG:
                raise ValueError(f"지원 언어: {sorted(_GPTSOVITS_LANG)} ({name}={lang})")
        self._ensure_engine()  # 미웜업 상태에서의 교체도 성립시킨다(idempotent)
        self._gpt_ckpt = os.path.abspath(gpt_ckpt) if gpt_ckpt else ""
        self._sovits_ckpt = os.path.abspath(sovits_ckpt) if sovits_ckpt else ""
        self._resolve_base_ckpts()
        self._ref_lang = ref_lang or self._ref_lang
        self._gen_lang = gen_lang or self._gen_lang
        self._apply_langs()
        self._load_weights()
        logger.info(
            "음색 가중치 교체 완료 — gpt=%s, sovits=%s (ref=%s, gen=%s)",
            os.path.basename(self._gpt_ckpt), os.path.basename(self._sovits_ckpt),
            self._ref_lang, self._gen_lang,
        )

    def _play(self, wav: Any) -> None:
        import sounddevice as sd

        sd.play(wav, _OUTPUT_SR)
        sd.wait()  # stop()이 sd.stop()을 부르면 즉시 반환된다

    def stop(self) -> None:
        self._stopped = True
        self._playing = False
        try:
            import sounddevice as sd

            sd.stop()  # 재생 중인 sd.wait()를 즉시 풀어 barge-in
        except Exception:  # 장치 문제로 중단 실패해도 플래그는 이미 내려둠
            logger.debug("sd.stop() 실패(무시)", exc_info=True)

    def is_playing(self) -> bool:
        return self._playing


# 폭주 판정 임계 — 정상 발화는 |x|>0.01 비율이 ~0.4-0.7, 폭주(EOS 실패로 max
# 길이까지 무음 채움)는 ~0.01. base(zero-shot)에서 회차당 대략 반반로 실측(2026.07.15,
# 레퍼런스 종류 무관). fine-tune 가중치에선 관측된 적 없어 사실상 zero-shot 전용 안전망.
_ACTIVE_RATIO_MIN = 0.1
_SYNTH_ATTEMPTS = 3


def _synth_one(
    tts_fn: Any,
    ref_path: str,
    ref_text: str,
    text: str,
    prompt_lang: Any,
    text_lang: Any,
    how_to_cut: Any,
) -> Any:
    """한 청크를 합성해 float32 ndarray로 반환. get_tts_wav는 (sr, int16) 튜플을 yield.

    s1(GPT) 단계가 EOS를 못 뱉으면 max 길이까지 무음으로 채운 출력이 나온다(확률적,
    base zero-shot에서 빈발) — active 비율로 감지해 재시도한다.
    """
    import numpy as np

    for attempt in range(1, _SYNTH_ATTEMPTS + 1):
        try:
            raw = list(
                tts_fn(
                    ref_wav_path=ref_path,
                    prompt_text=ref_text,
                    prompt_language=prompt_lang,
                    text=text,
                    text_language=text_lang,
                    how_to_cut=how_to_cut,  # 청킹은 우리가 문장 경계로 이미 함
                )
            )
        except Exception:
            logger.exception("GPT-SoVITS 합성 오류: %r", text[:40])
            return None
        if not raw:
            return None
        # yield 형식: (sample_rate, int16 ndarray) 튜플들.
        chunks = [audio for _sr, audio in raw]
        wav_i16 = np.concatenate(chunks)
        wav = wav_i16.astype(np.float32) / 32768.0
        active = float(np.mean(np.abs(wav) > 0.01))
        if active >= _ACTIVE_RATIO_MIN:
            return wav
        logger.warning(
            "합성 폭주 감지(무음 %d%%, %.1fs) — 재시도 %d/%d: %r",
            round((1 - active) * 100), len(wav) / _OUTPUT_SR,
            attempt, _SYNTH_ATTEMPTS, text[:40],
        )
    logger.error("합성 %d회 모두 폭주 — 청크 포기: %r", _SYNTH_ATTEMPTS, text[:40])
    return None
