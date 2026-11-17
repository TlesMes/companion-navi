package navi.server.memory;

import java.time.Instant;
import java.time.OffsetDateTime;
import java.time.ZoneId;
import java.time.format.DateTimeParseException;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import navi.server.jobs.JobQueue;
import navi.server.jobs.JobQueue.Claimed;
import navi.server.quota.UsageLedger;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.jdbc.core.simple.JdbcClient;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

/**
 * 사실 추출 작업 — 언제 넣고, 워커에 무엇을 보이고, 결과를 어떻게 받아들이는가.
 *
 * <p>"언제"는 전부 결정론이다(단독 모드 FactExtractor와 같은 규칙): 확정된 새 사용자 발화가
 * {@code minNewUserTurns}개 쌓이면 작업 하나. LLM은 "무엇이 기억할 만한가"에만 쓴다 — 그건
 * 워커가 부른다.
 *
 * <p><b>추출기에 보이는 건 확정된 발화뿐이다.</b> 임시 턴은 재생 보고로 잘릴 수 있다. 그리고
 * 아직 안 끝난 턴(생성 중·임시)이 있으면 그 앞까지만 보인다 — 건너뛰고 커서를 넘기면 그 턴은
 * 나중에 확정돼도 영영 추출되지 않는다.
 */
@Service
public class FactJobs {

    public static final String KIND = "extract_facts";

    private static final Logger log = LoggerFactory.getLogger(FactJobs.class);
    /** 사실 문장 길이 상한 — 추출기가 대화를 통째로 옮겨 적는 것을 막는다. */
    private static final int MAX_FACT_CHARS = 300;
    /** 추출기에 보이는 발화 한 줄의 길이 상한(단독 모드 _MAX_TURN_CHARS와 같다). */
    private static final int MAX_TURN_CHARS = 300;

    // 커서 뒤, 아직 안 끝난 턴의 첫 발화 앞까지의 확정 발화.
    private static final String PENDING_CONFIRMED = """
              FROM turn_message m JOIN turn t USING (turn_id)
             WHERE m.user_id = :user AND m.message_id > :cursor AND t.status = 'confirmed'
               AND m.message_id < coalesce((
                     SELECT min(m2.message_id) FROM turn_message m2 JOIN turn t2 USING (turn_id)
                      WHERE m2.user_id = :user AND m2.message_id > :cursor
                        AND t2.status IN ('generating', 'provisional')),
                     9223372036854775807)
            """;

    /** 워커에게 줄 작업 내용. */
    public record Payload(
            long userId, Instant now, int utcOffsetMinutes, int maxFacts,
            List<Map<String, Object>> turns, List<Map<String, Object>> knownFacts) {}

    /** 워커가 돌려준 사실 하나. */
    public record FactResult(
            String kind, String text, Long sourceId, String expiresAt, Long replaces) {}

    /** 늦은 보고 — 그 시도는 이미 무효다(임대가 끝나 다른 워커가 가져갔다). */
    public static class StaleAttempt extends RuntimeException {
        public StaleAttempt() {
            super("유효하지 않은 시도입니다");
        }
    }

    private final JdbcClient jdbc;
    private final FactStore facts;
    private final JobQueue queue;
    private final UsageLedger ledger;
    private final FactsProperties props;

    public FactJobs(
            JdbcClient jdbc, FactStore facts, JobQueue queue, UsageLedger ledger,
            FactsProperties props) {
        this.jdbc = jdbc;
        this.facts = facts;
        this.queue = queue;
        this.ledger = ledger;
        this.props = props;
    }

    /**
     * 확정된 새 사용자 발화가 충분하면 추출 작업을 넣는다. 턴이 확정될 때마다 부른다.
     *
     * <p>실패를 삼킨다 — 추출은 있으면 좋은 것이지 턴의 전제가 아니다.
     */
    public void maybeEnqueue(long userId) {
        if (!props.enabled()) {
            return;
        }
        try {
            long cursor = facts.cursor(userId);
            long userTurns = jdbc.sql("SELECT count(*) " + PENDING_CONFIRMED + " AND m.role = 'user'")
                    .param("user", userId).param("cursor", cursor)
                    .query(Long.class).single();
            if (userTurns >= props.minNewUserTurns() && queue.enqueue(KIND, userId)) {
                log.info("사실 추출 작업 추가 user={} 새 사용자 발화={}", userId, userTurns);
            }
        } catch (RuntimeException e) {
            log.warn("사실 추출 작업을 넣지 못했습니다 user={}", userId, e);
        }
    }

