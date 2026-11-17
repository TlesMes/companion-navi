-- 서버 기억(checklist G3). 단독 모드의 fact·fact_cursor(navi/memory/schema.sql)와 같은 뜻이다.
-- 사실은 사용자 단위다 — 페르소나를 바꿔도 사용자에 대한 사실은 이어진다(service.md §16).

-- 대화에서 뽑은, 사용자에 대해 아는 것 하나. 지우지 않고 superseded로 남긴다 — 추출이 틀려
-- 멀쩡한 사실을 덮었을 때 되돌릴 수 있어야 한다. 만료된 사실도 지우지 않는다(주입에서만 빠진다).
-- kind에 CHECK를 걸지 않는 이유는 단독 모드와 같다: 분류는 튜닝 대상이라 검증은 코드가 한다.
CREATE TABLE fact (
    fact_id           BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    user_id           BIGINT      NOT NULL REFERENCES app_user (user_id) ON DELETE CASCADE,
    kind              TEXT        NOT NULL,          -- profile | preference | ongoing | plan
    text              TEXT        NOT NULL,
    -- 출처 발화. 원문 확인과 연쇄 삭제(G8 ④)의 축. 발화가 지워지면(재생 0자) 출처만 비운다.
    source_message_id BIGINT      REFERENCES turn_message (message_id) ON DELETE SET NULL,
    status            TEXT        NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'superseded')),
    superseded_by     BIGINT      REFERENCES fact (fact_id),
    expires_at        TIMESTAMPTZ,                   -- NULL = 만료 없음(신상·선호)
    created_at        TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX idx_fact_active ON fact (user_id, status, fact_id);

-- 어느 발화까지 추출기에 보였는가. 사실의 최대 출처로 대신할 수 없다 — 뽑을 게 없는 구간이면
-- 사실이 0건이라 커서가 안 움직이고 같은 대화를 매번 다시 보낸다.
CREATE TABLE fact_cursor (
    user_id         BIGINT      PRIMARY KEY REFERENCES app_user (user_id) ON DELETE CASCADE,
    last_message_id BIGINT      NOT NULL,
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- 작업 큐(D25) — Python 워커가 pull(claim)한다. Redis 없이 SKIP LOCKED로 나눠 가진다.
--  queued   기다림(not_before 이후 가져갈 수 있다)
--  claimed  워커가 가져감. lease_until이 지나면 다른 워커가 다시 가져간다(워커가 죽은 경우)
--  done     끝남
--  failed   재시도 한도를 넘김
-- 완료 표시·결과 저장·쿼터 차감이 한 트랜잭션이다 — 큐를 PostgreSQL에 둔 이유(D25 근거 ②).
CREATE TABLE job (
    job_id      BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    kind        TEXT        NOT NULL,
    user_id     BIGINT      REFERENCES app_user (user_id) ON DELETE CASCADE,
    status      TEXT        NOT NULL DEFAULT 'queued'
        CHECK (status IN ('queued', 'claimed', 'done', 'failed')),
    attempts    INTEGER     NOT NULL DEFAULT 0,    -- claim할 때 오른다. complete·fail의 펜싱 토큰
    not_before  TIMESTAMPTZ NOT NULL DEFAULT now(),
    lease_until TIMESTAMPTZ,
    last_error  TEXT,
    result      JSONB,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);
-- 사용자당 같은 종류의 대기 작업은 하나 — 같은 대화를 두 워커가 동시에 추출하지 않게.
CREATE UNIQUE INDEX job_one_pending ON job (kind, user_id) WHERE status IN ('queued', 'claimed');
CREATE INDEX idx_job_claimable ON job (status, not_before, job_id);
