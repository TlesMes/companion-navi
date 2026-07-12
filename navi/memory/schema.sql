-- 01 문서 6장 논리 스키마의 부분집합 — Phase 1 테이블 + mode_state(Stage 14).
-- fact·memory_embedding 등은 해당 Phase에서 추가한다.

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

-- 능동축 모드(Stage 14) — 재기동해도 오버라이드("더 잘래" 등)가 살아남는다.
-- current_mode는 저장 상태(오버라이드의 근원)이고, 겉으로 보이는 모드는
-- ModeMachine이 시계와 합성해 판정한다 (navi/heartbeat/mode.py).
CREATE TABLE IF NOT EXISTS mode_state (
    user_id        INTEGER PRIMARY KEY REFERENCES user(user_id),
    current_mode   TEXT NOT NULL CHECK (current_mode IN ('sleep', 'active', 'dnd', 'snooze')),
    override_until TEXT,           -- ISO, NULL = 만료 없음(DND·기본)
    updated_at     TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS usage_log (
    log_id          INTEGER PRIMARY KEY AUTOINCREMENT,
    ts              TEXT NOT NULL,
    kind            TEXT NOT NULL CHECK (kind IN ('llm', 'stt', 'tts')),
    tokens_or_units TEXT NOT NULL,  -- JSON: {"input": n, "output": m}
    est_cost        REAL            -- 단가표 확정 전이라 NULL 허용
);

-- 능동성 튜닝 데이터(arch 6, Phase 3 순서 4) — 나비가 먼저 건 발화와 그 반응 기록.
-- 좋은 타이밍/주제는 종이로 못 정한다(진행 원칙 2) → 여기 쌓인 로그로 응답률·
-- 무시율을 계산해 timing.py 값을 튜닝하는 게 후속 작업. barge_in/false_endpoint(v2)는
-- 턴테이킹(D12)에서 추가한다.
CREATE TABLE IF NOT EXISTS interaction_log (
    log_id       INTEGER PRIMARY KEY AUTOINCREMENT,
    ts           TEXT NOT NULL,
    event        TEXT NOT NULL CHECK (
        event IN ('initiated', 'user_responded', 'user_ignored', 'user_overrode')
    ),
    mode_at_time TEXT,   -- 당시 능동축 모드(대개 active — 발화는 active에서만 나감)
    note         TEXT    -- 자유 메모(주제 힌트·"이 타이밍 별로" 등)
);

CREATE INDEX IF NOT EXISTS idx_interaction_time ON interaction_log (ts);
