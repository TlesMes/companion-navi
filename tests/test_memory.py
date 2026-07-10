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
