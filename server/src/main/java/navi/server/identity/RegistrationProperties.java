package navi.server.identity;

import org.springframework.boot.context.properties.ConfigurationProperties;
import org.springframework.boot.context.properties.bind.DefaultValue;

/**
 * 기기 등록 제한({@code navi.registration.*}).
 *
 * @param perSourcePerHour 같은 접속 주소에서 한 시간에 받을 수 있는 등록 수. 임시값(D24와 같은 취지)
 * @param trustForwardedFor 앞단 프록시가 붙인 {@code X-Forwarded-For}를 믿을지. 프록시 없이
 *     열려 있으면 누구나 그 헤더를 지어내 제한을 비껴가므로 기본은 믿지 않는다
 * @param sourceSecret 접속 주소 HMAC의 비밀값. 비우면 부팅 때 무작위로 만든다 — 재시작하면
 *     기록이 이어지지 않지만(한 시간짜리 창이라 손해가 작다) 주소를 되돌려 볼 수도 없다.
 *     서버가 여러 대면 같은 값을 줘야 한다
 */
@ConfigurationProperties("navi.registration")
public record RegistrationProperties(
        @DefaultValue("5") int perSourcePerHour,
        @DefaultValue("false") boolean trustForwardedFor,
        @DefaultValue("") String sourceSecret) {}
