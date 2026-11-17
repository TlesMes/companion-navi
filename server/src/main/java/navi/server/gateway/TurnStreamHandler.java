package navi.server.gateway;

import java.util.Map;
import java.util.Optional;
import java.util.UUID;
import java.util.concurrent.ConcurrentHashMap;
import navi.server.assembly.CharacterCard;
import navi.server.assembly.TurnKind;
import navi.server.brain.CancelToken;
import navi.server.gateway.ClientConnection.ActiveTurn;
import navi.server.identity.DeviceIdentity;
import navi.server.persona.PersonaRegistry;
import navi.server.quota.TurnGate;
import navi.server.turn.TurnStore;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.stereotype.Component;
import org.springframework.web.socket.CloseStatus;
import org.springframework.web.socket.TextMessage;
import org.springframework.web.socket.WebSocketSession;
import org.springframework.web.socket.handler.TextWebSocketHandler;
import tools.jackson.databind.JsonNode;
import tools.jackson.databind.json.JsonMapper;

/**
 * 턴 스트림({@code /v1/turn-stream}) — 클라이언트 메시지를 받아 나눠 준다.
 *
 * <p>여기는 검증과 배분만 한다. 턴 실행은 {@link TurnRunner}가 가상 스레드에서 한다 — 수신
 * 스레드를 붙들면 그 턴을 멈추라는 {@code turn.cancel}을 못 받는다.
 */
@Component
class TurnStreamHandler extends TextWebSocketHandler {

    static final String IDENTITY_ATTRIBUTE = "navi.identity";

    private static final Logger log = LoggerFactory.getLogger(TurnStreamHandler.class);
    /** 클라이언트 수치 보고의 크기 상한 — 임의 데이터를 쌓는 통로가 되지 않게. */
    private static final int MAX_METRICS_CHARS = 4096;
    private static final int MAX_MESSAGE_BYTES = 32 * 1024;

    private final Map<String, ClientConnection> connections = new ConcurrentHashMap<>();
    private final JsonMapper json;
    private final PersonaRegistry personas;
    private final TurnStore store;
    private final TurnRunner runner;
    private final TurnProperties props;
    private final TurnGate gate;

    TurnStreamHandler(
            JsonMapper json, PersonaRegistry personas, TurnStore store, TurnRunner runner,
            TurnProperties props, TurnGate gate) {
        this.gate = gate;
        this.json = json;
        this.personas = personas;
        this.store = store;
        this.runner = runner;
        this.props = props;
    }

    @Override
    public void afterConnectionEstablished(WebSocketSession session) {
        DeviceIdentity identity = (DeviceIdentity) session.getAttributes().get(IDENTITY_ATTRIBUTE);
        // 한 메시지의 크기 상한. 발화 길이 제한보다 넉넉하고, 큰 덩어리를 밀어 넣기엔 작다.
        session.setTextMessageSizeLimit(MAX_MESSAGE_BYTES);
        ClientConnection conn = new ClientConnection(identity, session, json);
        connections.put(session.getId(), conn);
        Map<String, Object> ready = Protocol.of("session.ready", null);
        ready.put("protocol", Protocol.VERSION);
        ready.put("user_id", identity.userId());
        ready.put("personas", personas.keys());
        // 한도를 알려 준다 — 클라이언트가 보내기 전에 걸러 낼 수 있게. 집행은 서버가 한다.
        ready.put("limits", Map.of(
                "max_text_chars", gate.limits().maxTextChars(),
                "turns_per_minute", gate.limits().turnsPerMinute(),
                "daily_tokens", gate.limits().dailyTokens()));
        conn.send(ready);
        log.info("연결 user={} device={}", identity.userId(), identity.deviceId());
    }

    @Override
    protected void handleTextMessage(WebSocketSession session, TextMessage message) {
        ClientConnection conn = connections.get(session.getId());
        if (conn == null) {
            return;
        }
        JsonNode msg;
        try {
            msg = json.readTree(message.getPayload());
        } catch (RuntimeException e) {
            conn.send(Protocol.error(null, Protocol.BAD_REQUEST, "JSON이 아닙니다"));
            return;
        }
        String type = msg.path("type").asString("");
        switch (type) {
            case "turn.start" -> onStart(conn, msg);
            case "turn.cancel" -> onCancel(conn, msg);
            case "turn.played" -> onPlayed(conn, msg);
            case "turn.metrics" -> onMetrics(conn, msg);
            default -> conn.send(Protocol.error(
                    textOrNull(msg, "turn_id"), Protocol.BAD_REQUEST, "알 수 없는 type"));
        }
    }

