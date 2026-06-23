"""마이크 실시간 입력 → 발화 단위 방출 (arch 4.1 입력 깔때기의 I/O 글루).

sounddevice(PortAudio)로 16-bit PCM 프레임을 콜백으로 받아 Endpointer에 흘리고,
발화가 끝날 때마다 Utterance를 yield한다. 이 파일만 하드웨어 I/O를 안다 — 판정 로직은
endpointer.py(순수·테스트 가능)에 있다.

sounddevice는 .venv-voice에만 설치되므로 지연 임포트한다 — 기본 .venv에서도 모듈
임포트·단위 테스트가 깨지지 않게(STT 어댑터와 동일 규약).
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator

from navi.models import AudioChunk
from navi.ear.endpointer import Endpointer, Utterance
from navi.ear.vad import EnergyVad, Vad

log = logging.getLogger(__name__)


class MicListener:
    """마이크를 열어 발화 1건씩 비동기로 내보낸다.

    sample_rate·frame_ms는 STT(faster-whisper, 16kHz mono)와 맞춘다. 발화 경계 튜닝값
    (start_speech_ms·endpoint_silence_ms)은 Endpointer로 그대로 넘긴다(D12 영역).
    """

    def __init__(
        self,
        vad: Vad | None = None,
        *,
        sample_rate: int = 16000,
        frame_ms: int = 20,
        start_speech_ms: int = 200,
        endpoint_silence_ms: int = 800,
        preroll_ms: int = 200,
    ) -> None:
        self._sr = sample_rate
        self._frame_samples = int(sample_rate * frame_ms / 1000)
        self._endpointer = Endpointer(
            vad or EnergyVad(),
            frame_ms=frame_ms,
            start_speech_ms=start_speech_ms,
            endpoint_silence_ms=endpoint_silence_ms,
            preroll_ms=preroll_ms,
        )

    async def utterances(self) -> AsyncIterator[Utterance]:
        """마이크 스트림을 열고, 발화가 끝날 때마다 Utterance를 yield한다 (Ctrl+C까지 무한)."""
        import sounddevice as sd

        loop = asyncio.get_running_loop()
        queue: asyncio.Queue[bytes] = asyncio.Queue()

        def _on_frame(indata, _frames, _time, status) -> None:
            # PortAudio 콜백 스레드 — 이벤트 루프로 안전하게 프레임을 넘긴다.
            if status:
                log.debug("입력 스트림 상태: %s", status)
            loop.call_soon_threadsafe(queue.put_nowait, bytes(indata))

        stream = sd.RawInputStream(
            samplerate=self._sr,
            channels=1,
            dtype="int16",
            blocksize=self._frame_samples,
            callback=_on_frame,
        )
        log.info("마이크 열림 — %dHz, %d샘플/프레임", self._sr, self._frame_samples)
        with stream:
            while True:
                pcm = await queue.get()
                utt = self._endpointer.push(AudioChunk(pcm=pcm, sample_rate=self._sr))
                if utt is not None:
                    log.info("발화 감지 — %.0fms", utt.duration_ms)
                    yield utt
