-- 기기 등록 기록 — 같은 곳에서 토큰을 계속 새로 받아 한도를 비껴가는 것을 막는다(checklist G4).
-- 계정이 없어 사용자를 묶을 기준이 접속 주소뿐이다. 주소 원문은 저장하지 않는다: 서버 비밀값으로
-- 만든 HMAC만 둔다(G8 — 수집 최소화). 하루가 지난 행은 등록 때마다 지운다.
CREATE TABLE device_registration (
    registration_id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    source_hash     CHAR(64)    NOT NULL,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX idx_registration_source ON device_registration (source_hash, created_at);
