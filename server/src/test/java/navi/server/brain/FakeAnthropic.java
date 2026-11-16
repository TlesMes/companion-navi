package navi.server.brain;

import com.sun.net.httpserver.HttpExchange;
import com.sun.net.httpserver.HttpServer;
import java.io.IOException;
import java.io.OutputStream;
import java.net.InetSocketAddress;
import java.nio.charset.StandardCharsets;
import java.util.List;
import java.util.concurrent.CountDownLatch;
import java.util.concurrent.Executors;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.atomic.AtomicReference;

/**
 * 가짜 Anthropic — Messages API의 SSE 스트림을 흉내 내는 로컬 HTTP 서버.
 *
 * <p>어댑터를 진짜 SDK·진짜 HTTP로 돌리되 과금과 네트워크만 뺀다. 받은 요청 본문을 남겨 두어
 * 어댑터가 실제로 무엇을 보냈는지 볼 수 있다.
 */
final class FakeAnthropic implements AutoCloseable {

    private final HttpServer server;
    final AtomicReference<String> lastRequestBody = new AtomicReference<>();
    final AtomicReference<String> lastApiKey = new AtomicReference<>();
    /** 응답 중 끊긴 걸 서버가 알아챘는지 — 취소가 벤더 연결을 실제로 닫는지 본다. */
    final CountDownLatch clientGone = new CountDownLatch(1);

    private volatile int status = 200;
    private volatile List<String> tokens = List.of();
    private volatile long stallAfterTokens = -1;
    private volatile CountDownLatch stalled = new CountDownLatch(1);

    FakeAnthropic() throws IOException {
        server = HttpServer.create(new InetSocketAddress("127.0.0.1", 0), 0);
        server.createContext("/v1/messages", this::handle);
        server.setExecutor(Executors.newVirtualThreadPerTaskExecutor());
        server.start();
    }

    String baseUrl() {
        return "http://127.0.0.1:" + server.getAddress().getPort();
    }

    FakeAnthropic respondWith(String... tokens) {
        this.status = 200;
        this.tokens = List.of(tokens);
        return this;
    }

    FakeAnthropic failWith(int status) {
        this.status = status;
        return this;
    }

    /** n개를 보낸 뒤 멈춰 선다 — 그 사이 클라이언트가 취소하는 상황을 만든다. */
    FakeAnthropic stallAfter(long n) {
        this.stallAfterTokens = n;
        this.stalled = new CountDownLatch(1);
        return this;
    }

    boolean awaitStalled() throws InterruptedException {
        return stalled.await(5, TimeUnit.SECONDS);
    }

    private void handle(HttpExchange ex) throws IOException {
        // SDK는 키 모양에 따라 x-api-key 또는 Authorization: Bearer로 보낸다 — 어느 쪽이든 받는다.
        String apiKey = ex.getRequestHeaders().getFirst("x-api-key");
        String bearer = ex.getRequestHeaders().getFirst("Authorization");
        lastApiKey.set(apiKey != null ? apiKey : bearer == null ? null : bearer.replace("Bearer ", ""));
        lastRequestBody.set(new String(ex.getRequestBody().readAllBytes(), StandardCharsets.UTF_8));
        if (status != 200) {
            byte[] body = ("{\"type\":\"error\",\"error\":{\"type\":\"api_error\","
                    + "\"message\":\"fake\"}}").getBytes(StandardCharsets.UTF_8);
            ex.getResponseHeaders().add("content-type", "application/json");
            ex.sendResponseHeaders(status, body.length);
            ex.getResponseBody().write(body);
            ex.close();
            return;
        }
        ex.getResponseHeaders().add("content-type", "text/event-stream");
        ex.sendResponseHeaders(200, 0);
        try (OutputStream out = ex.getResponseBody()) {
            send(out, "message_start", "{\"type\":\"message_start\",\"message\":{\"id\":\"msg_fake\","
                    + "\"type\":\"message\",\"role\":\"assistant\",\"model\":\"fake\",\"content\":[],"
                    + "\"stop_reason\":null,\"stop_sequence\":null,\"usage\":{\"input_tokens\":7,"
                    + "\"cache_creation_input_tokens\":11,\"cache_read_input_tokens\":13,"
                    + "\"output_tokens\":1}}}");
            send(out, "content_block_start", "{\"type\":\"content_block_start\",\"index\":0,"
                    + "\"content_block\":{\"type\":\"text\",\"text\":\"\"}}");
            long sent = 0;
            for (String token : tokens) {
                if (sent == stallAfterTokens) {
                    stalled.countDown();
                    // 클라이언트가 끊을 때까지 주기적으로 써 본다 — 끊기면 쓰기가 실패한다.
                    while (true) {
                        Thread.sleep(50);
                        send(out, "ping", "{\"type\":\"ping\"}");
                    }
                }
                send(out, "content_block_delta", "{\"type\":\"content_block_delta\",\"index\":0,"
                        + "\"delta\":{\"type\":\"text_delta\",\"text\":" + json(token) + "}}");
                sent++;
            }
            send(out, "content_block_stop", "{\"type\":\"content_block_stop\",\"index\":0}");
            send(out, "message_delta", "{\"type\":\"message_delta\",\"delta\":{\"stop_reason\":"
                    + "\"end_turn\",\"stop_sequence\":null},\"usage\":{\"output_tokens\":17}}");
            send(out, "message_stop", "{\"type\":\"message_stop\"}");
        } catch (IOException e) {
            clientGone.countDown();
        } catch (InterruptedException e) {
            Thread.currentThread().interrupt();
        }
    }

    private static void send(OutputStream out, String event, String data) throws IOException {
        out.write(("event: " + event + "\ndata: " + data + "\n\n").getBytes(StandardCharsets.UTF_8));
        out.flush();
    }

    private static String json(String s) {
        return "\"" + s.replace("\\", "\\\\").replace("\"", "\\\"").replace("\n", "\\n") + "\"";
    }

    @Override
    public void close() {
        server.stop(0);
    }
}
