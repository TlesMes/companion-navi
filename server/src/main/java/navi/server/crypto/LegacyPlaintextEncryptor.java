package navi.server.crypto;

import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.boot.ApplicationArguments;
import org.springframework.boot.ApplicationRunner;
import org.springframework.jdbc.core.simple.JdbcClient;
import org.springframework.stereotype.Component;

/**
 * 암호화 전에 쌓인 평문 본문을 부팅 때 암호화한다. 키가 있어야 해서 SQL 마이그레이션으로는 못 한다.
 *
 * <p>읽는 쪽은 평문도 받으므로({@link FieldCipher#decrypt}) 이게 끝나기 전에도 서버는 맞게 돈다.
 */
@Component
class LegacyPlaintextEncryptor implements ApplicationRunner {

    private static final Logger log = LoggerFactory.getLogger(LegacyPlaintextEncryptor.class);
    private static final String NOT_ENCRYPTED = "text NOT LIKE '" + FieldCipher.PREFIX + "%'";

    private final JdbcClient jdbc;
    private final FieldCipher cipher;

    LegacyPlaintextEncryptor(JdbcClient jdbc, FieldCipher cipher) {
        this.jdbc = jdbc;
        this.cipher = cipher;
    }

    @Override
    public void run(ApplicationArguments args) {
        int messages = encrypt("turn_message", "message_id", "turn_message.text");
        int facts = encrypt("fact", "fact_id", "fact.text");
        if (messages + facts > 0) {
            log.warn("평문으로 남아 있던 본문을 암호화했습니다 — 발화 {}건, 사실 {}건", messages, facts);
        }
    }

    private int encrypt(String table, String idColumn, String field) {
        record Row(long id, long userId, String text) {}
        var rows = jdbc.sql("SELECT " + idColumn + ", user_id, text FROM " + table
                        + " WHERE " + NOT_ENCRYPTED)
                .query((rs, n) -> new Row(rs.getLong(1), rs.getLong(2), rs.getString(3)))
                .list();
        for (Row row : rows) {
            // 조건에 평문 그대로를 다시 건다 — 그사이 다른 쪽이 바꿨으면 덮지 않는다.
            jdbc.sql("UPDATE " + table + " SET text = ? WHERE " + idColumn + " = ? AND text = ?")
                    .params(cipher.encrypt(row.userId(), field, row.text()), row.id(), row.text())
                    .update();
        }
        return rows.size();
    }
}
