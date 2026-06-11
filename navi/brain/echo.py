"""가짜 두뇌 — API 키·네트워크·비용 없이 파이프라인 전 구간을 검증한다."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator

from navi.brain.base import BrainAdapter
from navi.models import BrainResult, LlmRequest, Usage


class EchoBrain(BrainAdapter):
    async def generate_stream(self, request: LlmRequest) -> AsyncIterator[str]:
        self.last_result = None
        self._cancelled = False
        last_user = next(
            (m.text for m in reversed(request.messages) if m.role == "user"), ""
        )
        reply = f"(echo) {last_user}"
        emitted: list[str] = []
        # 어절 단위로 끊어 스트리밍을 흉내 낸다
        for i, word in enumerate(reply.split(" ")):
            if self._cancelled:
                break
            token = word if i == 0 else f" {word}"
            emitted.append(token)
            yield token
            await asyncio.sleep(0.02)
        self.last_result = BrainResult(full_text="".join(emitted), usage=Usage(0, 0))
