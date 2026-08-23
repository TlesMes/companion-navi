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

-- 관심사 피드 후보(arch 6, D13/feed.md) — 나비가 먼저 말 걸 "재료".
-- source='rss'는 외부 뉴스 소화물(TTL 있음), 'memory'는 대화 콜백(TTL NULL, feed.md PR2).
-- 둘이 한 테이블에 사는 이유: 소비처(pick_topic)가 같고 source만 다르다.
-- 이 리포엔 마이그레이션 경로가 없다(executescript + IF NOT EXISTS가 전부) — 아직 안 쓰는
-- 'memory'를 CHECK에 미리 넣어 두는 것도 그래서다. 나중에 ALTER로 못 고친다.
CREATE TABLE IF NOT EXISTS topic_candidate (
    candidate_id INTEGER PRIMARY KEY AUTOINCREMENT,
    source       TEXT NOT NULL CHECK (source IN ('rss', 'memory')),
    topic_key    TEXT NOT NULL,   -- 관심사 분류(등록명 or 추출 주제)
    summary      TEXT NOT NULL,   -- 나비 입말 재료 — 트리거 문자열로 직행
    dedup_key    TEXT NOT NULL,   -- source:topic_key:원문해시 — 재적재·요약 LLM 낭비 방지
    fetched_at   TEXT NOT NULL,   -- 우리가 가져온 시각(배치 안에서 전부 같다)
    published_at TEXT,            -- 원문 발행 시각 — 인출 정렬의 기준. 없는 피드는 NULL
    expires_at   TEXT,            -- 뉴스 TTL(발행 시각 기준 96h), memory 콜백은 NULL
    used_at      TEXT             -- 선제 발화에 쓴 시각 — 같은 이슈 재발화 방지
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_candidate_dedup ON topic_candidate (dedup_key);
-- fresh_candidates의 조회 축 — 미사용 후보를 발행 최신순으로.
-- fetched_at으로 정렬하면 한 배치가 전부 동률이라 삽입 순서(=피드 최신순)가 뒤집힌다.
CREATE INDEX IF NOT EXISTS idx_candidate_fresh ON topic_candidate (used_at, published_at);

-- 수집 게이트 상태 — 모드와 무관해서 mode_state에 얹지 않고 전용 1행 테이블로(feed.md 7).
-- 재기동 직후 곧바로 재수집하는 걸 막는 게 존재 이유라 반드시 영속이어야 한다.
CREATE TABLE IF NOT EXISTS feed_meta (
    id              INTEGER PRIMARY KEY CHECK (id = 1),  -- 단일 행 강제
    last_collect_at TEXT
);
