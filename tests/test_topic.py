"""3층 주제 도출(arch 4.4) 검증 — 규칙 기반 최소 배선이라 "값을 반환한다"까지만.

좋은 주제 선택은 로그·Feed가 쌓인 뒤(진행 원칙 2) — 여기선 우선순위·비어있음만 본다.
"""

from navi.heartbeat.topic import pick_topic


def test_returns_hint_for_each_time_of_day():
    for tod in ("morning", "afternoon", "evening", "night"):
        hint = pick_topic(memory_snapshot=None, weather=None, time_of_day=tod, topic_feed=[])
        assert isinstance(hint, str) and hint


def test_feed_takes_priority_when_present():
    hint = pick_topic(
        memory_snapshot=None, weather=None, time_of_day="morning",
        topic_feed=["오늘 비 온대"],
    )
    assert hint == "오늘 비 온대"


def test_memory_adds_continuation_cue():
    plain = pick_topic(None, None, "afternoon", [])
    with_memory = pick_topic(["지난 대화"], None, "afternoon", [])
    assert len(with_memory) > len(plain)  # 최근 대화 있으면 "이어가라" 덧붙음


def test_unknown_time_of_day_still_returns_hint():
    assert pick_topic(None, None, "dawn", []) is not None
