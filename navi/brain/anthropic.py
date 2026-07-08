"""Anthropic(Claude) 어댑터 (스트리밍).

키 확보·실호출 검증 완료(2026.07.08, Haiku 4.5 실측: TTFT ~0.7~1.3s 안정).
config.yaml에서 vendor만 바꿔 교체(Phase 1 완료 기준 2: 벤더 교체에도 같은 말투).

프롬프트 캐싱: system 블록에 cache_control을 명시해 캐릭터 카드 입력비를 절감한다.
단 캐시 최소 프리픽스가 모델별로 다르다 — Haiku 4.5는 4096토큰, Sonnet/Opus는 1024토큰.
현재 캐릭터 카드는 ~1.6K토큰이라 Haiku에선 최소치 미달로 조용히 no-op(에러 없음, 매 턴 전액
청구). Sonnet/Opus로 올리면 최소치를 넘겨 자동으로 캐시가 켜진다 — 코드는 벤더 중립이라 그대로 둔다.
검증법: usage의 cache_creation/cache_read_input_tokens가 둘 다 0이면 캐시 미작동.
"""

from __future__ import annotations

from collections.abc import AsyncIterator

import anthropic

from navi.brain.base import BrainAdapter
from navi.models import BrainResult, LlmRequest, Message, Usage

_MAX_TOKENS = 1024


class AnthropicBrain(BrainAdapter):
    def __init__(self, api_key: str):
        super().__init__()
        self._client = anthropic.AsyncAnthropic(api_key=api_key)

    async def generate_stream(self, request: LlmRequest) -> AsyncIterator[str]:
        self.last_result = None
        self._cancelled = False

        system = [
            {
                "type": "text",
                "text": request.system,
                "cache_control": {"type": "ephemeral"},
            }
        ]
        messages = [
            {"role": m.role, "content": m.text}
            for m in _normalize(request.messages)
        ]

        async with self._client.messages.stream(
            model=request.model,
            max_tokens=_MAX_TOKENS,
            system=system,
            messages=messages,
        ) as stream:
            async for text in stream.text_stream:
                if self._cancelled:
                    break
                yield text
            final = await stream.get_final_message()

        self.last_result = BrainResult(
            full_text="".join(b.text for b in final.content if b.type == "text"),
            usage=Usage(
                input_tokens=final.usage.input_tokens,
                output_tokens=final.usage.output_tokens,
            ),
        )


def _normalize(messages: list[Message]) -> list[Message]:
    """Anthropic API 제약 충족: user로 시작, user/assistant 교대.

    단기기억 인출 결과는 연속 같은 role(예: 응답 없이 끝난 세션 뒤 새 질문)이나
    assistant 시작(능동 발화가 첫 턴)이 가능하므로 어댑터 경계에서 흡수한다.
    """
    merged: list[Message] = []
    for m in messages:
        if merged and merged[-1].role == m.role:
            merged[-1] = Message(role=m.role, text=f"{merged[-1].text}\n{m.text}")
        else:
            merged.append(m)
    if merged and merged[0].role == "assistant":
        merged.insert(0, Message(role="user", text="(대화 시작)"))
    return merged
