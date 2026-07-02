"""F5-TTS 실어댑터 — 두뇌 토큰 스트림을 로컬에서 합성·재생한다 (D3 후보).

F5-TTS는 배치 엔진이다(전체 텍스트 → wav 한 방). Supertonic과 동일한
문장청크 스트리밍 전략으로 TTFA ~1초를 맞춘다:
  토큰 → 문장 경계 → 청크별 infer() → 첫 문장 즉시 재생
  합성(N+1)이 재생(N)과 겹쳐 끊김 최소화.

설치 (WSL2 + ROCm):
  pip install f5-tts sounddevice soundfile

레퍼런스 설정 (VoiceProfile 필드):
  vendor_voice_id = "/path/to/reference.wav"   ← 레퍼런스 WAV 절대경로
  speed           = 1.0                         ← 말 속도 (현재 미사용)

레퍼런스 전사 텍스트는 생성자 ref_text 인자로 주입:
  create_mouth("f5tts", ref_text="레퍼런스 내용")
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator
from typing import TYPE_CHECKING, Any

from navi.models import VoiceProfile
from navi.mouth.base import MouthAdapter
from navi.mouth.sentence import SENTENCE_END

if TYPE_CHECKING:
    import numpy as np

logger = logging.getLogger(__name__)


class F5TTSMouth(MouthAdapter):
    def __init__(
        self,
        *,
        ref_text: str = "",
        device: str = "cuda",
        tts: Any = None,
    ) -> None:
        self._ref_text = ref_text
        self._device = device
        self._tts = tts  # 무거운 엔진 — 첫 발화 시 지연 로드(또는 테스트 주입)
        self._playing = False
        self._stopped = False

    def _ensure_engine(self) -> Any:
        if self._tts is None:
            from f5_tts.api import F5TTS  # type: ignore[import]

            logger.info("F5-TTS 엔진 로드 중 (최초 실행 시 모델 다운로드 가능)...")
            self._tts = F5TTS(device=self._device)
            logger.info("F5-TTS 준비 완료.")
        return self._tts

    # --- 계약 ---------------------------------------------------------

    async def speak_stream(
        self, tokens: AsyncIterator[str], voice: VoiceProfile
    ) -> None:
        tts = await asyncio.to_thread(self._ensure_engine)
        ref_path = voice.vendor_voice_id  # WAV 절대경로

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
                    m = SENTENCE_END.match(buf)
                    if not m:
                        break
                    chunk_text = m.group(0)
                    buf = buf[m.end():]
                    wav, sr = await asyncio.to_thread(
                        _synth_one, tts, ref_path, self._ref_text, chunk_text
                    )
                    if wav is not None:
                        await audio_q.put((wav, sr))
                    if self._stopped:
                        break
            # 꼬리 처리
            if buf.strip() and not self._stopped:
                wav, sr = await asyncio.to_thread(
                    _synth_one, tts, ref_path, self._ref_text, buf.strip()
                )
                if wav is not None:
                    await audio_q.put((wav, sr))
            await audio_q.put(_DONE)

        async def _play_loop() -> None:
            import sounddevice as sd

            while True:
                item = await audio_q.get()
                if item is _DONE:
                    break
                wav, sr = item
                if self._stopped:
                    continue
                await asyncio.to_thread(sd.play, wav, sr)
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


def _synth_one(
    tts: Any, ref_path: str, ref_text: str, text: str
) -> tuple[Any, int] | tuple[None, None]:
    try:
        import numpy as np

        wav, sr, _ = tts.infer(
            ref_file=ref_path,
            ref_text=ref_text,
            gen_text=text,
            remove_silence=True,
        )
        return np.array(wav, dtype=np.float32), int(sr)
    except Exception:
        logger.exception("F5-TTS 합성 오류: %r", text[:40])
        return None, None
