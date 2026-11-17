package navi.server.jobs;

import com.fasterxml.jackson.annotation.JsonProperty;
import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import navi.server.memory.FactJobs;
import navi.server.memory.FactJobs.FactResult;
import org.springframework.http.HttpHeaders;
import org.springframework.http.HttpStatus;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestHeader;
import org.springframework.web.bind.annotation.RestController;

/**
 * 워커 API — Python 워커가 작업을 pull한다(service.md §9·§11, D25). 명세는 protocol.md §7.
 *
 * <p>워커는 DB에 직접 붙지 않는다. 스키마와 검증은 서버가 갖고, 워커는 LLM을 부르고 결과를
 * 돌려줄 뿐이다 — 워커가 DB 자격을 갖지 않아도 되고, 워커가 준 값을 서버가 한 번 더 거른다.
 */
@RestController
public class WorkerController {

    record ClaimRequest(List<String> kinds) {}

    record UsageIn(
            @JsonProperty("input_tokens") long inputTokens,
            @JsonProperty("output_tokens") long outputTokens) {}

    record FactIn(
            String kind, String text, @JsonProperty("source_id") Long sourceId,
            @JsonProperty("expires_at") String expiresAt, Long replaces) {}

    record CompleteRequest(int attempt, List<FactIn> facts, Long cursor, UsageIn usage) {}

    record FailRequest(int attempt, String error) {}

    private final JobQueue queue;
    private final FactJobs factJobs;
    private final JobProperties props;

    public WorkerController(JobQueue queue, FactJobs factJobs, JobProperties props) {
        this.queue = queue;
        this.factJobs = factJobs;
        this.props = props;
    }

    @PostMapping("/v1/worker/jobs/claim")
    public ResponseEntity<?> claim(
            @RequestHeader(value = HttpHeaders.AUTHORIZATION, required = false) String auth,
            @RequestBody(required = false) ClaimRequest request) {
        ResponseEntity<?> denied = authorize(auth);
        if (denied != null) {
            return denied;
        }
        // 지금 아는 작업 종류는 하나다. 워커가 모르는 종류를 달라고 하면 줄 게 없다.
        List<String> kinds = request == null || request.kinds() == null
                ? List.of(FactJobs.KIND)
                : request.kinds().stream().filter(FactJobs.KIND::equals).toList();
        if (kinds.isEmpty()) {
            return ResponseEntity.noContent().build();
        }
        return queue.claim(kinds)
                .<ResponseEntity<?>>map(job -> {
                    FactJobs.Payload p = factJobs.payload(job);
                    Map<String, Object> payload = new LinkedHashMap<>();
                    payload.put("user_id", p.userId());
                    payload.put("now", p.now().toString());
                    payload.put("utc_offset_minutes", p.utcOffsetMinutes());
                    payload.put("max_facts", p.maxFacts());
                    payload.put("turns", p.turns());
                    payload.put("known_facts", p.knownFacts());
                    Map<String, Object> body = new LinkedHashMap<>();
                    body.put("job_id", job.jobId());
                    body.put("kind", job.kind());
                    body.put("attempt", job.attempt());
                    body.put("payload", payload);
                    return ResponseEntity.ok(body);
                })
                .orElseGet(() -> ResponseEntity.noContent().build());
    }

    @PostMapping("/v1/worker/jobs/{jobId}/complete")
    public ResponseEntity<?> complete(
            @RequestHeader(value = HttpHeaders.AUTHORIZATION, required = false) String auth,
            @PathVariable long jobId, @RequestBody CompleteRequest request) {
        ResponseEntity<?> denied = authorize(auth);
        if (denied != null) {
            return denied;
        }
        List<FactResult> results = request.facts() == null ? List.of() : request.facts().stream()
                .map(f -> new FactResult(f.kind(), f.text(), f.sourceId(), f.expiresAt(), f.replaces()))
                .toList();
        long tokens = request.usage() == null
                ? 0 : request.usage().inputTokens() + request.usage().outputTokens();
        int stored;
        Long userId;
        try {
            stored = factJobs.complete(jobId, request.attempt(), results, request.cursor(), tokens);
        } catch (FactJobs.StaleAttempt e) {
            return ResponseEntity.status(HttpStatus.CONFLICT).body(Map.of("error", "stale_attempt"));
        }
        // 완료가 커밋된 뒤 — 그사이 더 쌓인 대화가 있으면 다음 작업을 넣는다.
        userId = queue.userOf(jobId);
        if (userId != null) {
            factJobs.maybeEnqueue(userId);
        }
        return ResponseEntity.ok(Map.of("ok", true, "stored", stored));
    }

    @PostMapping("/v1/worker/jobs/{jobId}/fail")
    public ResponseEntity<?> fail(
            @RequestHeader(value = HttpHeaders.AUTHORIZATION, required = false) String auth,
            @PathVariable long jobId, @RequestBody FailRequest request) {
        ResponseEntity<?> denied = authorize(auth);
        if (denied != null) {
            return denied;
        }
        if (!queue.fail(jobId, request.attempt(), request.error())) {
            return ResponseEntity.status(HttpStatus.CONFLICT).body(Map.of("error", "stale_attempt"));
        }
        return ResponseEntity.ok(Map.of("ok", true));
    }

    /** 공유 비밀 확인. 설정이 비어 있으면 API 자체가 닫힌다. 통과면 null. */
    private ResponseEntity<?> authorize(String header) {
        if (props.token().isBlank()) {
            return ResponseEntity.status(HttpStatus.SERVICE_UNAVAILABLE)
                    .body(Map.of("error", "worker_api_disabled"));
        }
        String expected = "Bearer " + props.token();
        boolean ok = header != null && MessageDigest.isEqual(
                header.getBytes(StandardCharsets.UTF_8), expected.getBytes(StandardCharsets.UTF_8));
        return ok ? null : ResponseEntity.status(HttpStatus.UNAUTHORIZED)
                .body(Map.of("error", "unauthorized"));
    }
}
