package navi.server.gateway;

import static org.assertj.core.api.Assertions.assertThat;

import java.net.URI;
import java.net.http.HttpClient;
import java.net.http.WebSocket;
import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.UUID;
import java.util.concurrent.BlockingQueue;
import java.util.concurrent.CompletionStage;
import java.util.concurrent.LinkedBlockingQueue;
import java.util.concurrent.TimeUnit;
import tools.jackson.databind.JsonNode;
import tools.jackson.databind.ObjectMapper;

/** 테스트용 턴 스트림 클라이언트 — 진짜 소켓으로 붙어 받은 메시지를 차례로 내준다. */
final class TestClient implements WebSocket.Listener, AutoCloseable {

    private static final ObjectMapper MAPPER = new ObjectMapper();

    private final BlockingQueue<JsonNode> inbox = new LinkedBlockingQueue<>();
    private final StringBuilder partial = new StringBuilder();
    private WebSocket socket;

    static TestClient connect(int port, String token) {
        TestClient client = new TestClient();
        WebSocket.Builder builder = HttpClient.newHttpClient().newWebSocketBuilder();
        if (token != null) {
            builder.header("Authorization", "Bearer " + token);
        }
        client.socket = builder
                .buildAsync(URI.create("ws://localhost:" + port + "/v1/turn-stream"), client)
                .orTimeout(5, TimeUnit.SECONDS).join();
        return client;
    }

    @Override
    public CompletionStage<?> onText(WebSocket webSocket, CharSequence data, boolean last) {
        partial.append(data);
        if (last) {
            inbox.add(MAPPER.readTree(partial.toString()));
            partial.setLength(0);
        }
        webSocket.request(1);
        return null;
    }

    void send(Map<String, Object> message) {
        socket.sendText(MAPPER.writeValueAsString(message), true).join();
    }

    void sendRaw(String text) {
        socket.sendText(text, true).join();
    }

    /** 턴을 시작한다. 만든 turn_id를 돌려준다. */
    String start(String text) {
        String id = UUID.randomUUID().toString();
        start(id, "reactive", "navi", text);
        return id;
    }

    void start(String turnId, String kind, String persona, String text) {
        Map<String, Object> m = new LinkedHashMap<>();
        m.put("type", "turn.start");
        m.put("turn_id", turnId);
        m.put("kind", kind);
        m.put("persona", persona);
        m.put("text", text);
        send(m);
    }

    /** 다음 메시지. 5초 안에 안 오면 실패. */
    JsonNode next() throws InterruptedException {
        JsonNode msg = inbox.poll(5, TimeUnit.SECONDS);
        assertThat(msg).as("5초 안에 메시지가 와야 한다").isNotNull();
        return msg;
    }

    JsonNode next(String expectedType) throws InterruptedException {
        JsonNode msg = next();
        assertThat(msg.get("type").asString()).as(msg.toString()).isEqualTo(expectedType);
        return msg;
    }

    /** 지정한 type이 올 때까지 받아 모은다(그 메시지 포함). */
    List<JsonNode> until(String type) throws InterruptedException {
        List<JsonNode> out = new ArrayList<>();
        while (true) {
            JsonNode msg = next();
            out.add(msg);
            if (msg.get("type").asString().equals(type)) {
                return out;
            }
        }
    }

    boolean nothingMore(long millis) throws InterruptedException {
        return inbox.poll(millis, TimeUnit.MILLISECONDS) == null;
    }

    /** 인사 없이 끊는다 — 네트워크가 죽은 것처럼. */
    void abort() {
        socket.abort();
    }

    @Override
    public void close() {
        socket.abort();
    }
}
