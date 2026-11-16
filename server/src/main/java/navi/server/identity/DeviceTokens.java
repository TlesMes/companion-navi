package navi.server.identity;

import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.security.NoSuchAlgorithmException;
import java.security.SecureRandom;
import java.util.Base64;
import java.util.HexFormat;
import java.util.Optional;

/**
 * 기기 토큰 만들기·해시.
 *
 * <p>토큰은 256비트 난수다. 서버는 원문을 저장하지 않고 SHA-256만 둔다 — DB가 새도 토큰으로
 * 쓸 수 없다. 비밀번호가 아니라 충분히 긴 난수라 솔트나 느린 해시는 필요 없다.
 */
public final class DeviceTokens {

    /** 로그나 저장소에서 눈에 띄게 하는 접두사 — 유출 탐지에 쓴다. */
    public static final String PREFIX = "navi_dt_";

    private static final SecureRandom RANDOM = new SecureRandom();
    private static final String BEARER = "Bearer ";

    private DeviceTokens() {}

    public static String generate() {
        byte[] bytes = new byte[32];
        RANDOM.nextBytes(bytes);
        return PREFIX + Base64.getUrlEncoder().withoutPadding().encodeToString(bytes);
    }

    public static String hash(String token) {
        try {
            byte[] digest = MessageDigest.getInstance("SHA-256")
                    .digest(token.getBytes(StandardCharsets.UTF_8));
            return HexFormat.of().formatHex(digest);
        } catch (NoSuchAlgorithmException e) {
            throw new IllegalStateException(e);
        }
    }

    /** {@code Authorization: Bearer <토큰>}에서 토큰을 꺼낸다. 모양이 다르면 비어 있다. */
    public static Optional<String> fromAuthorizationHeader(String header) {
        if (header == null || !header.regionMatches(true, 0, BEARER, 0, BEARER.length())) {
            return Optional.empty();
        }
        String token = header.substring(BEARER.length()).strip();
        return token.startsWith(PREFIX) ? Optional.of(token) : Optional.empty();
    }
}
