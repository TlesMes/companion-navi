package navi.server.crypto;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;

import java.util.List;
import java.util.UUID;
import navi.server.PostgresTestConfig;
import navi.server.assembly.Message;
import navi.server.assembly.TurnKind;
import navi.server.brain.Usage;
import navi.server.identity.DeviceService;
import navi.server.memory.FactStore;
import navi.server.turn.TurnStore;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.context.annotation.Import;
import org.springframework.jdbc.core.simple.JdbcClient;

/** 본문 필드 암호화(G8 ②) — DB에는 암호문만, 읽는 쪽은 그대로, 옮겨 붙이면 안 풀린다. */
@SpringBootTest
@Import(PostgresTestConfig.class)
class FieldCipherTest {

    @Autowired FieldCipher cipher;
    @Autowired TurnStore turns;
    @Autowired FactStore facts;
    @Autowired DeviceService devices;
    @Autowired JdbcClient jdbc;
    long user;

    @BeforeEach
    void setUp() {
        user = devices.register().identity().userId();
    }

    private List<String> rawMessages(long userId) {
        return jdbc.sql("SELECT text FROM turn_message WHERE user_id = ? ORDER BY message_id")
                .param(userId).query(String.class).list();
    }

    @Test
    void conversationAndFactsAreStoredOnlyAsCiphertext() {
        UUID id = UUID.randomUUID();
        long turn = turns.begin(user, id, "navi", TurnKind.REACTIVE, "내 비밀번호는 1234야", "m");
        turns.finishProvisional(turn, "그건 말하지 마.", "calm", Usage.ZERO, 1, 2);
        facts.insert(user, "profile", "사용자의 동생 이름은 민준이다.", null, null);

        assertThat(rawMessages(user)).allSatisfy(raw -> {
            assertThat(raw).startsWith("enc:v1:");
            assertThat(raw).doesNotContain("1234").doesNotContain("말하지");
        });
        assertThat(jdbc.sql("SELECT text FROM fact WHERE user_id = ?").param(user)
                .query(String.class).single()).startsWith("enc:v1:").doesNotContain("민준");
        // 읽는 쪽은 아무것도 모른다.
        assertThat(turns.recallRecent(user, "navi", 10)).containsExactly(
                Message.user("내 비밀번호는 1234야"), Message.assistant("그건 말하지 마."));
        assertThat(facts.recall(user, 10)).extracting(FactStore.Fact::text)
                .containsExactly("사용자의 동생 이름은 민준이다.");
    }

    @Test
    void sameTextEncryptsDifferentlyEachTime() {
        assertThat(cipher.encrypt(user, "t", "안녕")).isNotEqualTo(cipher.encrypt(user, "t", "안녕"));
    }

    @Test
    void ciphertextMovedToAnotherUserOrFieldDoesNotOpen() {
        long other = devices.register().identity().userId();
        String sealed = cipher.encrypt(user, TurnStore.TEXT, "내 이야기");

        assertThat(cipher.decrypt(user, TurnStore.TEXT, sealed)).isEqualTo("내 이야기");
        assertThatThrownBy(() -> cipher.decrypt(other, TurnStore.TEXT, sealed))
                .hasMessageContaining("복호화 실패");
        assertThatThrownBy(() -> cipher.decrypt(user, FactStore.TEXT, sealed))
                .hasMessageContaining("복호화 실패");
    }

    @Test
    void eachUserGetsAWrappedKeyOfTheirOwn() {
        long other = devices.register().identity().userId();
        cipher.encrypt(user, "t", "a");
        cipher.encrypt(other, "t", "b");

        List<byte[]> keys = jdbc.sql("SELECT data_key FROM app_user WHERE user_id IN (?, ?)")
                .params(user, other).query((rs, n) -> rs.getBytes(1)).list();
        assertThat(keys).hasSize(2).allSatisfy(k -> assertThat(k).hasSize(12 + 32 + 16));
        assertThat(keys.get(0)).isNotEqualTo(keys.get(1));
    }

    @Test
    void droppingTheUsersKeyMakesTheirTextUnreadable() {
        // 삭제 경로(G8 ④)의 토대 — 키를 지우면 백업에 남은 행까지 되돌릴 수 없다.
        String sealed = cipher.encrypt(user, TurnStore.TEXT, "지워져야 할 말");
        jdbc.sql("UPDATE app_user SET data_key = NULL WHERE user_id = ?").param(user).update();
        cipher.forget(user);

        assertThatThrownBy(() -> cipher.decrypt(user, TurnStore.TEXT, sealed))
                .hasMessageContaining("복호화 실패");
    }

    @Test
    void playbackTruncationStillCutsAtTheHeardPosition() {
        UUID id = UUID.randomUUID();
        long turn = turns.begin(user, id, "navi", TurnKind.REACTIVE, "얘기해 줘", "m");
        turns.finishProvisional(turn, "😀 첫 문장. 둘째 문장.", "neutral", Usage.ZERO, 1, 2);

        turns.confirm(user, id, 7, true);

        assertThat(turns.recallRecent(user, "navi", 10).getLast())
                .isEqualTo(Message.assistant("😀 첫 문장."));
        assertThat(rawMessages(user).getLast()).startsWith("enc:v1:");
    }

    @Test
    void plaintextLeftFromBeforeIsReadAndThenEncryptedAtBoot() throws Exception {
        long turn = turns.begin(user, UUID.randomUUID(), "navi", TurnKind.REACTIVE, "옛 말", "m");
        jdbc.sql("UPDATE turn_message SET text = '옛 평문' WHERE turn_id = ?").param(turn).update();
        assertThat(cipher.decrypt(user, TurnStore.TEXT, "옛 평문")).isEqualTo("옛 평문");

        new LegacyPlaintextEncryptor(jdbc, cipher).run(null);

        String raw = rawMessages(user).getFirst();
        assertThat(raw).startsWith("enc:v1:");
        assertThat(cipher.decrypt(user, TurnStore.TEXT, raw)).isEqualTo("옛 평문");
    }

    @Test
    void serverRefusesToStartWithoutAProperKey() {
        assertThatThrownBy(() -> new FieldCipher(new CryptoProperties(""), jdbc))
                .hasMessageContaining("NAVI_DATA_KEY");
        assertThatThrownBy(() -> new FieldCipher(new CryptoProperties("c2hvcnQ="), jdbc))
                .hasMessageContaining("32바이트");
    }
}
