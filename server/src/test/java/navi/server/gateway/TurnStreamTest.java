package navi.server.gateway;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;

import java.net.http.WebSocketHandshakeException;
import java.time.Duration;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.UUID;
import java.util.function.Supplier;
import navi.server.PostgresTestConfig;
import navi.server.assembly.Message;
import navi.server.brain.BrainException;
import navi.server.brain.BrainPort;
import navi.server.identity.DeviceService;
import org.junit.jupiter.api.AfterEach;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.boot.test.context.SpringBootTest.WebEnvironment;
import org.springframework.boot.test.context.TestConfiguration;
import org.springframework.boot.test.web.server.LocalServerPort;
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Import;
import org.springframework.context.annotation.Primary;
import org.springframework.jdbc.core.simple.JdbcClient;
import tools.jackson.databind.JsonNode;

/** 턴 스트림 — 진짜 서버에 진짜 소켓으로 붙는다. 두뇌만 대본이다. */
@SpringBootTest(webEnvironment = WebEnvironment.RANDOM_PORT)
@Import({PostgresTestConfig.class, TurnStreamTest.Brain.class})
class TurnStreamTest {

    @TestConfiguration(proxyBeanMethods = false)
    static class Brain {
        @Bean
        @Primary
        ScriptedBrain scriptedBrain() {
            return new ScriptedBrain();
        }
    }

    @LocalServerPort int port;
    @Autowired DeviceService devices;
    @Autowired JdbcClient jdbc;
    @Autowired ScriptedBrain brain;
    @Autowired BrainPort wired;

    String token;
    long user;
    TestClient client;

    @BeforeEach
    void setUp() throws Exception {
        assertThat(wired).isSameAs(brain);
        brain.reset();
        DeviceService.Registered r = devices.register();
        token = r.token();
        user = r.identity().userId();
        client = TestClient.connect(port, token);
        JsonNode ready = client.next("session.ready");
        assertThat(ready.get("protocol").asInt()).isEqualTo(1);
        assertThat(ready.get("user_id").asLong()).isEqualTo(user);
    }

    @AfterEach
    void tearDown() {
        client.close();
    }

    private String status(String turnId) {
        return jdbc.sql("SELECT status FROM turn WHERE user_id = ? AND client_turn_id = ?")
                .params(user, UUID.fromString(turnId)).query(String.class).single();
    }

    private List<String> stored() {
        return jdbc.sql("SELECT role || ':' || text FROM turn_message WHERE user_id = ? ORDER BY message_id")
                .param(user).query(String.class).list();
    }

    private static <T> void await(Supplier<T> actual, T expected) throws InterruptedException {
        long deadline = System.nanoTime() + Duration.ofSeconds(5).toNanos();
        while (!expected.equals(actual.get()) && System.nanoTime() < deadline) {
            Thread.sleep(20);
        }
        assertThat(actual.get()).isEqualTo(expected);
    }

    // ── 인증 ──────────────────────────────────────────────────────────────

    @Test
    void handshakeWithoutValidTokenIsRefused() {
        for (String bad : new String[] {null, "navi_dt_nope"}) {
            assertThatThrownBy(() -> TestClient.connect(port, bad))
                    .cause()
                    .isInstanceOfSatisfying(
                            WebSocketHandshakeException.class,
                            e -> assertThat(e.getResponse().statusCode()).isEqualTo(401));
        }
    }

    // ── 한 턴 ─────────────────────────────────────────────────────────────

