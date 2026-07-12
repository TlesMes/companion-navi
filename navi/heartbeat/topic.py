"""3층: 주제 도출 (arch 4.4). "무엇을 먼저 말할까"의 힌트를 만든다.

계약상 작은 모델/LLM 사용이 허용된 층이지만, 지금은 **규칙 기반 최소 배선**이다
(진행 원칙 2 — 좋은 주제 선택은 로그·Feed가 쌓인 뒤). 반환값 topic_hint는
Conductor.build_request(trigger_text=...)로 그대로 들어가는 트리거 문자열 —
사용자 발화 자리에 놓여 LLM에게 "이런 톤으로 먼저 말 걸어"를 지시한다.

weather는 출처 미정(D번호 없음)이라 이번 범위에선 None만 받는다. topic_feed는
Feed(arch 4.10, Phase 3 후반)의 산물로 지금은 항상 빈 리스트 — 들어오면 그걸
우선한다는 배선만 걸어 둔다.
"""

from __future__ import annotations

from typing import Any

# 시간대 → 먼저 말 걸 때의 기본 톤 지시. 대충값 — 좋은 문구는 후속(로그 기반).
_HINTS = {
    "morning": "아침 인사를 건네고 오늘 하루를 가볍게 물어봐.",
    "afternoon": "지금 뭐 하고 있는지 가볍게 안부를 물어봐.",
    "evening": "저녁 무렵, 하루가 어땠는지 물어봐.",
    "night": "늦은 시간이니 짧고 나직하게 안부를 건네.",
}


def pick_topic(
    memory_snapshot: list[Any] | None,
    weather: Any | None,
    time_of_day: str,
    topic_feed: list[str] | None,
) -> str | None:
    """arch 4.4 3층 계약. topic_hint(트리거 문자열) 또는 None(걸 게 없음).

    우선순위: Feed 후보(있으면) > 시간대 기본 힌트. 최근 대화가 있으면 "이어가라"를
    덧붙여 맥락을 준다 — 실제 사실 인출은 Conductor가 기억에서 하므로 여기선 지시만.
    """
    if topic_feed:
        return topic_feed[0]  # Feed는 Phase 3 후반 — 지금은 빈 리스트라 여기 안 옴
    hint = _HINTS.get(time_of_day, "가볍게 안부를 물어봐.")
    if memory_snapshot:
        hint += " 지난 대화를 자연스럽게 이어가도 좋아."
    return hint
