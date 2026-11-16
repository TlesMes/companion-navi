package navi.server.brain;

import org.springframework.boot.context.properties.ConfigurationProperties;
import org.springframework.boot.context.properties.bind.DefaultValue;

/**
 * 두뇌 설정({@code navi.brain.*}).
 *
 * <p>키는 설정 파일에 적지 않는다 — 환경 변수 {@code ANTHROPIC_API_KEY}에서 온다. 키는 서버
 * 것이고(service.md §2.2) 클라이언트는 볼 수 없다.
 *
 * @param vendor {@code echo}(키 없이 배선 확인) 또는 {@code anthropic}
 * @param maxTokens 출력 상한 — 고정이다. 클라이언트가 바꿀 수 없다(G7)
 * @param anthropicBaseUrl 비우면 SDK 기본값. 테스트가 가짜 서버를 가리킬 때 쓴다
 */
@ConfigurationProperties("navi.brain")
public record BrainProperties(
        @DefaultValue("echo") String vendor,
        @DefaultValue("claude-haiku-4-5") String model,
        @DefaultValue("1024") long maxTokens,
        @DefaultValue("") String anthropicApiKey,
        @DefaultValue("") String anthropicBaseUrl) {}
