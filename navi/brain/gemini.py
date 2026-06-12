"""Gemini 어댑터 (google-genai SDK, 스트리밍).

Phase 1 기본 벤더 — D1(LLM 벤더)은 보류 상태이며, 보유 키가 Gemini라 검증용 기본값일 뿐이다.
프롬프트 캐싱: Gemini는 implicit caching이라 별도 지시 불필요 — 요청 앞부분(system)이
고정이면 자동 적용된다. Conductor가 캐싱 친화 순서로 조립하는 것으로 충분.
"""

from __future__ import annotations

from collections.abc import AsyncIterator

from google import genai
from google.genai import types

from navi.brain.base import BrainAdapter
from navi.models import BrainResult, LlmRequest, Usage


class GeminiBrain(BrainAdapter):
    def __init__(self, api_key: str):
        super().__init__()
        self._client = genai.Client(api_key=api_key)

    async def generate_stream(self, request: LlmRequest) -> AsyncIterator[str]:
        self.last_result = None
        self._cancelled = False

        contents = [
            types.Content(
                role="user" if m.role == "user" else "model",
                parts=[types.Part.from_text(text=m.text)],
            )
            for m in request.messages
        ]
        stream = await self._client.aio.models.generate_content_stream(
            model=request.model,
            contents=contents,
            config=types.GenerateContentConfig(
                system_instruction=request.system,
                # 잡담 티어는 즉답성 우선 — thinking이 첫 토큰을 수 초~수십 초 지연시킴
                # (실측 2026.06.13). D1 깊은 대화 티어 도입 시 티어별로 재검토.
                thinking_config=types.ThinkingConfig(thinking_budget=0),
            ),
        )

        parts: list[str] = []
        usage = Usage(0, 0)
        async for chunk in stream:
            if self._cancelled:
                break
            if chunk.text:
                parts.append(chunk.text)
                yield chunk.text
            if chunk.usage_metadata:  # 보통 마지막 청크에 실림
                usage = Usage(
                    input_tokens=chunk.usage_metadata.prompt_token_count or 0,
                    output_tokens=chunk.usage_metadata.candidates_token_count or 0,
                )
        self.last_result = BrainResult(full_text="".join(parts), usage=usage)
