-- 턴 게이트웨이의 첫 스키마. 단독 모드의 SQLite 스키마(navi/memory/schema.sql)를 옮긴 게 아니라
-- 다시 그렸다 — 그쪽은 단일 사용자 전제다(service.md §1).

-- 사용자. 지금은 전부 익명이다: 첫 접속에 행이 생기고 기기 토큰이 여기 연결된다(D23).
-- 턴·사실·쿼터는 전부 user_id에 붙는다. 로그인 수단이 나중에 붙어도 이 행에 연결만 하면 된다.
CREATE TABLE app_user (
    user_id    BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    -- 하루 한도의 "오늘"은 이 시간대로 센다(service.md §4, G4).
    timezone   TEXT        NOT NULL DEFAULT 'Asia/Seoul',
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- 기기(= 토큰). 사용자와 테이블을 나눈다 — 토큰을 사용자 키로 쓰면 계정을 나중에 못 붙인다(D23).
CREATE TABLE device (
    device_id    BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    user_id      BIGINT      NOT NULL REFERENCES app_user (user_id) ON DELETE CASCADE,
    -- 토큰 원문은 저장하지 않는다. SHA-256(hex). 토큰이 256비트 난수라 솔트·느린 해시가 필요 없다.
    token_hash   CHAR(64)    NOT NULL UNIQUE,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_seen_at TIMESTAMPTZ,
    revoked_at   TIMESTAMPTZ
);
CREATE INDEX idx_device_user ON device (user_id);

-- 턴 한 번(요청 → 응답). 본문은 여기 없다 — turn_message에 있다.
-- 폐기된 턴도 행은 남긴다(본문만 지운다): 쓴 토큰과 폐기율은 봐야 하는 수치다.
CREATE TABLE turn (
    turn_id            BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    user_id            BIGINT      NOT NULL REFERENCES app_user (user_id) ON DELETE CASCADE,
    -- 클라이언트가 만든 ID. 같은 턴이 두 번 오면 한 번만 받는다.
    client_turn_id     UUID        NOT NULL,
    -- 대화는 사용자×페르소나 단위다(service.md §16).
    persona_key        TEXT        NOT NULL,
    kind               TEXT        NOT NULL
        CHECK (kind IN ('reactive', 'proactive', 'proactive_callback')),
    --  generating  두뇌가 생성 중
    --  provisional 생성 끝, 재생 보고 대기(G1)
    --  confirmed   확정 — 재생 보고를 받았거나, 보고 없이 연결이 닫혀 전문으로 확정
    --  discarded   폐기 — 취소·연결 끊김(D22)·두뇌 실패
    status             TEXT        NOT NULL
        CHECK (status IN ('generating', 'provisional', 'confirmed', 'discarded')),
    model              TEXT        NOT NULL,
    mood               TEXT,
    -- 사용자가 끝까지 듣지 못했다(끼어들기 등). 재생 보고가 알려 준다.
    interrupted        BOOLEAN     NOT NULL DEFAULT FALSE,
    input_tokens       BIGINT      NOT NULL DEFAULT 0,
    output_tokens      BIGINT      NOT NULL DEFAULT 0,
    cache_read_tokens  BIGINT      NOT NULL DEFAULT 0,
    cache_write_tokens BIGINT      NOT NULL DEFAULT 0,
    ttft_ms            INTEGER,
    total_ms           INTEGER,
    -- 클라이언트가 보고한 수치(TTFA·RTF 등). 모양은 프로토콜 문서가 정한다.
    client_metrics     JSONB,
    created_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
    ended_at           TIMESTAMPTZ,
    UNIQUE (user_id, client_turn_id)
);
-- 분당 턴 수 집계용.
CREATE INDEX idx_turn_user_time ON turn (user_id, created_at);

-- 발화 한 행. 조립이 "최근 대화"로 읽는 단위다 — 행 수로 센다(사용자·나비 각각 한 행).
-- 본문은 평문이다. 사용자별 키 필드 암호화(service.md §10, G8 ②)는 아직 넣지 않았다.
CREATE TABLE turn_message (
    message_id  BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    turn_id     BIGINT      NOT NULL REFERENCES turn (turn_id) ON DELETE CASCADE,
    user_id     BIGINT      NOT NULL REFERENCES app_user (user_id) ON DELETE CASCADE,
    persona_key TEXT        NOT NULL,
    role        TEXT        NOT NULL CHECK (role IN ('user', 'assistant')),
    text        TEXT        NOT NULL,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX idx_message_recall ON turn_message (user_id, persona_key, message_id);
