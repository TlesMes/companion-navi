-- 사용자의 하루 사용량. 일일 토큰 한도(service.md §10, G7 / D24)의 장부다.
-- "하루"는 사용자 시간대의 날짜다(app_user.timezone, G4).
--
-- 턴 행을 합산해도 같은 값이 나오지만 따로 둔다: 턴마다 "오늘 얼마나 썼나"를 한 행으로 읽고,
-- 차감이 한 문장(tokens = tokens + ?)이라 동시 요청에서 잃어버리는 갱신이 없다.
CREATE TABLE usage_daily (
    user_id BIGINT  NOT NULL REFERENCES app_user (user_id) ON DELETE CASCADE,
    day     DATE    NOT NULL,
    tokens  BIGINT  NOT NULL DEFAULT 0,   -- 입력(캐시 포함) + 출력
    turns   INTEGER NOT NULL DEFAULT 0,   -- 두뇌를 실제로 부른 턴 수(폐기 포함)
    PRIMARY KEY (user_id, day)
);
