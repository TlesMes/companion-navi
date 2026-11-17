package navi.server.crypto;

import java.nio.ByteBuffer;
import java.nio.charset.StandardCharsets;
import java.security.GeneralSecurityException;
import java.security.SecureRandom;
import java.util.Base64;
import java.util.Map;
import java.util.concurrent.ConcurrentHashMap;
import javax.crypto.Cipher;
import javax.crypto.SecretKey;
import javax.crypto.spec.GCMParameterSpec;
import javax.crypto.spec.SecretKeySpec;
import org.springframework.boot.context.properties.EnableConfigurationProperties;
import org.springframework.jdbc.core.simple.JdbcClient;
import org.springframework.stereotype.Component;

/**
 * 본문 필드 암호화(service.md §10, G8 ②) — 봉투 암호화.
 *
 * <pre>
 *  마스터 키(서버 설정) ─감쌈─▶ 사용자 데이터 키(app_user.data_key) ─암호화─▶ 대화·사실 본문
 * </pre>
 *
 * <p>사용자마다 키를 따로 두는 이유: 한 사용자의 키를 지우면 그 사람의 본문만 되돌릴 수 없게 된다
 * (삭제 경로, G8 ④). DB 백업에 남은 행까지 지워진다.
 *
 * <p>알고리즘은 AES-256-GCM, 필드마다 무작위 12바이트 nonce. <b>인증 데이터에 사용자 ID와 칸 이름을
 * 넣는다</b> — 암호문을 다른 사용자의 행이나 다른 칸으로 옮겨 붙이면 복호화가 실패한다.
 *
 * <p>저장 형식은 {@code enc:v1:<base64(nonce || 암호문+태그)>}. 접두사가 없는 값은 이 기능 전에
 * 쌓인 평문으로 보고 그대로 돌려준다 — 부팅 때 {@link LegacyPlaintextEncryptor}가 암호화한다.
 *
 * <p>막지 못하는 것: 서버가 조립하므로 서버 프로세스와 운영자는 본문을 본다(service.md §15.3 —
 * E2EE 불가). 이 기능이 막는 건 DB·백업만 새는 경우다.
 */
@Component
@EnableConfigurationProperties(CryptoProperties.class)
public class FieldCipher {

    static final String PREFIX = "enc:v1:";
    private static final int NONCE_BYTES = 12;
    private static final int TAG_BITS = 128;

    private final SecretKey master;
    private final JdbcClient jdbc;
    private final SecureRandom random = new SecureRandom();
    /** 풀어 둔 데이터 키. 키는 바뀌지 않으므로(지우기만 한다) 무효화는 forget()뿐이다. */
    private final Map<Long, SecretKey> keys = new ConcurrentHashMap<>();

    public FieldCipher(CryptoProperties props, JdbcClient jdbc) {
        this.jdbc = jdbc;
        if (props.masterKey().isBlank()) {
            throw new IllegalStateException(
                    "본문 암호화 마스터 키가 없습니다 — 환경 변수 NAVI_DATA_KEY에 base64로 쓴 32바이트를 "
                            + "주세요(예: openssl rand -base64 32). 로컬 개발은 ./gradlew bootRun이 개발용 "
                            + "키를 넣어 줍니다");
        }
        byte[] raw;
        try {
            raw = Base64.getDecoder().decode(props.masterKey().strip());
        } catch (IllegalArgumentException e) {
            throw new IllegalStateException("NAVI_DATA_KEY가 base64가 아닙니다", e);
        }
        if (raw.length != 32) {
            throw new IllegalStateException("NAVI_DATA_KEY는 32바이트여야 합니다(지금 " + raw.length + ")");
        }
        this.master = new SecretKeySpec(raw, "AES");
    }

    /** 본문을 암호화한다. field는 "turn_message.text"처럼 칸 이름 — 인증 데이터로 묶인다. */
    public String encrypt(long userId, String field, String plaintext) {
        byte[] sealed = seal(keyFor(userId), aad(userId, field),
                plaintext.getBytes(StandardCharsets.UTF_8));
        return PREFIX + Base64.getEncoder().encodeToString(sealed);
    }

