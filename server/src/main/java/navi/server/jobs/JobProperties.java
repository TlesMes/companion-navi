package navi.server.jobs;

import org.springframework.boot.context.properties.ConfigurationProperties;
import org.springframework.boot.context.properties.bind.DefaultValue;

/**
 * 작업 큐와 워커 API({@code navi.worker.*}).
 *
 * @param token 워커가 내미는 공유 비밀. 비어 있으면 워커 API가 닫힌다(503) — 키 없이 실수로
 *     열어 두지 않게. 환경 변수 {@code NAVI_WORKER_TOKEN}
 * @param leaseSeconds 가져간 작업을 붙들 수 있는 시간. 지나면 워커가 죽은 것으로 보고 다시 내준다
 * @param maxAttempts 이만큼 실패하면 그 작업은 버린다(다음 대화가 쌓이면 새 작업이 생긴다)
 * @param retryBackoffSeconds 실패 뒤 다시 가져갈 수 있기까지 — 단독 모드의 추출 백오프와 같다
 */
@ConfigurationProperties("navi.worker")
public record JobProperties(
        @DefaultValue("") String token,
        @DefaultValue("300") int leaseSeconds,
        @DefaultValue("3") int maxAttempts,
        @DefaultValue("1800") int retryBackoffSeconds) {}
