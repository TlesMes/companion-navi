"""메모리 모듈 — 단기기억·친밀도·usage_log·mode_state (01 문서 4.5절 계약의 부분집합).

계약 확장: recall_recent_for_user(user_id, n).
"껐다 켜도 어제 대화를 기억한다"(Phase 1 완료 기준)를 위해 세션 경계 없이
사용자의 최근 턴을 인출한다. CLI는 실행마다 새 session_id를 만들기 때문에
session_id 기준 인출만으로는 이전 실행의 대화가 보이지 않는다.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime
from pathlib import Path

from navi.models import TopicCandidate, Turn, Usage

_SCHEMA_PATH = Path(__file__).with_name("schema.sql")


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


class MemoryStore:
    def __init__(self, db_path: Path | str):
        self._conn = sqlite3.connect(db_path)
        self._conn.row_factory = sqlite3.Row
        self._conn.executescript(_SCHEMA_PATH.read_text(encoding="utf-8"))
        self._conn.commit()

    def close(self) -> None:
        self._conn.close()

    # ─── user ───────────────────────────────────────────────

    def ensure_user(self, display_name: str) -> int:
        """단일 사용자 전제 — 이미 있으면 그 사용자를, 없으면 생성해서 돌려준다."""
        row = self._conn.execute(
            "SELECT user_id FROM user ORDER BY user_id LIMIT 1"
        ).fetchone()
        if row:
            return row["user_id"]
        cur = self._conn.execute(
            "INSERT INTO user (display_name, created_at) VALUES (?, ?)",
            (display_name, _now_iso()),
        )
        user_id = cur.lastrowid
        self._conn.execute(
            "INSERT INTO intimacy (user_id, score, updated_at) VALUES (?, 0, ?)",
            (user_id, _now_iso()),
        )
        self._conn.commit()
        return user_id

    # ─── 단기기억 ────────────────────────────────────────────

    def append_turn(
        self,
        session_id: str,
        user_id: int,
        role: str,
        text: str,
        trigger_type: str = "manual",
    ) -> None:
        self._conn.execute(
            "INSERT INTO conversation_turn"
            " (session_id, user_id, role, text, created_at, trigger_type)"
            " VALUES (?, ?, ?, ?, ?, ?)",
            (session_id, user_id, role, text, _now_iso(), trigger_type),
        )
        self._conn.commit()

    def recall_recent(self, session_id: str, n: int) -> list[Turn]:
        """해당 세션의 최근 n턴, 시간순."""
        rows = self._conn.execute(
            "SELECT * FROM ("
            "  SELECT * FROM conversation_turn WHERE session_id = ?"
            "  ORDER BY turn_id DESC LIMIT ?"
            ") ORDER BY turn_id ASC",
            (session_id, n),
        ).fetchall()
        return [_row_to_turn(r) for r in rows]

    def recall_recent_for_user(self, user_id: int, n: int) -> list[Turn]:
        """세션 경계 없이 사용자의 최근 n턴, 시간순."""
        rows = self._conn.execute(
            "SELECT * FROM ("
            "  SELECT * FROM conversation_turn WHERE user_id = ?"
            "  ORDER BY turn_id DESC LIMIT ?"
            ") ORDER BY turn_id ASC",
            (user_id, n),
        ).fetchall()
        return [_row_to_turn(r) for r in rows]

    # ─── 친밀도 — ✖ 폐기 (D9, 2026.08.31) ───────────────────
    # 새 코드는 쓰지 말 것. `update_intimacy`는 부르는 곳이 없고 `get_intimacy`는 늘 0을
    # 돌려준다(Conductor가 첫 프로필만 고르는 이유). 폐기 사유 → design/aliveness.md §3.3.
    # 테이블 제거는 마이그레이션 경로가 없어(executescript + IF NOT EXISTS) 별도 판단.

    def get_intimacy(self, user_id: int) -> float:
        row = self._conn.execute(
            "SELECT score FROM intimacy WHERE user_id = ?", (user_id,)
        ).fetchone()
        return row["score"] if row else 0.0

    def update_intimacy(self, user_id: int, delta: float) -> float:
        """단순 가감. 산식(D9)은 폐기됐고 이 메서드를 부르는 곳도 없다."""
        self._conn.execute(
            "UPDATE intimacy SET score = score + ?, updated_at = ? WHERE user_id = ?",
            (delta, _now_iso(), user_id),
        )
        self._conn.commit()
        return self.get_intimacy(user_id)

    # ─── 능동축 모드 (Stage 14) ───────────────────────────────

    def get_mode_state(self, user_id: int) -> tuple[str, str | None] | None:
        """저장된 (current_mode, override_until) — 없으면 None(첫 기동)."""
        row = self._conn.execute(
            "SELECT current_mode, override_until FROM mode_state WHERE user_id = ?",
            (user_id,),
        ).fetchone()
        return (row["current_mode"], row["override_until"]) if row else None

    def set_mode_state(
        self, user_id: int, mode: str, override_until: str | None
    ) -> None:
        self._conn.execute(
            "INSERT INTO mode_state (user_id, current_mode, override_until, updated_at)"
            " VALUES (?, ?, ?, ?)"
            " ON CONFLICT(user_id) DO UPDATE SET"
            "   current_mode = excluded.current_mode,"
            "   override_until = excluded.override_until,"
            "   updated_at = excluded.updated_at",
            (user_id, mode, override_until, _now_iso()),
        )
        self._conn.commit()

    # ─── 능동성 로그 (Phase 3 순서 4) ────────────────────────

    def log_interaction(
        self, event: str, mode_at_time: str | None = None, note: str | None = None
    ) -> None:
        """능동 발화·반응 1건 기록 — 나중에 응답률/무시율 산출의 원천 데이터.

        event ∈ {initiated, user_responded, user_ignored, user_overrode}.
        """
        self._conn.execute(
            "INSERT INTO interaction_log (ts, event, mode_at_time, note)"
            " VALUES (?, ?, ?, ?)",
            (_now_iso(), event, mode_at_time, note),
        )
        self._conn.commit()

    def count_interactions(self, event: str, since_iso: str) -> int:
        """since_iso(포함) 이후 특정 event 건수 — daily_cap 판정에 쓴다."""
        row = self._conn.execute(
            "SELECT COUNT(*) AS n FROM interaction_log WHERE event = ? AND ts >= ?",
            (event, since_iso),
        ).fetchone()
        return row["n"]

    # ─── 관심사 피드 후보 (D13) ──────────────────────────────

    def insert_candidate(
        self,
        *,
        source: str,
        topic_key: str,
        summary: str,
        dedup_key: str,
        fetched_at: str,
        published_at: str | None = None,
        expires_at: str | None = None,
    ) -> int | None:
        """후보 1건 적재 — dedup_key가 이미 있으면 아무것도 안 하고 None.

        INSERT OR IGNORE가 아니라 ON CONFLICT(dedup_key) DO NOTHING인 이유: OR IGNORE는
        source CHECK 위반까지 조용히 삼켜서 오타난 source가 "dedup으로 걸렸나 보다"로
        위장된다. 그건 크게 터져야 하는 버그다.
        """
        cur = self._conn.execute(
            "INSERT INTO topic_candidate"
            " (source, topic_key, summary, dedup_key, fetched_at, published_at, expires_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?)"
            " ON CONFLICT(dedup_key) DO NOTHING",
            (source, topic_key, summary, dedup_key, fetched_at, published_at, expires_at),
        )
        self._conn.commit()
        return cur.lastrowid if cur.rowcount else None

    def candidate_exists(self, dedup_key: str) -> bool:
        """이미 적재된 원문인가 — 요약 LLM을 부르기 *전에* 거르는 용도(feed.md 3.3)."""
        row = self._conn.execute(
            "SELECT 1 FROM topic_candidate WHERE dedup_key = ? LIMIT 1", (dedup_key,)
        ).fetchone()
        return row is not None

    def fresh_candidates(self, k: int, now_iso: str) -> list[TopicCandidate]:
        """미사용·TTL 유효 후보를 **발행 최신순** k개.

        정렬 기준이 published_at인 이유: fetched_at은 한 배치 안에서 전부 같아 동률이 되고,
        그러면 tiebreak가 삽입 순서를 결정한다. RSS는 최신 기사를 앞에 주므로 삽입 순서가
        곧 최신순인데, candidate_id DESC로 깨면 그게 뒤집혀 **가장 낡은 기사가 [0]에 온다**
        (pick_topic은 [0]만 쓴다). 발행 시각이 없는 소스(memory 콜백 등)는 fetched_at으로
        폴백한다 — 방금 만든 콜백이 옛 기사보다 앞서는 건 의도된 순서다(feed.md 1: 뉴스보다
        콜백이 값지다). 동률이면 삽입 순서(candidate_id ASC = 피드 최신순)를 따른다.

        now_iso를 인자로 받는 건 count_interactions와 같은 이유 — TTL 만료를 테스트하려면
        시계 주입이 필수다. expires_at 비교는 문자열 비교라 호출부가 _now_iso()와 같은
        포맷(UTC, +00:00 오프셋)을 넘겨야 성립한다.
        """
        rows = self._conn.execute(
            "SELECT * FROM topic_candidate"
            " WHERE used_at IS NULL AND (expires_at IS NULL OR expires_at > ?)"
            " ORDER BY COALESCE(published_at, fetched_at) DESC, candidate_id ASC LIMIT ?",
            (now_iso, k),
        ).fetchall()
        return [_row_to_candidate(r) for r in rows]

    def mark_candidate_used(
        self, candidate_id: int, now_iso: str | None = None
    ) -> None:
        """선제 발화에 썼다고 표시 — 같은 이슈로 또 말 걸지 않게."""
        self._conn.execute(
            "UPDATE topic_candidate SET used_at = ? WHERE candidate_id = ?",
            (now_iso or _now_iso(), candidate_id),
        )
        self._conn.commit()

    def last_collect_at(self) -> str | None:
        """마지막 수집 시각 — 없으면 None(첫 기동, 곧바로 수집해도 됨)."""
        row = self._conn.execute(
            "SELECT last_collect_at FROM feed_meta WHERE id = 1"
        ).fetchone()
        return row["last_collect_at"] if row else None

    def set_last_collect_at(self, now_iso: str) -> None:
        self._conn.execute(
            "INSERT INTO feed_meta (id, last_collect_at) VALUES (1, ?)"
            " ON CONFLICT(id) DO UPDATE SET last_collect_at = excluded.last_collect_at",
            (now_iso,),
        )
        self._conn.commit()

    # ─── 사용자 설정 오버라이드 ──────────────────────────────

    def get_setting(self, key: str) -> str | None:
        row = self._conn.execute(
            "SELECT value FROM setting WHERE key = ?", (key,)
        ).fetchone()
        return row["value"] if row else None

    def set_setting(self, key: str, value: str) -> None:
        self._conn.execute(
            "INSERT INTO setting (key, value, updated_at) VALUES (?, ?, ?)"
            " ON CONFLICT(key) DO UPDATE SET value = excluded.value,"
            " updated_at = excluded.updated_at",
            (key, value, _now_iso()),
        )
        self._conn.commit()

    def all_settings(self) -> dict[str, str]:
        return {
            row["key"]: row["value"]
            for row in self._conn.execute("SELECT key, value FROM setting")
        }

    # ─── 원가 모니터링 ────────────────────────────────────────

    def log_usage(self, kind: str, usage: Usage, est_cost: float | None = None) -> None:
        self._conn.execute(
            "INSERT INTO usage_log (ts, kind, tokens_or_units, est_cost) VALUES (?, ?, ?, ?)",
            (
                _now_iso(),
                kind,
                json.dumps({"input": usage.input_tokens, "output": usage.output_tokens}),
                est_cost,
            ),
        )
        self._conn.commit()


def _row_to_turn(row: sqlite3.Row) -> Turn:
    return Turn(
        role=row["role"],
        text=row["text"],
        created_at=datetime.fromisoformat(row["created_at"]),
        session_id=row["session_id"],
        trigger_type=row["trigger_type"],
    )


def _opt_dt(value: str | None) -> datetime | None:
    return datetime.fromisoformat(value) if value else None


def _row_to_candidate(row: sqlite3.Row) -> TopicCandidate:
    return TopicCandidate(
        candidate_id=row["candidate_id"],
        source=row["source"],
        topic_key=row["topic_key"],
        summary=row["summary"],
        fetched_at=datetime.fromisoformat(row["fetched_at"]),
        published_at=_opt_dt(row["published_at"]),
        expires_at=_opt_dt(row["expires_at"]),
        used_at=_opt_dt(row["used_at"]),
    )
