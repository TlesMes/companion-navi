package navi.server.quota;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.catchThrowableOfType;

import java.util.ArrayList;
import java.util.List;
import java.util.UUID;
import java.util.concurrent.Callable;
import java.util.concurrent.CountDownLatch;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;
import java.util.concurrent.Future;
import navi.server.PostgresTestConfig;
import navi.server.assembly.TurnKind;
import navi.server.brain.Usage;
import navi.server.identity.DeviceService;
import navi.server.turn.TurnStore;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.context.annotation.Import;
import org.springframework.jdbc.core.simple.JdbcClient;

/** 한도 집행(G7) — 무엇이 거절되고, 동시 요청에서도 숫자가 맞는가. */
@SpringBootTest(properties = {
    "navi.quota.max-text-chars=10",
    "navi.quota.turns-per-minute=3",
    "navi.quota.daily-tokens=200",
})
@Import(PostgresTestConfig.class)
class QuotaTest {

    @Autowired TurnGate gate;
    @Autowired TurnStore store;
    @Autowired DeviceService devices;
    @Autowired JdbcClient jdbc;
    long user;

    @BeforeEach
    void setUp() {
        user = devices.register().identity().userId();
    }

    private long open(long userId, String text) {
        return gate.open(userId, UUID.randomUUID(), "navi", TurnKind.REACTIVE, text, "m");
    }

    private QuotaExceededException rejected(long userId, String text) {
        QuotaExceededException e =
                catchThrowableOfType(QuotaExceededException.class, () -> open(userId, text));
        assertThat(e).as("거절돼야 한다").isNotNull();
        return e;
    }

    private long turnCount(long userId) {
        return jdbc.sql("SELECT count(*) FROM turn WHERE user_id = ?")
                .param(userId).query(Long.class).single();
    }

    /** 지금까지의 턴을 창 밖(61초 전)으로 민다 — 시간이 흐른 것처럼. */
    private void ageTurns(long userId) {
        jdbc.sql("UPDATE turn SET created_at = created_at - interval '61 seconds' WHERE user_id = ?")
                .param(userId).update();
    }

    // ── 발화 길이 ─────────────────────────────────────────────────────────

    @Test
    void overlongUtteranceIsRejectedBeforeAnythingIsStored() {
        assertThat(open(user, "가나다라마바사아자차")).isPositive(); // 정확히 10자는 된다

        QuotaExceededException e = rejected(user, "가나다라마바사아자차카");

        assertThat(e.code()).isEqualTo("text_too_long");
        assertThat(e.retryAfterSeconds()).isNull(); // 기다려도 소용없다
        assertThat(turnCount(user)).isEqualTo(1);
    }

    @Test
    void lengthIsCountedInCodePoints() {
        // 이모지 10개 = 코드 포인트 10, UTF-16 20. 클라이언트(Python len)와 같은 단위로 센다.
        assertThat(open(user, "😀".repeat(10))).isPositive();
    }

    // ── 분당 턴 ───────────────────────────────────────────────────────────

    @Test
    void turnsBeyondTheMinuteLimitAreRejectedUntilTheWindowPasses() {
        open(user, "하나");
        open(user, "둘");
        open(user, "셋");

        QuotaExceededException e = rejected(user, "넷");

        assertThat(e.code()).isEqualTo("rate_limited");
        assertThat(e.retryAfterSeconds()).isBetween(1, 60);
        assertThat(turnCount(user)).isEqualTo(3);

        ageTurns(user);
        assertThat(open(user, "넷")).isPositive();
    }

    @Test
    void limitsArePerUser() {
        open(user, "하나");
        open(user, "둘");
        open(user, "셋");
        long other = devices.register().identity().userId();

        assertThat(open(other, "하나")).isPositive();
    }

    @Test
    void concurrentRequestsCannotSlipPastTheMinuteLimit() throws Exception {
        // 같은 사용자의 요청 20개가 동시에 온다(연결 여러 개). 확인과 생성이 갈라져 있으면
        // 여럿이 "아직 2개"를 보고 통과한다.
        int attempts = 20;
        CountDownLatch go = new CountDownLatch(1);
        List<Future<Boolean>> results = new ArrayList<>();
        try (ExecutorService pool = Executors.newFixedThreadPool(attempts)) {
            for (int i = 0; i < attempts; i++) {
                results.add(pool.submit((Callable<Boolean>) () -> {
                    go.await();
                    try {
                        open(user, "동시");
                        return true;
                    } catch (QuotaExceededException e) {
                        return false;
                    }
                }));
            }
            go.countDown();
            long admitted = 0;
            for (Future<Boolean> r : results) {
                admitted += r.get() ? 1 : 0;
            }
            assertThat(admitted).isEqualTo(3);
        }
        assertThat(turnCount(user)).isEqualTo(3);
    }

