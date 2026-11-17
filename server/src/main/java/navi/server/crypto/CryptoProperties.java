package navi.server.crypto;

import org.springframework.boot.context.properties.ConfigurationProperties;
import org.springframework.boot.context.properties.bind.DefaultValue;

/**
 * 본문 암호화 설정({@code navi.crypto.*}).
 *
 * @param masterKey 서버 마스터 키 — base64로 쓴 32바이트(AES-256). 환경 변수 {@code NAVI_DATA_KEY}.
 *     **비어 있으면 서버가 뜨지 않는다** — 평문으로 조용히 저장하는 길을 두지 않는다.
 *     잃으면 모든 사용자의 본문을 되돌릴 수 없고, 새면 DB와 함께 전부 읽힌다. 보관은 DB와 따로.
 */
@ConfigurationProperties("navi.crypto")
public record CryptoProperties(@DefaultValue("") String masterKey) {}
