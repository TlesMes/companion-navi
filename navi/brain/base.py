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

from navi.models import BrainResult, LlmRequest, Message


class BrainAuthError(RuntimeError):
    """키가 틀렸다(인증·권한 거부) — 사용자가 키를 다시 넣으면 해결된다."""


class BrainUnavailable(RuntimeError):
    """벤더에 닿지 못했다(네트워크·장애·쿼터) — 키 문제가 아니라 나중에 되는 종류다."""


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

    async def validate(self, model: str) -> None:
        """이 어댑터가 실제로 대답하는지 1회 확인 — 성공하면 조용히 반환.

        런타임 두뇌 교체가 "값만 바꿔 놓고 말을 시켜야 터지는" 실패를 하지 않게 하는
        관문이다(SwapRuntime.swap_brain). 실패 타이밍을 교체 시점으로 당긴다.

        기본 구현은 최소 요청 하나를 보내 **첫 토큰만** 받고 끊는다 — 어댑터마다 검증
        코드를 새로 짜지 않기 위해 기존 generate_stream 계약을 그대로 쓴다.

        실패는 BrainAuthError(키가 틀림) / BrainUnavailable(네트워크·장애) 둘로만
        올린다. 벤더 예외를 이 둘로 옮기는 건 각 어댑터의 몫이다 — 벤더 종속은 어댑터
        뒤에 가둔다(설계 원칙 1). 기본 구현은 벤더를 모르므로 전부 BrainUnavailable로
        본다(오진해도 "나중에 되는 종류"로 안내할 뿐 키를 지우지 않는다).
        """
        request = LlmRequest(
            system="ping", messages=[Message(role="user", text="ping")], model=model
        )
        stream = self.generate_stream(request)
        try:
            async for _ in stream:
                break
        except Exception as exc:
            raise BrainUnavailable(str(exc)) from exc
        finally:
            self.cancel()
            await stream.aclose()
