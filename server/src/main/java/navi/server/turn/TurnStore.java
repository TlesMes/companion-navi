package navi.server.turn;

import java.util.List;
import java.util.UUID;
import navi.server.assembly.Message;
import navi.server.assembly.TurnKind;
import navi.server.brain.Usage;
import org.springframework.dao.DuplicateKeyException;
import org.springframework.jdbc.core.simple.JdbcClient;
import org.springframework.stereotype.Repository;
import org.springframework.transaction.annotation.Transactional;

/**
 * 턴 저장소 — 턴의 한살이를 맡는다.
 *
 * <pre>
 *  begin ─▶ generating ─┬─ finishProvisional ─▶ provisional ─┬─ confirm(재생 보고) ─▶ confirmed
 *                       │                                    └─ confirmUnreported ──▶ confirmed
 *                       └─ discard ─▶ discarded   (취소·연결 끊김·두뇌 실패)
 * </pre>
 *
 * <p><b>조립이 읽는 건 provisional과 confirmed뿐이다.</b> 생성 중이거나 폐기된 턴은 대화에 없던
 * 일이다(D22). provisional을 읽는 이유: 재생 보고는 다음 발화보다 늦게 올 수 있고, 그 사이에
 * 방금 한 말을 모르면 대화가 끊긴다.
 *
 * <p>확정은 재생 보고를 받은 뒤다(service.md §3, G1). 사용자가 듣지 못한 말을 "했다"고 기억하면
 * 안 되므로 보고된 지점에서 본문을 자른다.
 */
@Repository
public class TurnStore {

    private final JdbcClient jdbc;

    public TurnStore(JdbcClient jdbc) {
        this.jdbc = jdbc;
    }

    /**
     * 턴을 연다. REACTIVE면 사용자 발화도 함께 적는다 — 선제 턴의 트리거는 사용자가 한 말이
     * 아니라 소재라서 대화 기록에 넣지 않는다.
     *
     * <p>먼저 {@link #recallRecent}로 이력을 읽고 나서 부른다(이 턴의 발화는 이력이 아니라 트리거다).
     *
     * @throws DuplicateTurnException 같은 사용자의 같은 {@code clientTurnId}가 이미 있다
     */
    @Transactional
    public long begin(
            long userId, UUID clientTurnId, String personaKey, TurnKind kind, String trigger,
            String model) {
        long turnId;
        try {
            turnId = jdbc.sql("""
                            INSERT INTO turn (user_id, client_turn_id, persona_key, kind, status, model)
                            VALUES (?, ?, ?, ?, 'generating', ?) RETURNING turn_id
                            """)
                    .params(userId, clientTurnId, personaKey, kind.wire(), model)
                    .query(Long.class).single();
        } catch (DuplicateKeyException e) {
            throw new DuplicateTurnException(clientTurnId);
        }
        if (kind == TurnKind.REACTIVE) {
            insertMessage(turnId, userId, personaKey, Message.USER, trigger);
        }
        return turnId;
    }

    /** 이 사용자×페르소나의 최근 대화 {@code limit}행, 시간순. 행 수다 — 왕복 수가 아니다. */
    public List<Message> recallRecent(long userId, String personaKey, int limit) {
        List<Message> newestFirst = jdbc.sql("""
                        SELECT m.role, m.text
                          FROM turn_message m JOIN turn t USING (turn_id)
                         WHERE m.user_id = ? AND m.persona_key = ?
                           AND t.status IN ('provisional', 'confirmed')
                         ORDER BY m.message_id DESC
                         LIMIT ?
                        """)
                .params(userId, personaKey, limit)
                .query((rs, n) -> new Message(rs.getString("role"), rs.getString("text")))
                .list();
        return newestFirst.reversed();
    }

    /** 생성이 끝났다. 응답을 임시로 적는다 — 확정은 재생 보고를 기다린다. */
    @Transactional
    public void finishProvisional(
            long turnId, String assistantText, String mood, Usage usage, Integer ttftMs,
            int totalMs) {
        TurnRow row = lock(turnId);
        if (!"generating".equals(row.status())) {
            return; // 그 사이 폐기됐다(연결 끊김과 경합) — 되살리지 않는다
        }
        if (!assistantText.isEmpty()) {
            insertMessage(turnId, row.userId(), row.personaKey(), Message.ASSISTANT, assistantText);
        }
        jdbc.sql("""
                        UPDATE turn SET status = 'provisional', mood = ?, ttft_ms = ?, total_ms = ?,
                               input_tokens = ?, output_tokens = ?, cache_read_tokens = ?,
                               cache_write_tokens = ?, ended_at = now()
                         WHERE turn_id = ?
                        """)
                .params(mood, ttftMs, totalMs, usage.inputTokens(), usage.outputTokens(),
                        usage.cacheReadTokens(), usage.cacheWriteTokens(), turnId)
                .update();
    }