    @Test
    void turnStreamsMoodThenBodyThenEnd() throws Exception {
        String id = client.start("나 왔어");

        List<JsonNode> msgs = client.until("turn.end");

        assertThat(msgs).extracting(m -> m.get("type").asString())
                .containsExactly("turn.accepted", "turn.mood", "turn.delta", "turn.delta", "turn.end");
        assertThat(msgs).allSatisfy(m -> assertThat(m.get("turn_id").asString()).isEqualTo(id));
        // 무드 태그는 본문에 없고 구조화 필드로 온다. 본문 앞 공백도 없다.
        assertThat(msgs.get(1).get("mood").asString()).isEqualTo("bright");
        assertThat(msgs.get(2).get("seq").asInt()).isZero();
        assertThat(msgs.get(2).get("text").asString()).isEqualTo("안녕");
        assertThat(msgs.get(3).get("seq").asInt()).isEqualTo(1);
        assertThat(msgs.get(3).get("text").asString()).isEqualTo(", 왔어?");
        JsonNode end = msgs.get(4);
        assertThat(end.get("last_seq").asInt()).isEqualTo(1);
        assertThat(end.get("chars").asInt()).isEqualTo("안녕, 왔어?".length());
        assertThat(end.get("usage").get("input_tokens").asLong()).isEqualTo(50);
        assertThat(end.get("usage").get("output_tokens").asLong()).isEqualTo(10);

        assertThat(status(id)).isEqualTo("provisional");
        assertThat(stored()).containsExactly("user:나 왔어", "assistant:안녕, 왔어?");
    }

    @Test
    void serverAssemblesTheRequestFromCardAndHistory() throws Exception {
        client.start("나 왔어");
        client.until("turn.end");

        client.start("오늘 피곤해");
        client.until("turn.end");

        // 클라이언트는 발화만 보냈다 — 카드와 이력은 서버가 붙였다.
        assertThat(brain.lastRequest.system()).startsWith("너는 '나비'다.");
        assertThat(brain.lastRequest.context()).isEmpty();
        assertThat(brain.lastRequest.messages()).containsExactly(
                Message.user("나 왔어"), Message.assistant("안녕, 왔어?"), Message.user("오늘 피곤해"));
    }

    // ── 재생 보고 ─────────────────────────────────────────────────────────

    @Test
    void playbackReportConfirmsAndTruncatesTheTurn() throws Exception {
        String id = client.start("나 왔어");
        client.until("turn.end");

        Map<String, Object> played = new LinkedHashMap<>();
        played.put("type", "turn.played");
        played.put("turn_id", id);
        played.put("played_chars", 2);
        played.put("interrupted", true);
        client.send(played);

        assertThat(client.next("turn.confirmed").get("ok").asBoolean()).isTrue();
        assertThat(status(id)).isEqualTo("confirmed");
        assertThat(stored()).containsExactly("user:나 왔어", "assistant:안녕");
        // 같은 보고가 또 와도 다시 자르지 않는다.
        client.send(played);
        assertThat(client.next("turn.confirmed").get("ok").asBoolean()).isFalse();
    }

    @Test
    void clientMetricsAreStoredOnTheTurn() throws Exception {
        String id = client.start("나 왔어");
        client.until("turn.end");

        client.send(Map.of("type", "turn.metrics", "turn_id", id, "metrics", Map.of("ttfa_ms", 812)));

        await(() -> jdbc.sql("SELECT coalesce((client_metrics->>'ttfa_ms')::int, -1) FROM turn WHERE client_turn_id = ?")
                .param(UUID.fromString(id)).query(Integer.class).single(), 812);
    }

    // ── 취소·끊김 (D22) ───────────────────────────────────────────────────

    @Test
    void cancelDiscardsTheTurnAndFreesTheConnection() throws Exception {
        brain.stallAfter = 2;
        String id = client.start("길게 말해 줘");
        client.next("turn.accepted");
        client.next("turn.mood");
        client.next("turn.delta");
        assertThat(brain.awaitStalled()).isTrue();

        client.send(Map.of("type", "turn.cancel", "turn_id", id));

        client.next("turn.cancelled");
        assertThat(brain.awaitCancelSeen()).isTrue();
        assertThat(status(id)).isEqualTo("discarded");
        // 폐기된 턴은 사용자 발화까지 없던 일이다.
        assertThat(stored()).isEmpty();
        // 쓴 토큰은 남는다.
        assertThat(jdbc.sql("SELECT input_tokens FROM turn WHERE client_turn_id = ?")
                .param(UUID.fromString(id)).query(Long.class).single()).isEqualTo(50);

        brain.reset();
        client.start("다시");
        assertThat(client.until("turn.end")).isNotEmpty();
    }

