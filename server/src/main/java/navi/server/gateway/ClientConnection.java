package navi.server.gateway;

import java.io.IOException;
import java.nio.ByteBuffer;
import java.time.Instant;
import java.util.Map;
import java.util.UUID;
import java.util.concurrent.atomic.AtomicReference;
import navi.server.brain.CancelToken;
import navi.server.identity.DeviceIdentity;
import org.springframework.web.socket.CloseStatus;
import org.springframework.web.socket.PingMessage;
import org.springframework.web.socket.TextMessage;
import org.springframework.web.socket.WebSocketSession;
import org.springframework.web.socket.handler.ConcurrentWebSocketSessionDecorator;
import tools.jackson.databind.json.JsonMapper;

/**
 * 연결 하나 — 누구의 연결인지, 지금 도는 턴이 무엇인지, 그리고 보내는 손.
 *
 * <p>턴은 가상 스레드에서 돌고 메시지 수신은 다른 스레드라 한 소켓에 둘이 동시에 쓴다. 그래서
 * 전송은 {@link ConcurrentWebSocketSessionDecorator}를 거친다.
 */
final class ClientConnection {

    /** 진행 중인 턴. 연결당 하나다. */
    record ActiveTurn(UUID clientTurnId, CancelToken cancel) {}

    private static final int SEND_TIME_LIMIT_MS = 10_000;
    private static final int SEND_BUFFER_BYTES = 512 * 1024;

    private final DeviceIdentity identity;
    private final WebSocketSession session;
    private final JsonMapper json;
    private final AtomicReference<ActiveTurn> active = new AtomicReference<>();
    private volatile boolean closed;
    /** 마지막으로 살아 있다는 신호(pong)를 받은 시각. 연결 직후는 연결 시각이다. */
    private volatile Instant lastPong = Instant.now();

    ClientConnection(DeviceIdentity identity, WebSocketSession raw, JsonMapper json) {
        this.identity = identity;
        this.session =
                new ConcurrentWebSocketSessionDecorator(raw, SEND_TIME_LIMIT_MS, SEND_BUFFER_BYTES);
        this.json = json;
    }

    DeviceIdentity identity() {
        return identity;
    }

    /** 턴 자리를 잡는다. 이미 도는 턴이 있으면 false. */
    boolean tryBegin(ActiveTurn turn) {
        return active.compareAndSet(null, turn);
    }

    void end(ActiveTurn turn) {
        active.compareAndSet(turn, null);
    }

    ActiveTurn activeTurn() {
        return active.get();
    }

    boolean isClosed() {
        return closed;
    }

    /** 연결이 닫혔다. 도는 턴이 있으면 멈춘다 — 끊기면 그 턴은 폐기다(D22). */
    void markClosed() {
        closed = true;
        ActiveTurn turn = active.get();
        if (turn != null) {
            turn.cancel().cancel();
        }
    }

    String sessionId() {
        return session.getId();
    }

    void markPong(Instant at) {
        lastPong = at;
    }

    Instant lastPong() {
        return lastPong;
    }

    /** 살아 있는지 묻는다(WebSocket ping). 못 보냈으면 false. */
    boolean ping() {
        if (closed || !session.isOpen()) {
            return false;
        }
        try {
            session.sendMessage(new PingMessage(ByteBuffer.wrap(new byte[] {'n'})));
            return true;
        } catch (IOException | RuntimeException e) {
            return false;
        }
    }

    /** 연결을 닫는다 — 닫힘 처리(턴 폐기 등)는 핸들러의 afterConnectionClosed가 한다. */
    void close(CloseStatus status) {
        try {
            session.close(status);
        } catch (IOException | RuntimeException e) {
            // 이미 죽은 연결이다. 닫힘 처리는 전송 오류 경로가 맡는다.
        }
    }

    /** 메시지를 보낸다. 못 보냈으면(연결이 죽었으면) false. */
    boolean send(Map<String, ?> message) {
        if (closed || !session.isOpen()) {
            return false;
        }
        try {
            session.sendMessage(new TextMessage(json.writeValueAsString(message)));
            return true;
        } catch (IOException | RuntimeException e) {
            return false;
        }
    }
}
