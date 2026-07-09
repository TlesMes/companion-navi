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

from navi.models import Turn, Usage

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

    # ─── 친밀도 ─────────────────────────────────────────────

    def get_intimacy(self, user_id: int) -> float:
        row = self._conn.execute(
            "SELECT score FROM intimacy WHERE user_id = ?", (user_id,)
        ).fetchone()
        return row["score"] if row else 0.0

    def update_intimacy(self, user_id: int, delta: float) -> float:
        """산식(D9)은 Phase 4 — 지금은 단순 가감만 제공한다."""
        self._conn.execute(
            "UPDATE intimacy SET score = score + ?, updated_at = ? WHERE user_id = ?",
            (delta, _now_iso(), user_id),
        )
        self._conn.commit()
        return self.get_intimacy(user_id)

    # ─── 선톡축 모드 (Stage 14) ───────────────────────────────

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
