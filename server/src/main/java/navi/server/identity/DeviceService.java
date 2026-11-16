package navi.server.identity;

import java.util.Optional;
import org.springframework.jdbc.core.simple.JdbcClient;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

/**
 * 기기 등록과 인증(D23).
 *
 * <p>첫 접속에 익명 사용자 행과 기기 토큰을 만든다. 계정이 없는 동안의 한계: 사용자당 기기
 * 하나이고, 토큰을 잃으면 기억을 되찾지 못한다.
 */
@Service
public class DeviceService {

    /** 등록 결과. 토큰 원문은 이때 한 번만 나간다. */
    public record Registered(String token, DeviceIdentity identity) {}

    private final JdbcClient jdbc;

    public DeviceService(JdbcClient jdbc) {
        this.jdbc = jdbc;
    }

    @Transactional
    public Registered register() {
        long userId = jdbc.sql("INSERT INTO app_user DEFAULT VALUES RETURNING user_id")
                .query(Long.class).single();
        String token = DeviceTokens.generate();
        long deviceId = jdbc.sql(
                        "INSERT INTO device (user_id, token_hash) VALUES (?, ?) RETURNING device_id")
                .params(userId, DeviceTokens.hash(token))
                .query(Long.class).single();
        return new Registered(token, new DeviceIdentity(userId, deviceId));
    }

    /** 토큰의 주인. 없거나 폐기된 토큰이면 비어 있다. 마지막 접속 시각을 남긴다. */
    public Optional<DeviceIdentity> authenticate(String token) {
        return jdbc.sql("""
                        UPDATE device SET last_seen_at = now()
                         WHERE token_hash = ? AND revoked_at IS NULL
                        RETURNING user_id, device_id
                        """)
                .param(DeviceTokens.hash(token))
                .query((rs, n) -> new DeviceIdentity(rs.getLong("user_id"), rs.getLong("device_id")))
                .optional();
    }

    public Optional<DeviceIdentity> authenticateHeader(String authorizationHeader) {
        return DeviceTokens.fromAuthorizationHeader(authorizationHeader).flatMap(this::authenticate);
    }
}
