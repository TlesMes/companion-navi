package navi.server.gateway;

import static org.assertj.core.api.Assertions.assertThat;

import java.util.Map;
import java.util.UUID;
import navi.server.PostgresTestConfig;
import navi.server.identity.DeviceService;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.boot.test.context.SpringBootTest.WebEnvironment;
import org.springframework.boot.test.web.server.LocalServerPort;
import org.springframework.context.annotation.Import;
import tools.jackson.databind.JsonNode;

/** 활성 연결 하나 + 대기(D20) — 같은 사용자의 연결이 여럿일 때. */
@SpringBootTest(webEnvironment = WebEnvironment.RANDOM_PORT,
        properties = "navi.turn.allow-proactive=true")
@Import({PostgresTestConfig.class, TurnStreamTest.Brain.class})
class ActiveConnectionTest {

    @LocalServerPort int port;
    @Autowired DeviceService devices;
    @Autowired ScriptedBrain brain;
    String token;

    @BeforeEach
    void setUp() {
        brain.reset();
        token = devices.register().token();
    }

    private TestClient open() throws Exception {
        TestClient client = TestClient.connect(port, token);
        assertThat(client.next("session.ready").get("role").asString()).isEqualTo("active");
        return client;
    }

    @Test
    void newConnectionTakesOverAndTheOldOneWaitsWithoutBeingLoggedOut() throws Exception {
        try (TestClient home = open(); TestClient laptop = open()) {
            home.next("session.standby");

            // 밀린 연결도 살아 있다 — 사용자가 그 기기에 말을 걸면 활성권을 되찾는다.
            home.start("나 다시 집이야");
            home.next("session.active");
            assertThat(home.until("turn.end")).isNotEmpty();
            laptop.next("session.standby");
        }
    }

    @Test
    void standbyCannotSpeakFirst() throws Exception {
        try (TestClient home = open(); TestClient laptop = open()) {
            home.next("session.standby");

            home.start(UUID.randomUUID().toString(), "proactive", "navi", "GTA 6 출시가 미뤄졌다.");

            JsonNode error = home.next("turn.error");
            assertThat(error.get("code").asString()).isEqualTo("not_active");
            assertThat(brain.lastRequest).isNull();
        }
    }

    @Test
    void whenTheActiveConnectionLeavesTheLatestStandbyIsPromoted() throws Exception {
        try (TestClient home = open(); TestClient laptop = open()) {
            home.next("session.standby");

            laptop.close();

            home.next("session.active");
            home.start(UUID.randomUUID().toString(), "proactive", "navi", "소재");
            assertThat(home.next().get("type").asString()).isEqualTo("turn.accepted");
            home.until("turn.end");
        }
    }

    @Test
    void explicitActivationMovesTheRole() throws Exception {
        try (TestClient home = open(); TestClient laptop = open()) {
            home.next("session.standby");

            home.send(Map.of("type", "session.activate")); // 웨이크워드 — 말이 오기 전에 먼저 알린다

            home.next("session.active");
            laptop.next("session.standby");
        }
    }

    @Test
    void otherUsersAreNotAffected() throws Exception {
        try (TestClient mine = open();
                TestClient theirs = TestClient.connect(port, devices.register().token())) {
            theirs.next("session.ready");

            assertThat(mine.nothingMore(300)).isTrue();
        }
    }
}