    /** 저장된 값을 푼다. 접두사가 없으면 이 기능 전의 평문이라 그대로 돌려준다. */
    public String decrypt(long userId, String field, String stored) {
        if (stored == null || !stored.startsWith(PREFIX)) {
            return stored;
        }
        byte[] sealed = Base64.getDecoder().decode(stored.substring(PREFIX.length()));
        return new String(open(keyFor(userId), aad(userId, field), sealed), StandardCharsets.UTF_8);
    }

    public static boolean isEncrypted(String stored) {
        return stored != null && stored.startsWith(PREFIX);
    }

    /** 풀어 둔 키를 잊는다 — 사용자 키를 지운 뒤 부른다. */
    public void forget(long userId) {
        keys.remove(userId);
    }

    private SecretKey keyFor(long userId) {
        return keys.computeIfAbsent(userId, this::loadOrCreate);
    }

    private SecretKey loadOrCreate(long userId) {
        // 감싸서 받는다 — 키가 아직 없는 행(NULL)과 행이 없는 것을 가려야 한다.
        byte[] wrapped = jdbc.sql("SELECT data_key FROM app_user WHERE user_id = ?").param(userId)
                .query((rs, n) -> new byte[][] {rs.getBytes(1)}).optional()
                .orElseThrow(() -> new IllegalStateException("사용자가 없습니다: " + userId))[0];
        if (wrapped == null) {
            byte[] fresh = new byte[32];
            random.nextBytes(fresh);
            // 동시에 두 요청이 처음 키를 만들 수 있다 — 먼저 쓴 쪽이 이기고 진 쪽은 다시 읽는다.
            jdbc.sql("UPDATE app_user SET data_key = ? WHERE user_id = ? AND data_key IS NULL")
                    .params(seal(master, wrapAad(userId), fresh), userId).update();
            wrapped = jdbc.sql("SELECT data_key FROM app_user WHERE user_id = ?").param(userId)
                    .query((rs, n) -> rs.getBytes(1)).single();
        }
        return new SecretKeySpec(open(master, wrapAad(userId), wrapped), "AES");
    }

    private byte[] seal(SecretKey key, byte[] aad, byte[] plaintext) {
        try {
            byte[] nonce = new byte[NONCE_BYTES];
            random.nextBytes(nonce);
            Cipher cipher = Cipher.getInstance("AES/GCM/NoPadding");
            cipher.init(Cipher.ENCRYPT_MODE, key, new GCMParameterSpec(TAG_BITS, nonce));
            cipher.updateAAD(aad);
            byte[] body = cipher.doFinal(plaintext);
            return ByteBuffer.allocate(nonce.length + body.length).put(nonce).put(body).array();
        } catch (GeneralSecurityException e) {
            throw new IllegalStateException("암호화 실패", e);
        }
    }

    private static byte[] open(SecretKey key, byte[] aad, byte[] sealed) {
        try {
            Cipher cipher = Cipher.getInstance("AES/GCM/NoPadding");
            cipher.init(Cipher.DECRYPT_MODE, key, new GCMParameterSpec(TAG_BITS, sealed, 0, NONCE_BYTES));
            cipher.updateAAD(aad);
            return cipher.doFinal(sealed, NONCE_BYTES, sealed.length - NONCE_BYTES);
        } catch (GeneralSecurityException e) {
            // 키가 다르거나, 값이 바뀌었거나, 다른 사용자·칸의 값을 옮겨 붙였다.
            throw new IllegalStateException("복호화 실패 — 키가 맞지 않거나 값이 바뀌었습니다", e);
        }
    }

    private static byte[] aad(long userId, String field) {
        return ("navi:field:user:" + userId + ":" + field).getBytes(StandardCharsets.UTF_8);
    }

    private static byte[] wrapAad(long userId) {
        return ("navi:data-key:user:" + userId).getBytes(StandardCharsets.UTF_8);
    }
}
