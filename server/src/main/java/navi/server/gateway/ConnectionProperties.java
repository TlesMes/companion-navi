package navi.server.gateway;

import org.springframework.boot.context.properties.ConfigurationProperties;
import org.springframework.boot.context.properties.bind.DefaultValue;

/**
 * 연결 유지({@code navi.connection.*}).
 *
 * @param pingIntervalMs 서버가 살아 있는지 묻는 간격
 * @param pongTimeoutMs 이만큼 대답(pong)이 없으면 연결이 죽은 것으로 보고 닫는다. 닫히면 진행 중인
 *     턴은 폐기된다(D22) — 끊긴 줄 모르고 두뇌를 계속 부르지 않게
 */
@ConfigurationProperties("navi.connection")
public record ConnectionProperties(
        @DefaultValue("20000") long pingIntervalMs, @DefaultValue("60000") long pongTimeoutMs) {}
