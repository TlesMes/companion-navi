-- Phase 1 테이블만 (01 문서 6장 논리 스키마의 부분집합).
-- mode_state·fact·memory_embedding 등은 해당 Phase에서 추가한다.

CREATE TABLE IF NOT EXISTS user (
    user_id      INTEGER PRIMARY KEY AUTOINCREMENT,
    display_name TEXT NOT NULL,
    created_at   TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS intimacy (
    user_id    INTEGER PRIMARY KEY REFERENCES user(user_id),
    score      REAL NOT NULL DEFAULT 0,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS conversation_turn (
    turn_id      INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id   TEXT    NOT NULL,
    user_id      INTEGER NOT NULL REFERENCES user(user_id),
    role         TEXT    NOT NULL CHECK (role IN ('user', 'assistant')),
    text         TEXT    NOT NULL,
    created_at   TEXT    NOT NULL,
    trigger_type TEXT    NOT NULL DEFAULT 'manual',
    interrupted  INTEGER NOT NULL DEFAULT 0  -- barge-in 튜닝 데이터 (Phase 2+)
);

CREATE INDEX IF NOT EXISTS idx_turn_user_time ON conversation_turn (user_id, turn_id);
CREATE INDEX IF NOT EXISTS idx_turn_session   ON conversation_turn (session_id, turn_id);

CREATE TABLE IF NOT EXISTS usage_log (
    log_id          INTEGER PRIMARY KEY AUTOINCREMENT,
    ts              TEXT NOT NULL,
    kind            TEXT NOT NULL CHECK (kind IN ('llm', 'stt', 'tts')),
    tokens_or_units TEXT NOT NULL,  -- JSON: {"input": n, "output": m}
    est_cost        REAL            -- 단가표 확정 전이라 NULL 허용
);
