package navi.server.assembly;

import java.util.Arrays;
import java.util.Optional;

/**
 * 턴의 성격 — 트리거를 어떻게 감쌀지 가른다.
 *
 * <p>카드(system)는 모든 종류에서 같고 차이는 마지막 메시지의 프레임에만 있다.
 */
public enum TurnKind {
    /** 사용자 발화에 답한다. 트리거는 진짜 발화라 감싸지 않는다. */
    REACTIVE("reactive"),
    /** 먼저 말을 건다. 트리거는 밖에서 온 소식이다. */
    PROACTIVE("proactive"),
    /** 먼저 말을 걸되 소재가 사용자 자신이 전에 한 말이다. */
    PROACTIVE_CALLBACK("proactive_callback");

    private final String wire;

    TurnKind(String wire) {
        this.wire = wire;
    }

    /** 프로토콜·저장소에 쓰는 이름 — Python {@code TurnKind.value}와 같다. */
    public String wire() {
        return wire;
    }

    public static Optional<TurnKind> fromWire(String wire) {
        return Arrays.stream(values()).filter(k -> k.wire.equals(wire)).findFirst();
    }
}
