package navi.server.jobs;

import java.util.List;
import java.util.Optional;
import org.springframework.boot.context.properties.EnableConfigurationProperties;
import org.springframework.jdbc.core.simple.JdbcClient;
import org.springframework.stereotype.Repository;

/**
 * 작업 큐 — PostgreSQL 행과 {@code SKIP LOCKED}(D25). 인터페이스 뒤에 두지 않은 건 아직
 * 구현이 하나라서다. 폴링 지연이나 DB 부하가 측정되면 그때 뗀다.
 *
 * <p><b>펜싱:</b> claim할 때마다 {@code attempts}가 오르고, 완료·실패는 그 값을 내밀어야
 * 받아들여진다. 임대가 끝나 다른 워커가 다시 가져간 작업을, 늦게 깨어난 첫 워커가 덮어쓰지
 * 못한다.
 */
@Repository
@EnableConfigurationProperties(JobProperties.class)
public class JobQueue {

    /** 가져간 작업. attempt가 펜싱 토큰이다. */
    public record Claimed(long jobId, String kind, Long userId, int attempt) {}

    private final JdbcClient jdbc;
    private final JobProperties props;

    public JobQueue(JdbcClient jdbc, JobProperties props) {
        this.jdbc = jdbc;
        this.props = props;
    }

    /**
     * 작업을 넣는다. 그 사용자의 같은 종류 작업이 이미 기다리고 있으면 아무 일도 없다.
     *
     * @return 새로 넣었으면 true
     */
    public boolean enqueue(String kind, long userId) {
        // 임대가 끝났는데 더 시도할 수 없는 작업은 여기서 정리한다. 남겨 두면 대기 작업 자리를
        // 영영 차지해 그 사용자의 새 작업이 못 들어온다.
        jdbc.sql("""
                        UPDATE job SET status = 'failed', updated_at = now(),
                               last_error = coalesce(last_error, '임대 만료')
                         WHERE kind = ? AND user_id = ? AND status = 'claimed'
                           AND lease_until < now() AND attempts >= ?
                        """)
                .params(kind, userId, props.maxAttempts()).update();
        return jdbc.sql("""
                        INSERT INTO job (kind, user_id) VALUES (?, ?)
                        ON CONFLICT (kind, user_id) WHERE status IN ('queued', 'claimed') DO NOTHING
                        """)
                .params(kind, userId).update() > 0;
    }

    /**
     * 가져갈 수 있는 작업 하나를 가져간다. 없으면 비어 있다.
     *
     * <p>{@code SKIP LOCKED}라 워커 여럿이 동시에 불러도 같은 작업을 두 번 내주지 않는다 — 다른
     * 트랜잭션이 잠근 행은 기다리지 않고 건너뛴다.
     */
    public Optional<Claimed> claim(List<String> kinds) {
        return jdbc.sql("""
                        UPDATE job
                           SET status = 'claimed', attempts = attempts + 1, updated_at = now(),
                               lease_until = now() + make_interval(secs => ?)
                         WHERE job_id = (
                               SELECT job_id FROM job
                                WHERE kind = ANY (?)
                                  AND attempts < ?
                                  AND ((status = 'queued' AND not_before <= now())
                                    OR (status = 'claimed' AND lease_until < now()))
                                ORDER BY job_id
                                LIMIT 1
                                  FOR UPDATE SKIP LOCKED)
                        RETURNING job_id, kind, user_id, attempts
                        """)
                .params(props.leaseSeconds(), kinds.toArray(String[]::new), props.maxAttempts())
                .query((rs, n) -> new Claimed(
                        rs.getLong("job_id"), rs.getString("kind"),
                        rs.getObject("user_id", Long.class), rs.getInt("attempts")))
                .optional();
    }

    /**
     * 작업을 잠근다 — 완료·실패 처리의 첫 단계. 그 시도가 아직 유효할 때만 돌려준다.
     *
     * <p>부르는 쪽의 트랜잭션 안에서 부른다. 결과 적재가 끝날 때까지 행이 잠겨 있다.
     */
    public Optional<Claimed> lockForCompletion(long jobId, int attempt) {
        return jdbc.sql("""
                        SELECT job_id, kind, user_id, attempts FROM job
                         WHERE job_id = ? AND attempts = ? AND status = 'claimed'
                           FOR UPDATE
                        """)
                .params(jobId, attempt)
                .query((rs, n) -> new Claimed(
                        rs.getLong("job_id"), rs.getString("kind"),
                        rs.getObject("user_id", Long.class), rs.getInt("attempts")))
                .optional();
    }

    public Long userOf(long jobId) {
        return jdbc.sql("SELECT user_id FROM job WHERE job_id = ?")
                .param(jobId).query(Long.class).optional().orElse(null);
    }

    public void markDone(long jobId, String resultJson) {
        jdbc.sql("""
                        UPDATE job SET status = 'done', result = ?::jsonb, lease_until = NULL,
                               updated_at = now()
                         WHERE job_id = ?
                        """)
                .params(resultJson, jobId).update();
    }

    /**
     * 실패를 적는다. 시도가 남았으면 백오프 뒤 다시 내주고, 아니면 버린다.
     *
     * @return 그 시도가 유효했으면 true(늦게 온 실패 보고면 false)
     */
    public boolean fail(long jobId, int attempt, String error) {
        String trimmed = error == null ? "" : error.substring(0, Math.min(error.length(), 500));
        return jdbc.sql("""
                        UPDATE job
                           SET status = CASE WHEN attempts >= ? THEN 'failed' ELSE 'queued' END,
                               not_before = now() + make_interval(secs => ?),
                               lease_until = NULL, last_error = ?, updated_at = now()
                         WHERE job_id = ? AND attempts = ? AND status = 'claimed'
                        """)
                .params(props.maxAttempts(), props.retryBackoffSeconds(), trimmed, jobId, attempt)
                .update() > 0;
    }
}
