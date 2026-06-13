"""가짜 Mouth — 스피커·TTS API 없이 출력 파이프라인을 검증한다.

합성·재생 대신 토큰을 모아 텍스트로 보관한다. stop()으로 스트림 도중 끊어 barge-in을
흉내 낼 수 있다 — 토큰 경계에서 멈춘다 (EchoBrain.cancel과 같은 협조적 중단).
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator

from navi.models import VoiceProfile
from navi.mouth.base import MouthAdapter


class FakeMouth(MouthAdapter):
    def __init__(self) -> None:
        self.spoken: list[str] = []  # speak_stream 호출별 합성된 전문
        self.last_voice: VoiceProfile | None = None
        self._playing = False
        self._stopped = False

    async def speak_stream(
        self, tokens: AsyncIterator[str], voice: VoiceProfile
    ) -> None:
        self.last_voice = voice
        self._playing = True
        self._stopped = False
        buf: list[str] = []
        async for token in tokens:
            if self._stopped:  # barge-in — 다음 토큰 경계에서 중단
                break
            buf.append(token)
            await asyncio.sleep(0)  # 재생 시간을 흉내 내며 협조적으로 양보
        self.spoken.append("".join(buf))
        self._playing = False

    def stop(self) -> None:
        self._stopped = True
        self._playing = False

    def is_playing(self) -> bool:
        return self._playing
