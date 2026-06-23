"""발화 경계 판정 — VAD 프레임 시퀀스를 받아 발화 1건을 만든다 (arch 4.2 턴테이킹 전방경로).

마이크 I/O와 분리한 순수 상태머신이라 단위 테스트가 가능하다(가짜 프레임을 push). 규칙:
- 침묵 중 음성이 start_speech_ms 이상 연속되면 "발화 시작" 확정 (기침·딸깍 단발 무시).
- 발화 중 침묵이 endpoint_silence_ms 이상 지속되면 "발화 종료" 확정 → Utterance 방출.
- 시작 직전 preroll_ms 만큼은 미리 버퍼링해 onset이 잘리지 않게 한다.

barge-in(재생 중 사용자 발화) 판정은 이 모듈 밖 — Mouth 재생 상태가 필요해 다음 PR에서 붙인다.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from math import ceil

from navi.models import AudioChunk
from navi.ear.vad import Vad


@dataclass(frozen=True)
class Utterance:
    """발화 1건 — 시작부터 종료까지 누적된 PCM 프레임."""

    chunks: list[AudioChunk] = field(default_factory=list)

    @property
    def pcm(self) -> bytes:
        return b"".join(c.pcm for c in self.chunks)

    @property
    def sample_rate(self) -> int:
        return self.chunks[0].sample_rate if self.chunks else 16000

    @property
    def duration_ms(self) -> float:
        # 16-bit mono 가정 — 바이트수 / (2 * sr) * 1000
        return len(self.pcm) / (2 * self.sample_rate) * 1000


class Endpointer:
    def __init__(
        self,
        vad: Vad,
        *,
        frame_ms: int = 20,
        start_speech_ms: int = 200,
        endpoint_silence_ms: int = 800,
        preroll_ms: int = 200,
    ) -> None:
        self._vad = vad
        self._start_frames = max(1, ceil(start_speech_ms / frame_ms))
        self._endpoint_frames = max(1, ceil(endpoint_silence_ms / frame_ms))
        self._preroll: deque[AudioChunk] = deque(maxlen=max(1, ceil(preroll_ms / frame_ms)))
        self._reset()

    def _reset(self) -> None:
        self._speaking = False
        self._buf: list[AudioChunk] = []
        self._speech_run = 0  # 시작 전: 연속 음성 프레임 수
        self._silence_run = 0  # 발화 중: 연속 침묵 프레임 수
        self._preroll.clear()

    def push(self, chunk: AudioChunk) -> Utterance | None:
        """프레임 1개를 밀어넣는다. 발화가 종료되면 그 Utterance를, 아니면 None을 반환."""
        speech = self._vad.is_speech(chunk)
        if not self._speaking:
            self._preroll.append(chunk)
            if speech:
                self._speech_run += 1
                if self._speech_run >= self._start_frames:
                    self._speaking = True
                    self._buf = list(self._preroll)  # preroll에 onset·시작 프레임이 모여있다
                    self._preroll.clear()
                    self._silence_run = 0
            else:
                self._speech_run = 0
            return None

        self._buf.append(chunk)
        if speech:
            self._silence_run = 0
        else:
            self._silence_run += 1
            if self._silence_run >= self._endpoint_frames:
                utt = Utterance(chunks=self._buf)
                self._reset()
                return utt
        return None
