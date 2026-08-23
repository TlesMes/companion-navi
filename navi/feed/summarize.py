"""수집한 항목을 나비 입말 재료로 소화한다 (D13/feed.md 3.3).

"언제 말할까=규칙, 무엇을 말할까=모델" 원칙 안에 있다 — 수집 주기·중복방지·필터는
전부 결정론이고, LLM은 요약이라는 "무엇을 말할까"에만 쓴다. 저가 티어 1회성 호출.

collect.py가 아니라 별도 모듈인 이유: 프롬프트는 튜닝 대상 자산이고, feed.md PR2의
대화 콜백 추출 프롬프트가 여기 나란히 붙을 자리다. collect.py는 오케스트레이션만 한다.
"""

from __future__ import annotations

import logging
import re

from navi.brain.base import BrainAdapter
from navi.feed.base import RawItem
from navi.models import LlmRequest, Message

log = logging.getLogger(__name__)

# 2인칭 요약("네가 응원하는 팀이…")을 주면 두뇌가 화자를 재해석한다 — 실측으로 확인된
# 결함이다(turn_assembly.md 4.1 ③). 인칭·말투는 발화 시점에 두뇌가 입히는 것이고,
# 여기서 만드는 건 재료(사실)일 뿐이다.
#
# ④ 언어를 못 박는 이유: 재료는 사용자에게도 페르소나에게도 안 보이는 내부 표현이고,
# 오직 두뇌만 읽는다. 페르소나에 맞출 수도 없다 — 수집은 배치라 어느 카드가 이 재료로
# 말할지 그 시점엔 모르고, 사용자는 런타임에 페르소나를 갈아끼운다. 지금까진 시스템
# 프롬프트가 한국어라 모델이 따라오는 부작용에 기대고 있었다(실측 2026.08.02: gemini·
# haiku 둘 다 영문 기사를 한국어로 요약). 규칙이 아니라 부작용이라 모델이 바뀌면 흔들린다.
SUMMARY_SYSTEM = (
    "너는 뉴스 항목을 한두 문장으로 압축하는 요약기다. 규칙: "
    "①사실만, 중립 시점으로 쓴다. "
    "②청자를 지칭하지 않는다('너', '네가' 금지). "
    "③인사·감상·말투를 붙이지 않는다. 인칭과 말투는 나중에 다른 곳에서 입힌다. "
    "④원문이 무슨 언어든 한국어로 쓴다. "
    "⑤제목·머리말·목록기호 없이 문장만 쓴다."
)

_FENCE = re.compile(r"^```[^\n]*\n(.*?)\n?```$", re.DOTALL)
_HEADING = re.compile(r"^\s{0,3}#{1,6}\s+.*$", re.MULTILINE)
_BULLET = re.compile(r"^\s{0,3}(?:[-*+]|\d+\.)\s+", re.MULTILINE)
_BOLD = re.compile(r"\*\*(.+?)\*\*", re.DOTALL)
_WHITESPACE = re.compile(r"\s+")


def clean_summary(text: str) -> str:
    """모델이 얹은 서식을 걷어낸다 — 재료는 그대로 발화 트리거가 되기 때문이다.

    프롬프트로 형식을 막는 건 확률을 낮출 뿐이라 결정론 후처리를 둔다(무드 태그를
    peel_mood가 흡수하는 것과 같은 자리). 실측 2026.08.02: haiku가 '# 요약'을 머리말로
    붙였고, 그대로 두면 나비가 "샵 요약"이라고 소리 내 읽는다.
    """
    text = text.strip()
    fence = _FENCE.match(text)
    if fence:
        text = fence.group(1)
    text = _HEADING.sub("", text)
    text = _BULLET.sub("", text)
    text = _BOLD.sub(r"\1", text)
    return _WHITESPACE.sub(" ", text).strip()


async def drain(brain: BrainAdapter, request: LlmRequest) -> str:
    """스트림을 끝까지 소진하고 확정 전문을 돌려준다.

    BrainAdapter 계약상 last_result는 소진 후에만 유효하다(brain/base.py). 요약은
    스트리밍이 필요 없지만 어댑터에 1회성 호출 경로가 없어 이 관용구를 쓴다.
    """
    async for _ in brain.generate_stream(request):
        pass
    result = brain.last_result
    return clean_summary(result.full_text) if result else ""


async def summarize_item(brain: BrainAdapter, model: str, item: RawItem) -> str:
    """항목 하나 → 나비가 꺼낼 재료 한 문장. 실패·빈 응답은 호출부가 스킵으로 처리."""
    request = LlmRequest(
        system=SUMMARY_SYSTEM,
        messages=[Message(role="user", text=f"{item.title}\n{item.summary}".strip())],
        model=model,
    )
    return await drain(brain, request)
