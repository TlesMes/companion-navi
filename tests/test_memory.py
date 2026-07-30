import json
import sqlite3

from navi.memory import MemoryStore
from navi.models import Usage


def test_append_and_recall_by_session(tmp_path):
    store = MemoryStore(tmp_path / "t.db")
    uid = store.ensure_user("친구")
    store.append_turn("s1", uid, "user", "안녕")
    store.append_turn("s1", uid, "assistant", "안!")
    store.append_turn("s2", uid, "user", "다른 세션 얘기")

    turns = store.recall_recent("s1", 10)
    assert [(t.role, t.text) for t in turns] == [("user", "안녕"), ("assistant", "안!")]


def test_recall_keeps_latest_n_in_order(tmp_path):
    store = MemoryStore(tmp_path / "t.db")
    uid = store.ensure_user("친구")
    for i in range(30):
        store.append_turn("s1", uid, "user", f"턴{i}")

    turns = store.recall_recent("s1", 5)
    assert [t.text for t in turns] == [f"턴{i}" for i in range(25, 30)]


def test_restart_recalls_yesterday_conversation(tmp_path):
    """Phase 1 완료 기준 1 — 껐다 켜도(새 연결·새 세션) 어제 대화를 기억한다."""
    db = tmp_path / "navi.db"
    store = MemoryStore(db)
    uid = store.ensure_user("친구")
    store.append_turn("어제세션", uid, "user", "내 강아지 이름은 콩이야")
    store.close()

    reopened = MemoryStore(db)  # 데몬 재시작 시뮬레이션
    assert reopened.ensure_user("친구") == uid  # 같은 사용자로 복원
    turns = reopened.recall_recent_for_user(uid, 20)
    assert any("콩이" in t.text for t in turns)


def test_recall_for_user_spans_sessions(tmp_path):
    store = MemoryStore(tmp_path / "t.db")
    uid = store.ensure_user("친구")
    store.append_turn("s1", uid, "user", "세션1")
    store.append_turn("s2", uid, "user", "세션2")

    turns = store.recall_recent_for_user(uid, 10)
    assert [t.text for t in turns] == ["세션1", "세션2"]


def test_intimacy_starts_at_zero_and_updates(tmp_path):
    store = MemoryStore(tmp_path / "t.db")
    uid = store.ensure_user("친구")
    assert store.get_intimacy(uid) == 0
    assert store.update_intimacy(uid, 3.5) == 3.5


def test_usage_log_written_as_json(tmp_path):
    db = tmp_path / "t.db"
    store = MemoryStore(db)
    store.log_usage("llm", Usage(input_tokens=120, output_tokens=45))

    row = sqlite3.connect(db).execute(
        "SELECT kind, tokens_or_units, est_cost FROM usage_log"
    ).fetchone()
    assert row[0] == "llm"
    assert json.loads(row[1]) == {"input": 120, "output": 45}
    assert row[2] is None  # 단가표 확정 전


def test_interaction_log_records_and_counts(tmp_path):
    """Phase 3 순서 4 — 능동 발화·반응이 남아 나중에 응답률/무시율을 계산할 수 있다."""
    db = tmp_path / "t.db"
    store = MemoryStore(db)
    store.log_interaction("initiated", "active", note="아침 인사")
    store.log_interaction("user_responded", "active")
    store.log_interaction("initiated", "active")

    rows = sqlite3.connect(db).execute(
        "SELECT event, mode_at_time, note FROM interaction_log ORDER BY log_id"
    ).fetchall()
    assert [r[0] for r in rows] == ["initiated", "user_responded", "initiated"]
    assert rows[0][1] == "active" and rows[0][2] == "아침 인사"

    # 응답률 산출의 재료 — 카운트가 event별로 집계된다
    assert store.count_interactions("initiated", "1970-01-01T00:00:00+00:00") == 2
    assert store.count_interactions("user_responded", "1970-01-01T00:00:00+00:00") == 1


def test_count_interactions_respects_since(tmp_path):
    """daily_cap 판정용 — since 이전 기록은 세지 않는다."""
    from datetime import UTC, datetime

    store = MemoryStore(tmp_path / "t.db")
    store.log_interaction("initiated", "active")
    future = datetime(2999, 1, 1, tzinfo=UTC).isoformat()
    assert store.count_interactions("initiated", future) == 0


def test_mode_state_roundtrip_and_upsert(tmp_path):
    """Stage 14 — 능동축 오버라이드가 재기동을 견딘다 (mode_state)."""
    db = tmp_path / "t.db"
    store = MemoryStore(db)
    uid = store.ensure_user("친구")
    assert store.get_mode_state(uid) is None  # 첫 기동 — 저장된 모드 없음

    store.set_mode_state(uid, "snooze", "2026-07-10T07:35:00")
    assert store.get_mode_state(uid) == ("snooze", "2026-07-10T07:35:00")

    store.set_mode_state(uid, "dnd", None)  # 전이마다 upsert — 행은 사용자당 1개
    store.close()

    reopened = MemoryStore(db)  # 데몬 재시작 시뮬레이션
    assert reopened.get_mode_state(uid) == ("dnd", None)


