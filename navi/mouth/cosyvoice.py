"""CosyVoice2 실어댑터 — 두뇌 토큰 스트림을 로컬에서 합성·재생한다 (D3 후보).

CosyVoice2는 inference_zero_shot이 generator를 반환해 청크 단위 스트리밍이 가능하다.
F5-TTS보다 TTFA가 낮을 수 있으나, 모델이 크고 한국어 품질은 Stage 1 청취 비교로 확인한다.

문장청크 스트리밍 전략 (Supertonic·F5-TTS와 동일):
  토큰 → 문장 경계 → 청크별 inference_zero_shot() → 첫 문장 즉시 재생

설치 (WSL2 + ROCm):
  git clone https://github.com/FunAudioLLM/CosyVoice
  cd CosyVoice && pip install -r requirements.txt
  pip install sounddevice

레퍼런스 설정:
  vendor_voice_id = "/path/to/reference.wav"   ← 레퍼런스 WAV (16kHz 리샘플링 내부 처리)
  create_mouth("cosyvoice", ref_text="레퍼런스 내용")
"""

from __future__ import annotations

import asyncio
import logging
import re
from collections.abc import AsyncIterator
from typing import Any

from navi.models import VoiceProfile
from navi.mouth.base import MouthAdapter

logger = logging.getLogger(__name__)

_SENTENCE_END = re.compile(r".*?[.!?。…\n]+[""\')\]]*(?=\s|$)", re.DOTALL)


class CosyVoiceMouth(MouthAdapter):
    def __init__(
        self,
        *,
        ref_text: str = "",
        model_name: str = "CosyVoice2-0.5B",
        tts: Any = None,
    ) -> None:
        self._ref_text = ref_text
        self._model_name = model_name
        self._tts = tts
        self._prompt_audio: Any = None  # 레퍼런스 로드 캐시
        self._prompt_path: str = ""
        self._playing = False
        self._stopped = False

    def _ensure_engine(self) -> Any:
        if self._tts is None:
            from cosyvoice.cli.cosyvoice import CosyVoice2  # type: ignore[import]

            logger.info("CosyVoice2 로드 중...")
            self._tts = CosyVoice2(self._model_name)
            logger.info("CosyVoice2 준비 완료.")
        return self._tts

    def _load_prompt(self, ref_path: str) -> Any:
        """레퍼런스 WAV → 16kHz 모노 텐서. 경로가 같으면 캐시 반환."""
        if ref_path == self._prompt_path and self._prompt_audio is not None:
            return self._prompt_audio

        import torchaudio

        audio, sr = torchaudio.load(ref_path)
        if sr != 16_000:
            audio = torchaudio.functional.resample(audio, sr, 16_000)
        if audio.shape[0] > 1:
            audio = audio.mean(dim=0, keepdim=True)
        self._prompt_audio = audio
        self._prompt_path = ref_path
        return audio

    # --- 계약 ---------------------------------------------------------

    async def speak_stream(
        self, tokens: AsyncIterator[str], voice: VoiceProfile
    ) -> None:
        tts = await asyncio.to_thread(self._ensure_engine)
        prompt_audio = await asyncio.to_thread(self._load_prompt, voice.vendor_voice_id)
        sr = tts.sample_rate

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
                        _synth_one, tts, self._ref_text, chunk_text, prompt_audio
                    )
                    if wav is not None:
                        await audio_q.put(wav)
                    if self._stopped:
                        break
            if buf.strip() and not self._stopped:
                wav = await asyncio.to_thread(
                    _synth_one, tts, self._ref_text, buf.strip(), prompt_audio
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
                await asyncio.to_thread(sd.play, item, sr)
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


def _synth_one(tts: Any, ref_text: str, text: str, prompt_audio: Any) -> Any:
    import numpy as np

    try:
        chunks = [
            chunk["tts_speech"].numpy().flatten()
            for chunk in tts.inference_zero_shot(text, ref_text, prompt_audio)
        ]
        if not chunks:
            return None
        return np.concatenate(chunks).astype(np.float32)
    except Exception:
        logger.exception("CosyVoice2 합성 오류: %r", text[:40])
        return None
