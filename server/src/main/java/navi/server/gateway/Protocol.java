package navi.server.gateway;

import java.util.LinkedHashMap;
import java.util.Map;
import navi.server.brain.Usage;

/** 턴 스트림 프로토콜의 서버 → 클라이언트 메시지. 명세는 docs/design/protocol.md. */
final class Protocol {

    static final int VERSION = 1;

    // turn.error의 code
    static final String BAD_REQUEST = "bad_request";
    static final String UNSUPPORTED_KIND = "unsupported_kind";
    static final String UNKNOWN_PERSONA = "unknown_persona";
    static final String BUSY = "busy";
    static final String NOT_ACTIVE = "not_active";
    static final String DUPLICATE_TURN = "duplicate_turn";
    static final String BRAIN_UNAVAILABLE = "brain_unavailable";
    static final String INTERNAL = "internal";

    private Protocol() {}

    static Map<String, Object> of(String type, String turnId) {
        Map<String, Object> m = new LinkedHashMap<>();
        m.put("type", type);
        if (turnId != null) {
            m.put("turn_id", turnId);
        }
        return m;
    }

    static Map<String, Object> error(String turnId, String code, String message) {
        Map<String, Object> m = of("turn.error", turnId);
        m.put("code", code);
        m.put("message", message);
        return m;
    }

    static Map<String, Object> usage(Usage usage) {
        Map<String, Object> m = new LinkedHashMap<>();
        m.put("input_tokens", usage.totalInputTokens());
        m.put("output_tokens", usage.outputTokens());
        m.put("cache_read_tokens", usage.cacheReadTokens());
        return m;
    }
}
