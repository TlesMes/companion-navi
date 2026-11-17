package navi.server.memory;

import java.time.OffsetDateTime;
import java.util.ArrayList;
import java.util.List;
import java.util.Set;
import navi.server.crypto.FieldCipher;
import org.springframework.boot.context.properties.EnableConfigurationProperties;
import org.springframework.jdbc.core.simple.JdbcClient;
import org.springframework.stereotype.Repository;

/**
 * 사실과 추출 커서. 단독 모드의 {@code navi/memory/store.py} 사실 부분과 같은 규칙이다.
 *
 * <p>쓰는 쪽(적재·대체·커서)은 워커 작업 완료가 부르고, 읽는 쪽(주입·창 길이)은 턴이 부른다.
 */
@Repository
@EnableConfigurationProperties(FactsProperties.class)
public class FactStore {

    /** 사실의 종류 — 단독 모드 FACT_KINDS와 같다. */
    public static final Set<String> KINDS = Set.of("profile", "preference", "ongoing", "plan");

    /** 사실 하나. 프롬프트에 그대로 들어가는 건 text다. */
    public record Fact(long id, String kind, String text) {}

    private static final String ACTIVE = """
            user_id = ? AND status = 'active' AND (expires_at IS NULL OR expires_at > now())
            """;

    /** 본문 칸 이름 — 암호문의 인증 데이터로 묶인다. */
    public static final String TEXT = "fact.text";

    private final JdbcClient jdbc;
    private final FieldCipher cipher;

    public FactStore(JdbcClient jdbc, FieldCipher cipher) {
        this.jdbc = jdbc;
        this.cipher = cipher;
    }

    /**
     * 이번 턴에 넣을 사실 최대 k개.
     *
     * <p>고르는 규칙은 점수가 아니라 종류다: <b>신상(profile)을 먼저 다 넣고</b>, 남는 자리를
     * 나머지의 최신 것으로 채운다. 최근 순 하나로만 자르면 몇 달 전에 들은 가족 이름이 지난주
     * 잡사실에 밀린다. 순서는 신상 → 나머지, 각각 오래된 순 — 새 사실이 각 구간의 끝에 붙어
     * 앞쪽이 그대로다(프롬프트 캐시). 순서는 호출마다 같아야 한다.
     */
    public List<Fact> recall(long userId, int k) {
        if (k <= 0) {
            return List.of();
        }
        List<Fact> profile = newest(userId, "kind = 'profile'", k);
        List<Fact> rest = profile.size() < k
                ? newest(userId, "kind <> 'profile'", k - profile.size())
                : List.of();
        List<Fact> out = new ArrayList<>(profile);
        out.addAll(rest);
        return out;
    }

    /** 지금 유효한 사실 중 최신 limit개, 오래된 순 — 추출기에 "이미 아는 사실"로 보인다. */
    public List<Fact> active(long userId, int limit) {
        return newest(userId, "TRUE", limit);
    }

    private List<Fact> newest(long userId, String filter, int limit) {
        List<Fact> newestFirst = jdbc.sql("SELECT fact_id, kind, text FROM fact WHERE " + ACTIVE
                        + " AND " + filter + " ORDER BY fact_id DESC LIMIT ?")
                .params(userId, limit)
                .query((rs, n) -> new Fact(
                        rs.getLong(1), rs.getString(2), cipher.decrypt(userId, TEXT, rs.getString(3))))
                .list();
        return newestFirst.reversed();
    }

    public long insert(long userId, String kind, String text, Long sourceMessageId,
            OffsetDateTime expiresAt) {
        return jdbc.sql("""
                        INSERT INTO fact (user_id, kind, text, source_message_id, expires_at)
                        VALUES (?, ?, ?, ?, ?) RETURNING fact_id
                        """)
                .params(userId, kind, cipher.encrypt(userId, TEXT, text), sourceMessageId, expiresAt)
                .query(Long.class).single();
    }

    /**
     * 옛 사실을 '대체됨'으로 돌린다. user_id를 조건에 넣는 건 추출기가 준 번호를 믿지 않기
     * 위해서다 — 남의 사실이거나 없는 번호면 아무 일도 없다.
     */
    public boolean supersede(long userId, long oldFactId, long newFactId) {
        return jdbc.sql("""
                        UPDATE fact SET status = 'superseded', superseded_by = ?
                         WHERE fact_id = ? AND user_id = ? AND status = 'active'
                        """)
                .params(newFactId, oldFactId, userId).update() > 0;
    }

    /** 추출기에 보인 마지막 발화 ID. 없으면 0(처음부터). */
    public long cursor(long userId) {
        return jdbc.sql("SELECT last_message_id FROM fact_cursor WHERE user_id = ?")
                .param(userId).query(Long.class).optional().orElse(0L);
    }

    /** 커서를 옮긴다. 뒤로는 안 간다 — 늦게 끝난 작업이 앞선 작업의 진도를 되돌리지 않게. */
    public void advanceCursor(long userId, long messageId) {
        jdbc.sql("""
                        INSERT INTO fact_cursor (user_id, last_message_id) VALUES (?, ?)
                        ON CONFLICT (user_id) DO UPDATE
                           SET last_message_id = greatest(fact_cursor.last_message_id,
                                                          EXCLUDED.last_message_id),
                               updated_at = now()
                        """)
                .params(userId, messageId).update();
    }
}