    /** 가져간 작업의 내용 — 커서 뒤의 확정 대화와 이미 아는 사실. */
    public Payload payload(Claimed job) {
        long userId = job.userId();
        long cursor = facts.cursor(userId);
        List<Map<String, Object>> turns = jdbc.sql("""
                        SELECT m.message_id, m.role, left(m.text, :max_chars) AS text, m.created_at
                        """ + PENDING_CONFIRMED + " ORDER BY m.message_id LIMIT :limit")
                .param("user", userId).param("cursor", cursor)
                .param("max_chars", MAX_TURN_CHARS).param("limit", props.maxRowsPerBatch())
                .query((rs, n) -> {
                    Map<String, Object> turn = new LinkedHashMap<>();
                    turn.put("id", rs.getLong("message_id"));
                    turn.put("role", rs.getString("role"));
                    turn.put("text", rs.getString("text"));
                    turn.put("created_at",
                            rs.getObject("created_at", OffsetDateTime.class).toInstant().toString());
                    return turn;
                })
                .list();
        List<Map<String, Object>> known = facts.active(userId, props.maxKnownFacts()).stream()
                .map(f -> Map.<String, Object>of("id", f.id(), "kind", f.kind(), "text", f.text()))
                .toList();
        String timezone = jdbc.sql("SELECT timezone FROM app_user WHERE user_id = ?")
                .param(userId).query(String.class).single();
        Instant now = Instant.now();
        // 달력 계산은 워커가 한다. 시간대 이름 대신 지금의 오프셋을 준다 — 워커가 시간대 DB를
        // 갖지 않아도 되고, 이 작업은 길어야 몇 분이라 그 사이 오프셋이 바뀔 일이 거의 없다.
        int offset = ZoneId.of(timezone).getRules().getOffset(now).getTotalSeconds() / 60;
        return new Payload(userId, now, offset, props.maxFactsPerBatch(), turns, known);
    }

    /**
     * 워커의 결과를 받아들인다 — 사실 적재·대체, 커서 이동, 토큰 차감, 완료 표시가 한
     * 트랜잭션이다(D25가 큐를 PostgreSQL에 둔 근거 ②).
     *
     * <p>워커가 준 번호는 믿지 않는다: 출처는 그 사용자의 발화일 때만, 대체는 그 사용자의
     * 유효한 사실일 때만 받아들인다.
     *
     * @param cursor 이 작업이 읽은 마지막 발화 ID. 뽑은 게 없어도 옮긴다 — "기억할 게 없다"도
     *     정당한 답이고, 안 옮기면 같은 대화를 매번 다시 보낸다
     * @return 적재한 사실 수
     * @throws StaleAttempt 그 시도가 이미 무효다
     */
    @Transactional
    public int complete(
            long jobId, int attempt, List<FactResult> results, Long cursor, long tokens) {
        Claimed job = queue.lockForCompletion(jobId, attempt).orElseThrow(StaleAttempt::new);
        long userId = job.userId();
        int stored = 0;
        int superseded = 0;
        for (FactResult fact : results) {
            if (stored >= props.maxFactsPerBatch()) {
                break;
            }
            String text = fact.text() == null ? "" : fact.text().strip();
            if (!FactStore.KINDS.contains(fact.kind()) || text.isEmpty()
                    || text.codePointCount(0, text.length()) > MAX_FACT_CHARS) {
                continue;
            }
            long id = facts.insert(userId, fact.kind(), text, ownMessage(userId, fact.sourceId()),
                    parseTime(fact.expiresAt()));
            stored++;
            if (fact.replaces() != null && facts.supersede(userId, fact.replaces(), id)) {
                superseded++;
            }
        }
        if (cursor != null && ownMessage(userId, cursor) != null) {
            facts.advanceCursor(userId, cursor);
        }
        if (tokens > 0) {
            ledger.charge(userId, tokens, 0); // 사용자 대화에서 나온 작업 — 그 사용자에게 귀속(R1)
        }
        queue.markDone(jobId, "{\"stored\":" + stored + ",\"superseded\":" + superseded + "}");
        log.info("사실 추출 완료 user={} job={} 적재={} 대체={} 토큰={}",
                userId, jobId, stored, superseded, tokens);
        return stored;
    }

    private Long ownMessage(long userId, Long messageId) {
        if (messageId == null) {
            return null;
        }
        return jdbc.sql("SELECT message_id FROM turn_message WHERE message_id = ? AND user_id = ?")
                .params(messageId, userId).query(Long.class).optional().orElse(null);
    }

    private static OffsetDateTime parseTime(String iso) {
        if (iso == null || iso.isBlank()) {
            return null;
        }
        try {
            return OffsetDateTime.parse(iso);
        } catch (DateTimeParseException e) {
            return null; // 못 읽는 만료는 "모름"으로 — 사실 자체를 버릴 이유는 아니다(단독 모드와 같다)
        }
    }
}