# ─── 관심사 피드 후보 (D13) ──────────────────────────────────

_T0 = "2026-07-30T00:00:00+00:00"
_T1 = "2026-07-30T01:00:00+00:00"
_T2 = "2026-07-30T02:00:00+00:00"


def _insert(store, *, dedup_key, summary="소식", fetched_at=_T1, expires_at=None,
            source="rss", topic_key="축구"):
    return store.insert_candidate(
        source=source,
        topic_key=topic_key,
        summary=summary,
        dedup_key=dedup_key,
        fetched_at=fetched_at,
        expires_at=expires_at,
    )


def test_topic_candidate_roundtrips_and_returns_latest_first(tmp_path):
    """D13 — 적재한 후보가 필드 그대로, 최신순으로 나온다."""
    store = MemoryStore(tmp_path / "t.db")
    _insert(store, dedup_key="a", summary="어제 경기 3대1", fetched_at=_T1)
    _insert(store, dedup_key="b", summary="오늘 비 온다", fetched_at=_T2, topic_key="날씨")

    cands = store.fresh_candidates(10, _T2)
    assert [c.summary for c in cands] == ["오늘 비 온다", "어제 경기 3대1"]
    assert cands[0].source == "rss"
    assert cands[0].topic_key == "날씨"
    assert cands[0].used_at is None
    assert cands[0].expires_at is None  # TTL 없는 후보는 만료 자체가 없다


def test_fresh_candidates_excludes_expired_and_used(tmp_path):
    """만료·사용 후보는 안 나온다. expires_at NULL은 영원히 유효(콜백용, feed.md PR2)."""
    store = MemoryStore(tmp_path / "t.db")
    _insert(store, dedup_key="expired", summary="지난 소식", expires_at=_T1)
    used = _insert(store, dedup_key="used", summary="이미 쓴 소식")
    _insert(store, dedup_key="alive", summary="살아있는 소식", expires_at=None)
    store.mark_candidate_used(used, _T1)

    assert [c.summary for c in store.fresh_candidates(10, _T2)] == ["살아있는 소식"]


def test_insert_candidate_ignores_duplicate_dedup_key(tmp_path):
    """같은 원문 재적재 방지 — 덮어쓰기(upsert)가 아니라 무시(DO NOTHING)다."""
    db = tmp_path / "t.db"
    store = MemoryStore(db)
    first = _insert(store, dedup_key="same", summary="처음 요약")
    again = _insert(store, dedup_key="same", summary="나중 요약")

    assert first is not None
    assert again is None  # 충돌 → 적재 안 함
    row = sqlite3.connect(db).execute(
        "SELECT COUNT(*) , MIN(summary) FROM topic_candidate"
    ).fetchone()
    assert row == (1, "처음 요약")  # 첫 행이 살아있다 — 덮어쓰지 않는다


def test_candidate_exists_reports_dedup_key(tmp_path):
    """요약 LLM을 부르기 전에 거르는 조회(feed.md 3.3)."""
    store = MemoryStore(tmp_path / "t.db")
    _insert(store, dedup_key="known")

    assert store.candidate_exists("known") is True
    assert store.candidate_exists("unknown") is False


def test_mark_candidate_used_survives_restart(tmp_path):
    """한 번 쓴 소재로 재기동 후 또 말 걸지 않는다."""
    db = tmp_path / "t.db"
    store = MemoryStore(db)
    cid = _insert(store, dedup_key="a")
    store.mark_candidate_used(cid, _T1)
    store.close()

    reopened = MemoryStore(db)  # 데몬 재시작 시뮬레이션
    assert reopened.fresh_candidates(10, _T2) == []


def test_last_collect_at_starts_none_and_keeps_single_row(tmp_path):
    """수집 게이트 상태 — 재기동 직후 재수집을 막는 게 존재 이유라 영속이어야 한다."""
    db = tmp_path / "t.db"
    store = MemoryStore(db)
    assert store.last_collect_at() is None  # 첫 기동 — 곧바로 수집해도 됨

    store.set_last_collect_at(_T0)
    store.set_last_collect_at(_T1)
    store.close()

    reopened = MemoryStore(db)
    assert reopened.last_collect_at() == _T1
    count = sqlite3.connect(db).execute("SELECT COUNT(*) FROM feed_meta").fetchone()[0]
    assert count == 1  # 누적이 아니라 1행 upsert
