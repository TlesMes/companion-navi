package navi.server.gateway;

import org.springframework.boot.context.properties.ConfigurationProperties;
import org.springframework.boot.context.properties.bind.DefaultValue;

/**
 * 턴 게이트웨이 설정({@code navi.turn.*}).
 *
 * @param recentRows 조립에 넣는 최근 대화 행 수의 상한. 사실을 주입하는 동안은 추출이 따라잡은
 *     만큼 {@code navi.facts.recent-rows-min}까지 줄어든다(conductor.py {@code _recall_turns})
 * @param allowProactive 선제 턴({@code proactive}·{@code proactive_callback})을 받을지. 피드가
 *     서버에 없는 동안은 소재를 클라이언트가 보내야 하는데, 그건 임의 텍스트를 프레임에 싣는
 *     통로다. 그래서 기본은 거부다
 */
@ConfigurationProperties("navi.turn")
public record TurnProperties(
        @DefaultValue("20") int recentRows, @DefaultValue("false") boolean allowProactive) {}
