package navi.server.quota;

import org.springframework.jdbc.core.simple.JdbcClient;
import org.springframework.stereotype.Repository;

/**
 * 하루 사용량 장부({@code usage_daily})에 더한다. 턴과 워커 작업이 같이 쓴다.
 *
 * <p>사실 추출처럼 사용자 대화에서 나온 배치 작업의 토큰도 그 사용자에게 귀속한다(service.md
 * §11 R1). 공유 피드 같은 운영 원가는 여기로 오지 않는다.
 *
 * <p>더하기를 한 문장으로 한다 — 읽고-더해-쓰지 않으므로 동시 요청에서 갱신을 잃지 않는다.
 * 부르는 쪽의 트랜잭션 안에서 돈다(턴 종료·작업 완료와 한 몸이다).
 */
@Repository
public class UsageLedger {

    private final JdbcClient jdbc;

    public UsageLedger(JdbcClient jdbc) {
        this.jdbc = jdbc;
    }

    /**
     * @param turns 두뇌를 부른 턴이면 1, 배치 작업이면 0
     */
    public void charge(long userId, long tokens, int turns) {
        jdbc.sql("""
                        INSERT INTO usage_daily (user_id, day, tokens, turns)
                        SELECT user_id, (now() AT TIME ZONE timezone)::date, ?, ?
                          FROM app_user WHERE user_id = ?
                        ON CONFLICT (user_id, day)
                        DO UPDATE SET tokens = usage_daily.tokens + EXCLUDED.tokens,
                                      turns = usage_daily.turns + EXCLUDED.turns
                        """)
                .params(tokens, turns, userId).update();
    }
}
