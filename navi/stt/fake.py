"""가짜 STT — 마이크·API·네트워크 없이 후단 파이프라인을 검증한다 (Echo 두뇌의 STT판).

미리 정한 대본(transcript)을 받아쓴 척한다. feed는 프레임 수만 센다 — 진짜 인식은 안 한다.
테스트는 next_transcript를 갈아끼워 "사용자가 무슨 말을 했는지"를 주입한다.
"""

from __future__ import annotations

from navi.models import AudioChunk, SttResult
from navi.stt.base import SttAdapter, SttSession


class FakeStt(SttAdapter):
    def __init__(self, transcript: str = "") -> None:
        self.next_transcript = transcript  # 다음 발화로 받아쓸 대본 (테스트가 주입)

    async def open_stream(self, lang: str = "ko") -> SttSession:
        return _FakeSttSession(self.next_transcript, lang)


class _FakeSttSession(SttSession):
    def __init__(self, transcript: str, lang: str) -> None:
        self._transcript = transcript
        self._lang = lang
        self.frames_fed = 0  # feed가 실제로 흘렀는지 테스트가 확인할 수 있게

    async def feed(self, chunk: AudioChunk) -> None:
        self.frames_fed += 1

    async def finalize(self) -> SttResult:
        return SttResult(text=self._transcript, confidence=1.0, lang=self._lang)