    private void onStart(ClientConnection conn, JsonNode msg) {
        String rawId = textOrNull(msg, "turn_id");
        Optional<UUID> turnId = uuid(rawId);
        if (turnId.isEmpty()) {
            conn.send(Protocol.error(rawId, Protocol.BAD_REQUEST, "turn_id는 UUID여야 합니다"));
            return;
        }
        String text = msg.path("text").asString("");
        if (text.isBlank()) {
            conn.send(Protocol.error(rawId, Protocol.BAD_REQUEST, "text가 비었습니다"));
            return;
        }
        Optional<TurnKind> kind = TurnKind.fromWire(msg.path("kind").asString("reactive"));
        if (kind.isEmpty()) {
            conn.send(Protocol.error(rawId, Protocol.BAD_REQUEST, "알 수 없는 kind"));
            return;
        }
        if (kind.get() != TurnKind.REACTIVE && !props.allowProactive()) {
            conn.send(Protocol.error(
                    rawId, Protocol.UNSUPPORTED_KIND, "선제 턴은 아직 받지 않습니다"));
            return;
        }
        String personaKey = textOrNull(msg, "persona");
        Optional<CharacterCard> card = personas.find(personaKey);
        if (card.isEmpty()) {
            conn.send(Protocol.error(rawId, Protocol.UNKNOWN_PERSONA, "모르는 persona"));
            return;
        }
        ActiveTurn turn = new ActiveTurn(turnId.get(), new CancelToken());
        if (!conn.tryBegin(turn)) {
            // 끼어들기는 turn.cancel을 먼저 보내고 turn.cancelled를 받은 뒤 새 턴을 보낸다.
            conn.send(Protocol.error(rawId, Protocol.BUSY, "진행 중인 턴이 있습니다"));
            return;
        }
        Thread.ofVirtual()
                .name("turn-" + rawId)
                .start(() -> runner.run(conn, turn, personaKey, card.get(), kind.get(), text));
    }

    private void onCancel(ClientConnection conn, JsonNode msg) {
        ActiveTurn turn = conn.activeTurn();
        // 이미 끝난 턴의 취소는 조용히 버린다 — 끝과 취소가 엇갈리는 건 정상이다.
        if (turn != null && turn.clientTurnId().toString().equals(textOrNull(msg, "turn_id"))) {
            turn.cancel().cancel();
        }
    }

    private void onPlayed(ClientConnection conn, JsonNode msg) {
        String rawId = textOrNull(msg, "turn_id");
        Optional<UUID> turnId = uuid(rawId);
        JsonNode played = msg.path("played_chars");
        if (turnId.isEmpty() || !played.isIntegralNumber() || played.asInt() < 0) {
            conn.send(Protocol.error(
                    rawId, Protocol.BAD_REQUEST, "turn_id와 played_chars(0 이상 정수)가 필요합니다"));
            return;
        }
        boolean confirmed = store.confirm(
                conn.identity().userId(), turnId.get(), played.asInt(),
                msg.path("interrupted").asBoolean(false));
        Map<String, Object> reply = Protocol.of("turn.confirmed", rawId);
        // false: 임시 상태의 턴이 아니다(이미 확정됐거나 폐기됐다). 클라이언트는 다시 보내지 않는다.
        reply.put("ok", confirmed);
        conn.send(reply);
    }

    private void onMetrics(ClientConnection conn, JsonNode msg) {
        Optional<UUID> turnId = uuid(textOrNull(msg, "turn_id"));
        JsonNode metrics = msg.path("metrics");
        if (turnId.isEmpty() || !metrics.isObject()) {
            return; // 수치 보고는 있으면 좋은 것 — 틀려도 턴을 방해하지 않는다
        }
        String body = metrics.toString();
        if (body.length() <= MAX_METRICS_CHARS) {
            store.recordClientMetrics(conn.identity().userId(), turnId.get(), body);
        }
    }

    @Override
    public void afterConnectionClosed(WebSocketSession session, CloseStatus status) {
        ClientConnection conn = connections.remove(session.getId());
        if (conn == null) {
            return;
        }
        // 도는 턴은 멈추고(실행 스레드가 폐기한다), 생성이 끝났는데 재생 보고가 안 온 턴은
        // 전문으로 확정한다.
        conn.markClosed();
        int confirmed = store.confirmUnreported(conn.identity().userId());
        log.info("연결 종료 user={} code={} 보고 없이 확정={}",
                conn.identity().userId(), status.getCode(), confirmed);
    }

    @Override
    public void handleTransportError(WebSocketSession session, Throwable exception) {
        ClientConnection conn = connections.get(session.getId());
        if (conn != null) {
            conn.markClosed();
        }
    }

    private static String textOrNull(JsonNode msg, String field) {
        JsonNode node = msg.path(field);
        return node.isString() ? node.asString() : null;
    }

    private static Optional<UUID> uuid(String raw) {
        try {
            return raw == null ? Optional.empty() : Optional.of(UUID.fromString(raw));
        } catch (IllegalArgumentException e) {
            return Optional.empty();
        }
    }
}
