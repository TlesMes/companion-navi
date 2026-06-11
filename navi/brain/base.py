"""두뇌 어댑터 계약 (01 문서 4.7절).

LLM은 매 호출 새로 고용되는 무상태 배우다 — 인격·기억은 전부 데몬(요청 조립) 쪽에 있다.
어댑터는 LlmRequest(벤더 중립)를 자기 벤더 형식으로 변환하는 책임만 진다.

사용 규약:
- 어댑터 인스턴스는 동시 1요청 전제 (데몬은 단일 대화 스트림).
- generate_stream 소진 후 last_result에 전문·usage가 확정된다.
- cancel()은 barge-in용 — 텍스트 CLI(Phase 1)엔 호출처가 없지만 계약상 필수.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import AsyncIterator

from navi.models import BrainResult, LlmRequest


class BrainAdapter(ABC):
    def __init__(self) -> None:
        self.last_result: BrainResult | None = None
        self._cancelled = False

    @abstractmethod
    def generate_stream(self, request: LlmRequest) -> AsyncIterator[str]:
        """토큰(텍스트 조각)을 도착 즉시 흘려보내는 async 반복자."""

    def cancel(self) -> None:
        """생성 중단 요청 — 진행 중인 스트림이 다음 토큰 경계에서 멈춘다."""
        self._cancelled = True