    @Test
    void secondTurnWhileOneIsRunningIsRejectedAsBusy() throws Exception {
        brain.stallAfter = 1;
        String first = client.start("하나");
        client.next("turn.accepted");
        assertThat(brain.awaitStalled()).isTrue();

        String second = client.start("둘");

        JsonNode error = client.next("turn.error");
        assertThat(error.get("turn_id").asString()).isEqualTo(second);
        assertThat(error.get("code").asString()).isEqualTo("busy");
        client.send(Map.of("type", "turn.cancel", "turn_id", first));
        client.next("turn.cancelled");
    }

    @Test
    void droppedConnectionStopsTheBrainAndDiscardsTheTurn() throws Exception {
        brain.stallAfter = 2;
        String id = client.start("길게 말해 줘");
        client.next("turn.accepted");
        assertThat(brain.awaitStalled()).isTrue();

        client.abort();

        // 연결이 죽으면 두뇌 호출을 멈춘다 — 버릴 출력에 돈을 내지 않는다.
        assertThat(brain.awaitCancelSeen()).isTrue();
        await(() -> status(id), "discarded");
        assertThat(stored()).isEmpty();
    }

    @Test
    void finishedTurnWithoutPlaybackReportIsConfirmedWholeOnDisconnect() throws Exception {
        String id = client.start("나 왔어");
        client.until("turn.end");

        client.abort();

        await(() -> status(id), "confirmed");
        assertThat(stored()).containsExactly("user:나 왔어", "assistant:안녕, 왔어?");
    }

    // ── 거부 ──────────────────────────────────────────────────────────────

    @Test
    void invalidRequestsAreRejectedWithoutCreatingATurn() throws Exception {
        String id = UUID.randomUUID().toString();

        client.start(id, "proactive", "navi", "GTA 6 출시가 미뤄졌다.");
        assertThat(client.next("turn.error").get("code").asString()).isEqualTo("unsupported_kind");

        client.start(id, "reactive", "no-such-persona", "안녕");
        assertThat(client.next("turn.error").get("code").asString()).isEqualTo("unknown_persona");

        client.start(id, "reactive", "navi", "   ");
        assertThat(client.next("turn.error").get("code").asString()).isEqualTo("bad_request");

        client.start("not-a-uuid", "reactive", "navi", "안녕");
        assertThat(client.next("turn.error").get("code").asString()).isEqualTo("bad_request");

        client.start(id, "weird", "navi", "안녕");
        assertThat(client.next("turn.error").get("code").asString()).isEqualTo("bad_request");

        client.sendRaw("이건 JSON이 아니다");
        assertThat(client.next("turn.error").get("code").asString()).isEqualTo("bad_request");

        assertThat(jdbc.sql("SELECT count(*) FROM turn WHERE user_id = ?")
                .param(user).query(Long.class).single()).isZero();
        assertThat(brain.lastRequest).isNull();
    }

    @Test
    void sameTurnIdTwiceIsRejected() throws Exception {
        String id = client.start("나 왔어");
        client.until("turn.end");

        client.start(id, "reactive", "navi", "나 왔어");

        assertThat(client.next("turn.error").get("code").asString()).isEqualTo("duplicate_turn");
        assertThat(stored()).hasSize(2);
    }

    @Test
    void brainFailureEndsTheTurnWithAnErrorAndLeavesNoTrace() throws Exception {
        brain.failure = new BrainException.Unavailable("벤더 장애", null);
        String id = client.start("나 왔어");

        client.next("turn.accepted");
        JsonNode error = client.next("turn.error");

        assertThat(error.get("code").asString()).isEqualTo("brain_unavailable");
        assertThat(status(id)).isEqualTo("discarded");
        assertThat(stored()).isEmpty();
        // 실패한 뒤에도 연결은 살아 있고 다음 턴을 받는다.
        brain.reset();
        client.start("다시");
        assertThat(client.until("turn.end")).isNotEmpty();
    }
}
