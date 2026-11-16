package navi.server.quota;

import java.util.UUID;
import navi.server.assembly.TurnKind;
import navi.server.turn.TurnStore;
import org.springframework.boot.context.properties.EnableConfigurationProperties;
import org.springframework.jdbc.core.simple.JdbcClient;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

/**
 * 턴의 문 — 한도를 확인하고, 통과하면 턴을 연다. 둘이 한 트랜잭션이다.
 *
 * <p>쿼터는 비용이 생기는 곳에서 집행한다(service.md §2.2). 두뇌를 부르기 전의 마지막 관문이
 * 여기다. 클라이언트는 신뢰 경계 밖이라 클라이언트 쪽 제한은 세지 않는다.
 *
 * <p><b>확인과 턴 생성을 갈라놓으면 안 된다.</b> 같은 사용자의 요청 둘이 동시에 "아직 19개"를
 * 보고 둘 다 통과한다. 사용자 행을 잠가 그 사용자의 요청을 줄 세운다. Redis 없이 PostgreSQL
 * 행 잠금으로 한다(D25) — 서버가 여러 대여도 성립하고, 병목이 측정되면 그때 바꾼다.
 *
 * <p>일일 토큰은 <b>턴 앞에서 확인하고 턴 뒤에 차감한다</b>(차감은 {@link TurnStore}가 턴 종료와
 * 같은 트랜잭션에서 한다). 턴이 얼마나 쓸지는 끝나야 안다. 그래서 한도를 마지막 한 턴만큼은
 * 넘을 수 있다 — 넘는 폭은 출력 상한({@code max_tokens})과 입력 크기로 묶여 있다.
 */
@Service
@EnableConfigurationProperties(QuotaProperties.class)
public class TurnGate {

    private static final int WINDOW_SECONDS = 60;

    private final JdbcClient jdbc;
    private final TurnStore store;
    private final QuotaProperties limits;

    public TurnGate(JdbcClient jdbc, TurnStore store, QuotaProperties limits) {
        this.jdbc = jdbc;
        this.store = store;
        this.limits = limits;
    }

    public QuotaProperties limits() {
        return limits;
    }

    /**
     * 한도를 확인하고 턴을 연다.
     *
     * @return 열린 턴의 ID
     * @throws QuotaExceededException 한도를 넘었다. 턴은 만들어지지 않았다
     */
    @Transactional
    public long open(
            long userId, UUID clientTurnId, String personaKey, TurnKind kind, String text,
            String model) {
        if (text.codePointCount(0, text.length()) > limits.maxTextChars()) {
            throw new QuotaExceededException(
                    QuotaExceededException.TEXT_TOO_LONG,
                    "발화가 너무 깁니다(최대 " + limits.maxTextChars() + "자)", null);
        }
        // 이 사용자의 요청을 줄 세운다. 아래 두 확인과 턴 생성이 끝날 때까지 다음 요청은 기다린다.
        jdbc.sql("SELECT 1 FROM app_user WHERE user_id = ? FOR UPDATE")
                .param(userId).query(Integer.class).single();

        checkRate(userId);
        checkDailyTokens(userId);
        return store.begin(userId, clientTurnId, personaKey, kind, text, model);
    }

    private void checkRate(long userId) {
        var window = jdbc.sql("""
                        SELECT count(*) AS n,
                               coalesce(ceil(extract(epoch FROM
                                   min(created_at) + make_interval(secs => ?) - now())), 0) AS wait
                          FROM turn
                         WHERE user_id = ? AND created_at > now() - make_interval(secs => ?)
                        """)
                .params(WINDOW_SECONDS, userId, WINDOW_SECONDS)
                .query((rs, n) -> new long[] {rs.getLong("n"), rs.getLong("wait")})
                .single();
        if (window[0] >= limits.turnsPerMinute()) {
            throw new QuotaExceededException(
                    QuotaExceededException.RATE_LIMITED,
                    "너무 자주 보냈습니다",
                    (int) Math.clamp(window[1], 1, WINDOW_SECONDS));
        }
    }

    private void checkDailyTokens(long userId) {
        // "오늘"은 사용자 시간대의 날짜다. 자정까지 남은 초도 그 시간대로 센다.
        var today = jdbc.sql("""
                        SELECT coalesce(d.tokens, 0) AS used,
                               ceil(extract(epoch FROM
                                   date_trunc('day', now() AT TIME ZONE u.timezone)
                                   + interval '1 day' - (now() AT TIME ZONE u.timezone))) AS until_reset
                          FROM app_user u
                          LEFT JOIN usage_daily d
                                 ON d.user_id = u.user_id
                                AND d.day = (now() AT TIME ZONE u.timezone)::date
                         WHERE u.user_id = ?
                        """)
                .param(userId)
                .query((rs, n) -> new long[] {rs.getLong("used"), rs.getLong("until_reset")})
                .single();
        if (today[0] >= limits.dailyTokens()) {
            throw new QuotaExceededException(
                    QuotaExceededException.DAILY_QUOTA_EXCEEDED,
                    "오늘 쓸 수 있는 양을 다 썼습니다",
                    (int) Math.max(1, today[1]));
        }
    }
}
