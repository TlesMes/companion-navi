"""GPT-SoVITS V2 실어댑터 — 두뇌 토큰 스트림을 로컬에서 합성·재생한다 (D3 후보).

GPT-SoVITS는 Web UI 중심 설계라 라이브러리로 직접 쓰려면 repo가 PYTHONPATH에 있어야 한다.
음색 복제율이 3개 후보 중 최상으로 평가받지만(특히 한·일 교차언어), 잡음 아티팩트 보고가
있으므로 Stage 1 청취 비교에서 이 항목을 집중 확인한다.

설치 (WSL2 + ROCm):
  git clone https://github.com/RVC-Boss/GPT-SoVITS /opt/gptsovits
  cd /opt/gptsovits && pip install -r requirements.txt
  pip install sounddevice

  # 환경변수 (.bashrc)
  export PYTHONPATH=$PYTHONPATH:/opt/gptsovits

레퍼런스 설정:
  vendor_voice_id = "/path/to/reference.wav"
  create_mouth("gptsovits", ref_text="레퍼런스 내용", repo_path="/opt/gptsovits")
"""

from __future__ import annotations

import asyncio
import logging
import re
import sys
from collections.abc import AsyncIterator
from typing import Any

from navi.models import VoiceProfile
from navi.mouth.base import MouthAdapter

logger = logging.getLogger(__name__)

_SENTENCE_END = re.compile(r".*?[.!?。…\n]+[""\')\]]*(?=\s|$)", re.DOTALL)
_OUTPUT_SR = 32_000  # GPT-SoVITS 기본 출력 샘플레이트


class GPTSoVITSMouth(MouthAdapter):
    def __init__(
        self,
        *,
        ref_text: str = "",
        repo_path: str | None = None,
        gpt_ckpt: str | None = None,
        tts_fn: Any = None,  # 테스트 주입용
    ) -> None:
        self._ref_text = ref_text
        self._repo_path = repo_path
        self._gpt_ckpt = gpt_ckpt
        self._tts_fn = tts_fn
        self._playing = False
        self._stopped = False

    def _ensure_engine(self) -> Any:
        if self._tts_fn is None:
            if self._repo_path:
                sys.path.insert(0, self._repo_path)
            try:
                from GPT_SoVITS.inference_webui import (  # type: ignore[import]
                    change_gpt_weights,
                    get_tts_wav,
                )
            except ImportError as exc:
                raise ImportError(
                    "GPT-SoVITS를 찾을 수 없습니다.\n"
                    "  git clone https://github.com/RVC-Boss/GPT-SoVITS /opt/gptsovits\n"
                    "  create_mouth('gptsovits', repo_path='/opt/gptsovits') 또는 PYTHONPATH 설정"
                ) from exc

            if self._gpt_ckpt:
                change_gpt_weights(self._gpt_ckpt)
            self._tts_fn = get_tts_wav
            logger.info("GPT-SoVITS 준비 완료.")
        return self._tts_fn

    # --- 계약 ---------------------------------------------------------

    async def speak_stream(
        self, tokens: AsyncIterator[str], voice: VoiceProfile
    ) -> None:
        get_tts_wav = await asyncio.to_thread(self._ensure_engine)
        ref_path = voice.vendor_voice_id

        self._stopped = False
        self._playing = True
        audio_q: asyncio.Queue[Any] = asyncio.Queue()
        _DONE = object()

        async def _synth_worker() -> None:
            buf = ""
            async for tok in tokens:
                if self._stopped:
                    break
                buf += tok
                while True:
                    m = _SENTENCE_END.match(buf)
                    if not m:
                        break
                    chunk_text = m.group(0)
                    buf = buf[m.end():]
                    wav = await asyncio.to_thread(
                        _synth_one, get_tts_wav, ref_path, self._ref_text, chunk_text
                    )
                    if wav is not None:
                        await audio_q.put(wav)
                    if self._stopped:
                        break
            if buf.strip() and not self._stopped:
                wav = await asyncio.to_thread(
                    _synth_one, get_tts_wav, ref_path, self._ref_text, buf.strip()
                )
                if wav is not None:
                    await audio_q.put(wav)
            await audio_q.put(_DONE)

        async def _play_loop() -> None:
            import sounddevice as sd

            while True:
                item = await audio_q.get()
                if item is _DONE:
                    break
                if self._stopped:
                    continue
                await asyncio.to_thread(sd.play, item, _OUTPUT_SR)
                await asyncio.to_thread(sd.wait)

        try:
            await asyncio.gather(
                asyncio.create_task(_synth_worker()),
                asyncio.create_task(_play_loop()),
            )
        finally:
            self._playing = False

    def stop(self) -> None:
        import sounddevice as sd

        self._stopped = True
        self._playing = False
        sd.stop()

    def is_playing(self) -> bool:
        return self._playing


def _synth_one(get_tts_wav: Any, ref_path: str, ref_text: str, text: str) -> Any:
    import numpy as np

    try:
        raw_chunks = list(
            get_tts_wav(
                ref_wav_path=ref_path,
                prompt_text=ref_text,
                prompt_language="ko",
                text=text,
                text_language="ko",
            )
        )
        if not raw_chunks:
            return None
        wav_i16 = np.concatenate([np.frombuffer(c, dtype=np.int16) for c in raw_chunks])
        return (wav_i16.astype(np.float32) / 32768.0)
    except Exception:
        logger.exception("GPT-SoVITS 합성 오류: %r", text[:40])
        return None
