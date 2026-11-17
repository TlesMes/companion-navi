package navi.server.gateway;

import static org.assertj.core.api.Assertions.assertThat;

import navi.server.PostgresTestConfig;
import navi.server.identity.DeviceService;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.boot.test.context.SpringBootTest.WebEnvironment;
import org.springframework.boot.test.web.server.LocalServerPort;
import org.springframework.context.annotation.Import;
import org.springframework.jdbc.core.simple.JdbcClient;
import tools.jackson.databind.JsonNode;

/** 한도 초과가 소켓으로 어떻게 보이는가 — 거절되고, 두뇌는 불리지 않고, 연결은 산다. */
@SpringBootTest(webEnvironment = WebEnvironment.RANDOM_PORT, properties = {
    "navi.quota.max-text-chars=10",
    "navi.quota.turns-per-minute=2",
    "navi.quota.daily-tokens=100",
})
@Import({PostgresTestConfig.class, TurnStreamTest.Brain.class})
class QuotaStreamTest {

    @LocalServerPort int port;
    @Autowired DeviceService devices;
    @Autowired JdbcClient jdbc;
    @Autowired ScriptedBrain brain;

    @Test
    void sessionReadyAnnouncesTheLimits() throws Exception {
        try (TestClient client = TestClient.connect(port, devices.register().token())) {
            JsonNode limits = client.next("session.ready").get("limits");

            assertThat(limits.get("max_text_chars").asInt()).isEqualTo(10);
            assertThat(limits.get("turns_per_minute").asInt()).isEqualTo(2);
            assertThat(limits.get("daily_tokens").asLong()).isEqualTo(100);
        }
    }

    @Test
    void overlongUtteranceIsRejectedWithoutCallingTheBrain() throws Exception {
        brain.reset();
        try (TestClient client = TestClient.connect(port, devices.register().token())) {
            client.next("session.ready");

            String id = client.start("이 발화는 열 글자를 넘는다");

            JsonNode error = client.next("turn.error");
            assertThat(error.get("turn_id").asString()).isEqualTo(id);
            assertThat(error.get("code").asString()).isEqualTo("text_too_long");
            assertThat(error.has("retry_after_s")).isFalse();
            assertThat(brain.lastRequest).isNull();
            // 거절된 뒤에도 연결은 살아 있고 짧은 발화는 된다.
            client.start("안녕");
            assertThat(client.until("turn.end")).isNotEmpty();
        }
    }

    @Test
    void thirdTurnInAMinuteIsRateLimited() throws Exception {
        brain.reset();
        try (TestClient client = TestClient.connect(port, devices.register().token())) {
            client.next("session.ready");
            // 일일 한도에 먼저 걸리지 않게 이 사용자의 오늘 장부를 비워 가며 본다.
            client.start("하나");
            client.until("turn.end");
            jdbc.sql("DELETE FROM usage_daily").update();
            client.start("둘");
            client.until("turn.end");
            jdbc.sql("DELETE FROM usage_daily").update();
            brain.lastRequest = null;

            client.start("셋");

            JsonNode error = client.next("turn.error");
            assertThat(error.get("code").asString()).isEqualTo("rate_limited");
            assertThat(error.get("retry_after_s").asInt()).isBetween(1, 60);
            assertThat(brain.lastRequest).isNull();
        }
    }

    @Test
    void turnAfterTheDailyBudgetIsSpentIsRejected() throws Exception {
        brain.reset();
        try (TestClient client = TestClient.connect(port, devices.register().token())) {
            long user = client.next("session.ready").get("user_id").asLong();
            // 대본 두뇌는 턴마다 60토큰을 쓴다 — 두 턴이면 120으로 한도(100)를 넘는다.
            client.start("하나");
            client.until("turn.end");
            client.start("둘");
            client.until("turn.end");
            jdbc.sql("UPDATE turn SET created_at = created_at - interval '61 seconds' WHERE user_id = ?")
                    .param(user).update();
            brain.lastRequest = null;

            client.start("셋");

            JsonNode error = client.next("turn.error");
            assertThat(error.get("code").asString()).isEqualTo("daily_quota_exceeded");
            assertThat(error.get("retry_after_s").asInt()).isPositive();
            assertThat(brain.lastRequest).isNull();
            assertThat(jdbc.sql("SELECT tokens FROM usage_daily WHERE user_id = ?")
                    .param(user).query(Long.class).single()).isEqualTo(120);
        }
    }
}