    /**
     * 재생 보고로 확정한다.
     *
     * @param playedChars 사용자가 실제로 들은 본문 길이(유니코드 코드 포인트). 본문을 여기서 자른다.
     *     0이면 한 글자도 못 들은 것이라 나비의 발화 행을 지운다(사용자 발화는 남는다)
     * @return 확정했으면 true. 그 사용자의 임시 턴이 아니면 false(이미 확정·폐기됐거나 남의 턴)
     */
    @Transactional
    public boolean confirm(long userId, UUID clientTurnId, int playedChars, boolean interrupted) {
        Long turnId = jdbc.sql("""
                        SELECT turn_id FROM turn
                         WHERE user_id = ? AND client_turn_id = ? AND status = 'provisional'
                           FOR UPDATE
                        """)
                .params(userId, clientTurnId)
                .query(Long.class).optional().orElse(null);
        if (turnId == null) {
            return false;
        }
        if (playedChars <= 0) {
            jdbc.sql("DELETE FROM turn_message WHERE turn_id = ? AND role = 'assistant'")
                    .param(turnId).update();
        } else {
            // PostgreSQL의 left()는 문자(코드 포인트) 단위로 자른다. 본문보다 길면 그대로 둔다.
            jdbc.sql("UPDATE turn_message SET text = left(text, ?) WHERE turn_id = ? AND role = 'assistant'")
                    .params(playedChars, turnId).update();
        }
        jdbc.sql("UPDATE turn SET status = 'confirmed', interrupted = ? WHERE turn_id = ?")
                .params(interrupted, turnId).update();
        return true;
    }

    /**
     * 재생 보고 없이 연결이 닫힌 임시 턴을 전문으로 확정한다.
     *
     * <p>생성이 끝났으면 클라이언트는 전문을 받았다. 폐기하면 다음 턴의 맥락이 빈다. 대가는
     * 사용자가 실제로 못 들은 말이 기억에 남을 수 있다는 것이다(service.md §3의 미정 항목을
     * 이렇게 정했다, 2026.11.16).
     *
     * @return 확정한 턴 수
     */
    public int confirmUnreported(long userId) {
        return jdbc.sql("UPDATE turn SET status = 'confirmed' WHERE user_id = ? AND status = 'provisional'")
                .param(userId).update();
    }

    /**
     * 턴을 폐기한다 — 취소·연결 끊김(D22)·두뇌 실패. 본문을 지우고 행은 남긴다.
     *
     * <p>쓴 토큰은 적는다. 폐기해도 벤더에는 값을 냈다.
     */
    @Transactional
    public void discard(long turnId, Usage usage, int totalMs) {
        TurnRow row = lock(turnId);
        if (!"generating".equals(row.status())) {
            return;
        }
        jdbc.sql("DELETE FROM turn_message WHERE turn_id = ?").param(turnId).update();
        jdbc.sql("""
                        UPDATE turn SET status = 'discarded', total_ms = ?, input_tokens = ?,
                               output_tokens = ?, cache_read_tokens = ?, cache_write_tokens = ?,
                               ended_at = now()
                         WHERE turn_id = ?
                        """)
                .params(totalMs, usage.inputTokens(), usage.outputTokens(),
                        usage.cacheReadTokens(), usage.cacheWriteTokens(), turnId)
                .update();
    }

    /**
     * 생성 중인 채 남은 턴을 폐기한다 — 서버가 턴 도중 죽은 흔적이다. 부팅 때 한 번 부른다.
     *
     * <p>서버가 한 대라는 전제다. 여러 대가 되면 다른 서버가 생성 중인 턴을 지우게 되므로
     * 그때는 소유 서버나 시각 기준이 필요하다.
     */
    @Transactional
    public int discardAbandoned() {
        jdbc.sql("""
                        DELETE FROM turn_message WHERE turn_id IN
                               (SELECT turn_id FROM turn WHERE status = 'generating')
                        """).update();
        return jdbc.sql("UPDATE turn SET status = 'discarded', ended_at = now() WHERE status = 'generating'")
                .update();
    }

    /** 클라이언트가 보고한 수치를 턴에 붙인다. 그 사용자의 턴이 아니면 아무 일도 없다. */
    public boolean recordClientMetrics(long userId, UUID clientTurnId, String metricsJson) {
        return jdbc.sql("""
                        UPDATE turn SET client_metrics = ?::jsonb
                         WHERE user_id = ? AND client_turn_id = ?
                        """)
                .params(metricsJson, userId, clientTurnId).update() > 0;
    }

    private record TurnRow(long userId, String personaKey, String status) {}

    private TurnRow lock(long turnId) {
        return jdbc.sql("SELECT user_id, persona_key, status FROM turn WHERE turn_id = ? FOR UPDATE")
                .param(turnId)
                .query((rs, n) -> new TurnRow(
                        rs.getLong("user_id"), rs.getString("persona_key"), rs.getString("status")))
                .single();
    }

    private void insertMessage(long turnId, long userId, String personaKey, String role, String text) {
        jdbc.sql("INSERT INTO turn_message (turn_id, user_id, persona_key, role, text) VALUES (?, ?, ?, ?, ?)")
                .params(turnId, userId, personaKey, role, text).update();
    }
}
