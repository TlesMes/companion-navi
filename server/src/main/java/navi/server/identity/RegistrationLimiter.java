package navi.server.identity;

import java.nio.charset.StandardCharsets;
import java.security.GeneralSecurityException;
import java.security.SecureRandom;
import java.util.HexFormat;
import java.util.OptionalLong;
import javax.crypto.Mac;
import javax.crypto.spec.SecretKeySpec;
import org.springframework.boot.context.properties.EnableConfigurationProperties;
import org.springframework.jdbc.core.simple.JdbcClient;
import org.springframework.stereotype.Component;
import org.springframework.transaction.annotation.Transactional;

/**
 * 같은 접속 주소의 기기 등록을 시간당 N개로 묶는다.
 *
 * <p>계정이 없는 동안(D23) 한도(G7)는 사용자 행에 걸리고 사용자는 토큰을 새로 받으면 생긴다.
 * 이걸 막지 않으면 일일 토큰 한도는 등록 한 번으로 풀린다. 주소는 같은 집·같은 회사가 공유하므로
 * 강한 신원이 아니다 — 남용의 비용을 올릴 뿐이다. 계정이 붙으면 계정 단위 한도가 이 자리를 맡는다.
 */
@Component
@EnableConfigurationProperties(RegistrationProperties.class)
public class RegistrationLimiter {

    private static final int WINDOW_SECONDS = 3600;

    private final JdbcClient jdbc;
    private final RegistrationProperties props;
    private final byte[] secret;

    public RegistrationLimiter(JdbcClient jdbc, RegistrationProperties props) {
        this.jdbc = jdbc;
        this.props = props;
        if (props.sourceSecret().isBlank()) {
            this.secret = new byte[32];
            new SecureRandom().nextBytes(this.secret);
        } else {
            this.secret = props.sourceSecret().getBytes(StandardCharsets.UTF_8);
        }
    }

    public boolean trustForwardedFor() {
        return props.trustForwardedFor();
    }

    /**
     * 이 주소의 등록을 하나 받는다.
     *
     * @return 받았으면 비어 있고, 넘쳤으면 다시 될 때까지의 초
     */
    @Transactional
    public OptionalLong admit(String source) {
        String hash = hmac(source);
        // 같은 주소의 동시 등록을 줄 세운다 — 세고 넣는 사이에 둘이 함께 통과하지 않게.
        jdbc.sql("SELECT pg_advisory_xact_lock(hashtextextended(?, 0))").param(hash)
                .query((rs, n) -> 0).list();
        jdbc.sql("DELETE FROM device_registration WHERE created_at < now() - interval '1 day'")
                .update();
        var window = jdbc.sql("""
                        SELECT count(*) AS n,
                               coalesce(ceil(extract(epoch FROM
                                   min(created_at) + make_interval(secs => ?) - now())), 0) AS wait
                          FROM device_registration
                         WHERE source_hash = ? AND created_at > now() - make_interval(secs => ?)
                        """)
                .params(WINDOW_SECONDS, hash, WINDOW_SECONDS)
                .query((rs, n) -> new long[] {rs.getLong("n"), rs.getLong("wait")})
                .single();
        if (window[0] >= props.perSourcePerHour()) {
            return OptionalLong.of(Math.max(1, window[1]));
        }
        jdbc.sql("INSERT INTO device_registration (source_hash) VALUES (?)").param(hash).update();
        return OptionalLong.empty();
    }

    private String hmac(String source) {
        try {
            Mac mac = Mac.getInstance("HmacSHA256");
            mac.init(new SecretKeySpec(secret, "HmacSHA256"));
            return HexFormat.of().formatHex(mac.doFinal(source.getBytes(StandardCharsets.UTF_8)));
        } catch (GeneralSecurityException e) {
            throw new IllegalStateException(e);
        }
    }
}
