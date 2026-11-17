package navi.server.memory;

import org.springframework.boot.context.properties.ConfigurationProperties;
import org.springframework.boot.context.properties.bind.DefaultValue;

/**
 * 서버 기억 설정({@code navi.facts.*}). 값은 단독 모드(config.yaml {@code memory.facts})와 같다.
 *
 * @param enabled 사실 주입과 추출 작업. 끄면 최근 대화는 늘 상한 길이다
 * @param maxInjected 한 턴에 넣는 사실 상한(신상 먼저, 나머지는 최신 순). 0이면 주입만 끈다
 * @param minNewUserTurns 확정된 새 사용자 발화가 이만큼 쌓이면 추출 작업을 하나 넣는다
 * @param maxRowsPerBatch 추출 한 번에 보이는 대화 행 상한
 * @param maxKnownFacts 추출기에 "이미 아는 사실"로 보여 주는 상한
 * @param maxFactsPerBatch 추출 한 번에 받아들이는 사실 상한
 * @param recentRowsMin 최근 대화 창의 하한. 상한은 {@code navi.turn.recent-rows}
 */
@ConfigurationProperties("navi.facts")
public record FactsProperties(
        @DefaultValue("true") boolean enabled,
        @DefaultValue("50") int maxInjected,
        @DefaultValue("4") int minNewUserTurns,
        @DefaultValue("40") int maxRowsPerBatch,
        @DefaultValue("80") int maxKnownFacts,
        @DefaultValue("10") int maxFactsPerBatch,
        @DefaultValue("8") int recentRowsMin) {

    /** 주입이 실제로 켜져 있나 — 창을 줄여도 되는 근거가 이것뿐이다. */
    public boolean injecting() {
        return enabled && maxInjected > 0;
    }
}
