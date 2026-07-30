"""수집한 항목을 나비 입말 재료로 소화한다 (D13/feed.md 3.3).

"언제 말할까=규칙, 무엇을 말할까=모델" 원칙 안에 있다 — 수집 주기·중복방지·필터는
전부 결정론이고, LLM은 요약이라는 "무엇을 말할까"에만 쓴다. 저가 티어 1회성 호출.

collect.py가 아니라 별도 모듈인 이유: 프롬프트는 튜닝 대상 자산이고, feed.md PR2의
대화 콜백 추출 프롬프트가 여기 나란히 붙을 자리다. collect.py는 오케스트레이션만 한다.
"""

from __future__ import annotations

import logging

from navi.brain.base import BrainAdapter
from navi.feed.base import RawItem
from navi.models import LlmRequest, Message

log = logging.getLogger(__name__)

# 2인칭 요약("네가 응원하는 팀이…")을 주면 두뇌가 화자를 재해석한다 — 실측으로 확인된
# 결함이다(turn_assembly.md 4.1 ③). 인칭·말투는 발화 시점에 두뇌가 입히는 것이고,
# 여기서 만드는 건 재료(사실)일 뿐이다.
SUMMARY_SYSTEM = (
    "너는 뉴스 항목을 한두 문장으로 압축하는 요약기다. 규칙: "
    "①사실만, 중립 시점으로 쓴다. "
    "②청자를 지칭하지 않는다('너', '네가' 금지). "
    "③인사·감상·말투를 붙이지 않는다. 인칭과 말투는 나중에 다른 곳에서 입힌다."
)


async def drain(brain: BrainAdapter, request: LlmRequest) -> str:
    """스트림을 끝까지 소진하고 확정 전문을 돌려준다.

    BrainAdapter 계약상 last_result는 소진 후에만 유효하다(brain/base.py). 요약은
    스트리밍이 필요 없지만 어댑터에 1회성 호출 경로가 없어 이 관용구를 쓴다.
    """
    async for _ in brain.generate_stream(request):
        pass
    result = brain.last_result
    return result.full_text.strip() if result else ""


async def summarize_item(brain: BrainAdapter, model: str, item: RawItem) -> str:
    """항목 하나 → 나비가 꺼낼 재료 한 문장. 실패·빈 응답은 호출부가 스킵으로 처리."""
    request = LlmRequest(
        system=SUMMARY_SYSTEM,
        messages=[Message(role="user", text=f"{item.title}\n{item.summary}".strip())],
        model=model,
    )
    return await drain(brain, request)
