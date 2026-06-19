"""faster-whisper STT 어댑터 — 발화 버퍼 누적 후 일괄 추론.

feed()로 PCM 프레임을 누적하고, finalize()에서 asyncio.to_thread로 추론한다.
Ear(VAD 스트리밍)가 붙기 전까지는 "전체 받은 뒤 추론"이라 계약 의미와 같다.
"""
from __future__ import annotations

import asyncio
import tempfile
import wave
from pathlib import Path

from navi.models import AudioChunk, SttResult
from navi.stt.base import SttAdapter, SttSession


class FasterWhisperStt(SttAdapter):
    """faster-whisper 로컬 추론 어댑터.

    모델은 첫 open_stream 호출 시 지연 로드한다 — 임포트만으로 GPU/메모리를 점유하지 않게.
    D2 결정 기준 실측치: large-v3-turbo / CPU int8 / RTF ≈ 0.61 (구어체 16초 발화).
    """

    def __init__(
        self,
        model_size: str = "large-v3-turbo",
        device: str = "cpu",
        compute_type: str = "int8",
    ) -> None:
        self._model_size = model_size
        self._device = device
        self._compute_type = compute_type
        self._model = None  # lazy

    def _ensure_model(self):
        if self._model is None:
            from faster_whisper import WhisperModel

            self._model = WhisperModel(
                self._model_size, device=self._device, compute_type=self._compute_type
            )
        return self._model

    async def open_stream(self, lang: str = "ko") -> SttSession:
        return _FwSession(self, lang)


class _FwSession(SttSession):
    def __init__(self, adapter: FasterWhisperStt, lang: str) -> None:
        self._adapter = adapter
        self._lang = lang
        self._chunks: list[bytes] = []
        self._sample_rate = 16000

    async def feed(self, chunk: AudioChunk) -> None:
        self._chunks.append(chunk.pcm)
        self._sample_rate = chunk.sample_rate

    async def finalize(self) -> SttResult:
        pcm = b"".join(self._chunks)
        if not pcm:
            return SttResult(text="", confidence=0.0, lang=self._lang)
        await asyncio.to_thread(self._adapter._ensure_model)
        text, detected = await asyncio.to_thread(self._transcribe, pcm)
        return SttResult(text=text, confidence=1.0, lang=detected)

    def _transcribe(self, pcm: bytes) -> tuple[str, str]:
        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
            tmp = Path(f.name)
        try:
            with wave.open(str(tmp), "wb") as wf:
                wf.setnchannels(1)
                wf.setsampwidth(2)  # 16-bit PCM
                wf.setframerate(self._sample_rate)
                wf.writeframes(pcm)
            model = self._adapter._model
            segments, info = model.transcribe(
                str(tmp), language=self._lang, vad_filter=True
            )
            text = "".join(s.text for s in segments).strip()
            detected = info.language or self._lang
            return text, detected
        finally:
            tmp.unlink(missing_ok=True)
