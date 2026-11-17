package navi.server.gateway;

import static org.assertj.core.api.Assertions.assertThat;

import java.util.Map;
import navi.server.PostgresTestConfig;
import navi.server.identity.DeviceService;
import navi.server.memory.FactStore;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.boot.test.context.SpringBootTest.WebEnvironment;
import org.springframework.boot.test.web.server.LocalServerPort;
import org.springframework.context.annotation.Import;
import org.springframework.jdbc.core.simple.JdbcClient;

/** 서버 조립이 사실을 넣고, 추출이 따라잡은 만큼 최근 대화를 줄이는가. */
@SpringBootTest(webEnvironment = WebEnvironment.RANDOM_PORT, properties = {
    "navi.turn.recent-rows=6", "navi.facts.recent-rows-min=2",
})
@Import({PostgresTestConfig.class, TurnStreamTest.Brain.class})
class FactInjectionTest {

    @LocalServerPort int port;
    @Autowired DeviceService devices;
    @Autowired FactStore facts;
    @Autowired JdbcClient jdbc;
    @Autowired ScriptedBrain brain;

    private void turn(TestClient client, String text) throws Exception {
        String id = client.start(text);
        client.until("turn.end");
        client.send(Map.of("type", "turn.played", "turn_id", id, "played_chars", 999));
        client.next("turn.confirmed");
    }

    @Test
    void factsAreInjectedAndTheWindowShrinksOnceExtractionCatchesUp() throws Exception {
        brain.reset();
        DeviceService.Registered device = devices.register();
        long user = device.identity().userId();
        facts.insert(user, "profile", "사용자의 동생 이름은 민준이다.", null, null);
        try (TestClient client = TestClient.connect(port, device.token())) {
            client.next("session.ready");
            turn(client, "하나");
            turn(client, "둘");
            turn(client, "셋");

            turn(client, "넷");
            // 추출 전: 커서가 0이라 창은 상한(6행)까지 다 쓴다.
            assertThat(brain.lastRequest.context()).contains("[기억]", "- 사용자의 동생 이름은 민준이다.");
            assertThat(brain.lastRequest.messages()).hasSize(6 + 1);

            long last = jdbc.sql("SELECT max(message_id) FROM turn_message WHERE user_id = ?")
                    .param(user).query(Long.class).single();
            facts.advanceCursor(user, last); // 추출이 지금까지를 다 읽었다
            turn(client, "다섯");

            // 추출 뒤: 사실이 대신하므로 창이 하한(2행)으로 준다.
            assertThat(brain.lastRequest.messages()).hasSize(2 + 1);
        }
    }

    @Test
    void withoutFactsTheContextStaysEmpty() throws Exception {
        brain.reset();
        try (TestClient client = TestClient.connect(port, devices.register().token())) {
            client.next("session.ready");
            turn(client, "안녕");

            assertThat(brain.lastRequest.context()).isEmpty();
        }
    }
}
