package navi.server.brain;

import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.boot.context.properties.EnableConfigurationProperties;
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;

@Configuration
@EnableConfigurationProperties(BrainProperties.class)
public class BrainConfiguration {

    private static final Logger log = LoggerFactory.getLogger(BrainConfiguration.class);

    @Bean
    BrainPort brain(BrainProperties props) {
        return switch (props.vendor()) {
            case "echo" -> {
                log.warn("두뇌가 echo다 — 실제 모델을 부르지 않는다(navi.brain.vendor)");
                yield new EchoBrain();
            }
            case "anthropic" -> {
                if (props.anthropicApiKey().isBlank()) {
                    // 첫 턴에서 터지는 것보다 부팅에서 터지는 게 낫다.
                    throw new IllegalStateException(
                            "navi.brain.vendor=anthropic인데 키가 없습니다 — 환경 변수 "
                                    + "ANTHROPIC_API_KEY를 설정하세요");
                }
                log.info("두뇌: anthropic, model={}", props.model());
                yield AnthropicBrain.create(
                        props.anthropicApiKey(), props.anthropicBaseUrl(), props.maxTokens());
            }
            default -> throw new IllegalStateException(
                    "알 수 없는 navi.brain.vendor: " + props.vendor() + " (echo | anthropic)");
        };
    }
}
