package navi.server.quota;

import org.springframework.boot.context.properties.ConfigurationProperties;
import org.springframework.boot.context.properties.bind.DefaultValue;

/**
 * 반응 턴의 한도({@code navi.quota.*}) — service.md §10, G7.
 *
 * <p><b>값은 임시다(D24).</b> 넉넉하게 잡은 시작값이고, 좋은 값은 사용 로그가 쌓인 뒤 정한다.
 * 그래서 코드가 아니라 설정에 있다.
 *
 * @param maxTextChars 발화 한 번의 길이 상한(코드 포인트). 말로 500자는 2분쯤 — 그보다 길면
 *     음성 발화가 아니라 붙여 넣은 글이다
 * @param turnsPerMinute 최근 60초 동안 시작할 수 있는 턴 수
 * @param dailyTokens 하루에 쓸 수 있는 토큰(입력 + 출력). 턴당 약 2.7K 기준 110턴쯤
 */
@ConfigurationProperties("navi.quota")
public record QuotaProperties(
        @DefaultValue("500") int maxTextChars,
        @DefaultValue("20") int turnsPerMinute,
        @DefaultValue("300000") long dailyTokens) {}
