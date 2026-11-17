-- 사용자별 데이터 키(G8 ② 본문 필드 암호화). 서버 마스터 키로 감싼(AES-GCM) 값만 둔다.
-- 이 키를 지우면 그 사용자의 대화·사실 본문은 아무도 되돌릴 수 없다 — 삭제 경로(G8 ④)의 토대다.
ALTER TABLE app_user ADD COLUMN data_key BYTEA;