    // ── 일일 토큰 ─────────────────────────────────────────────────────────

    @Test
    void dailyTokensAreChargedWhenATurnEndsAndBlockFurtherTurnsOnceSpent() {
        long first = open(user, "하나");
        store.finishProvisional(first, "응", "neutral", new Usage(90, 20, 5, 5), 1, 2); // 120
        long second = open(user, "둘");
        store.finishProvisional(second, "응", "neutral", new Usage(70, 10, 0, 0), 1, 2); // 80 → 200
        ageTurns(user); // 분당 제한과 섞이지 않게

        QuotaExceededException e = rejected(user, "셋");

        assertThat(e.code()).isEqualTo("daily_quota_exceeded");
        assertThat(e.retryAfterSeconds()).isBetween(1, 86_400);
        assertThat(jdbc.sql("SELECT tokens FROM usage_daily WHERE user_id = ?")
                .param(user).query(Long.class).single()).isEqualTo(200);
    }

    @Test
    void discardedTurnsAreChargedToo() {
        // 폐기해도 벤더에는 값을 냈다. 취소를 반복해 한도를 비껴갈 수 없어야 한다.
        long turn = open(user, "하나");
        store.discard(turn, new Usage(190, 10, 0, 0), 5);
        ageTurns(user);

        assertThat(rejected(user, "둘").code()).isEqualTo("daily_quota_exceeded");
    }

    @Test
    void aTurnIsChargedOnlyOnce() {
        long turn = open(user, "하나");
        store.finishProvisional(turn, "응", "neutral", new Usage(40, 10, 0, 0), 1, 2);
        store.finishProvisional(turn, "응", "neutral", new Usage(40, 10, 0, 0), 1, 2);
        store.discard(turn, new Usage(40, 10, 0, 0), 2);

        assertThat(jdbc.sql("SELECT tokens || '/' || turns FROM usage_daily WHERE user_id = ?")
                .param(user).query(String.class).single()).isEqualTo("50/1");
    }

    @Test
    void yesterdaysUsageDoesNotCountAgainstToday() {
        long turn = open(user, "하나");
        store.finishProvisional(turn, "응", "neutral", new Usage(300, 0, 0, 0), 1, 2);
        ageTurns(user);
        assertThat(rejected(user, "둘").code()).isEqualTo("daily_quota_exceeded");

        jdbc.sql("UPDATE usage_daily SET day = day - 1 WHERE user_id = ?").param(user).update();

        assertThat(open(user, "둘")).isPositive();
    }

    @Test
    void todayIsTheUsersLocalDate() {
        long turn = open(user, "하나");
        store.finishProvisional(turn, "응", "neutral", new Usage(10, 0, 0, 0), 1, 2);

        assertThat(jdbc.sql("""
                        SELECT d.day = (now() AT TIME ZONE u.timezone)::date
                          FROM usage_daily d JOIN app_user u USING (user_id) WHERE u.user_id = ?
                        """)
                .param(user).query(Boolean.class).single()).isTrue();
    }

    @Test
    void concurrentChargesAddUpExactly() throws Exception {
        // 차감이 읽고-더해-쓰기면 동시 요청에서 갱신을 잃는다. 장부가 턴 합계와 정확히 같아야 한다.
        int turns = 24;
        long many = devices.register().identity().userId();
        List<Long> ids = new ArrayList<>();
        for (int i = 0; i < turns; i++) {
            ids.add(store.begin(many, UUID.randomUUID(), "navi", TurnKind.REACTIVE, "동시", "m"));
        }
        CountDownLatch go = new CountDownLatch(1);
        try (ExecutorService pool = Executors.newFixedThreadPool(turns)) {
            List<Future<?>> done = new ArrayList<>();
            for (int i = 0; i < turns; i++) {
                long id = ids.get(i);
                int n = i;
                done.add(pool.submit(() -> {
                    go.await();
                    if (n % 2 == 0) {
                        store.finishProvisional(id, "응", "neutral", new Usage(7, 3, 0, 0), 1, 2);
                    } else {
                        store.discard(id, new Usage(7, 3, 0, 0), 2);
                    }
                    return null;
                }));
            }
            go.countDown();
            for (Future<?> f : done) {
                f.get();
            }
        }

        assertThat(jdbc.sql("SELECT tokens || '/' || turns FROM usage_daily WHERE user_id = ?")
                .param(many).query(String.class).single()).isEqualTo("240/24");
        assertThat(jdbc.sql("SELECT sum(input_tokens + output_tokens) FROM turn WHERE user_id = ?")
                .param(many).query(Long.class).single()).isEqualTo(240);
    }
}
