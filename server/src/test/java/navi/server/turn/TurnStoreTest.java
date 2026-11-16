package navi.server.turn;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;

import java.util.List;
import java.util.UUID;
import navi.server.PostgresTestConfig;
import navi.server.assembly.Message;
import navi.server.assembly.TurnKind;
import navi.server.brain.Usage;
import navi.server.identity.DeviceService;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.context.annotation.Import;
import org.springframework.jdbc.core.simple.JdbcClient;

/** 턴의 한살이 — 무엇이 대화 기록에 남고 무엇이 없던 일이 되는가. */
@SpringBootTest
@Import(PostgresTestConfig.class)
class TurnStoreTest {

    private static final String NAVI = "navi";
    private static final Usage USAGE = new Usage(100, 20, 3, 4);

    @Autowired TurnStore store;
    @Autowired DeviceService devices;
    @Autowired JdbcClient jdbc;
    long user;

    @BeforeEach
    void setUp() {
        user = devices.register().identity().userId();
    }

    /** 한 왕복을 끝까지(임시 저장까지) 보낸다. */
    private UUID exchange(String said, String replied) {
        UUID id = UUID.randomUUID();
        long turn = store.begin(user, id, NAVI, TurnKind.REACTIVE, said, "m");
        store.finishProvisional(turn, replied, "neutral", USAGE, 300, 900);
        return id;
    }

    private String status(UUID id) {
        return jdbc.sql("SELECT status FROM turn WHERE user_id = ? AND client_turn_id = ?")
                .params(user, id).query(String.class).single();
    }

    @Test
    void generatingTurnIsNotYetPartOfTheConversation() {
        store.begin(user, UUID.randomUUID(), NAVI, TurnKind.REACTIVE, "안녕", "m");

        assertThat(store.recallRecent(user, NAVI, 20)).isEmpty();
    }

    @Test
    void provisionalTurnIsRecalledBeforePlaybackReport() {
        // 재생 보고는 다음 발화보다 늦게 올 수 있다 — 그 사이에도 방금 한 말은 알아야 한다.
        exchange("안녕", "응, 왔어?");

        assertThat(store.recallRecent(user, NAVI, 20))
                .containsExactly(Message.user("안녕"), Message.assistant("응, 왔어?"));
    }

    @Test
    void recallReturnsNewestRowsInChronologicalOrder() {
        exchange("하나", "1");
        exchange("둘", "2");
        exchange("셋", "3");

        assertThat(store.recallRecent(user, NAVI, 3))
                .containsExactly(Message.assistant("2"), Message.user("셋"), Message.assistant("3"));
    }

    @Test
    void playbackReportTruncatesToWhatWasHeard() {
        UUID id = exchange("얘기해 줘", "첫 문장이야. 둘째 문장이야.");

        assertThat(store.confirm(user, id, 7, true)).isTrue();

        assertThat(store.recallRecent(user, NAVI, 20).getLast())
                .isEqualTo(Message.assistant("첫 문장이야."));
        assertThat(status(id)).isEqualTo("confirmed");
        assertThat(jdbc.sql("SELECT interrupted FROM turn WHERE client_turn_id = ?")
                .param(id).query(Boolean.class).single()).isTrue();
    }

    @Test
    void truncationCountsCodePointsNotUtf16Units() {
        // 이모지는 UTF-16으로 2단위다. 클라이언트(Python)는 코드 포인트로 센다 — 같은 단위여야 한다.
        UUID id = exchange("기분 어때", "😀😀 좋아");

        store.confirm(user, id, 2, true);

        assertThat(store.recallRecent(user, NAVI, 20).getLast()).isEqualTo(Message.assistant("😀😀"));
    }

    @Test
    void reportLongerThanTheReplyKeepsItWhole() {
        UUID id = exchange("안녕", "응.");

        store.confirm(user, id, 999, false);

        assertThat(store.recallRecent(user, NAVI, 20).getLast()).isEqualTo(Message.assistant("응."));
    }

    @Test
    void nothingHeardRemovesTheReplyButKeepsWhatTheUserSaid() {
        UUID id = exchange("안녕", "응, 왔어?");

        store.confirm(user, id, 0, true);

        assertThat(store.recallRecent(user, NAVI, 20)).containsExactly(Message.user("안녕"));
    }

    @Test
    void confirmIsRejectedForAnotherUsersTurnAndForRepeats() {
        UUID id = exchange("안녕", "응, 왔어?");
        long other = devices.register().identity().userId();

        assertThat(store.confirm(other, id, 1, true)).isFalse();
        assertThat(store.confirm(user, id, 999, false)).isTrue();
        // 두 번째 보고가 이미 확정된 본문을 다시 자르면 안 된다.
        assertThat(store.confirm(user, id, 1, true)).isFalse();
        assertThat(store.recallRecent(user, NAVI, 20).getLast())
                .isEqualTo(Message.assistant("응, 왔어?"));
    }

    @Test
    void discardedTurnNeverHappenedButItsCostIsKept() {
        UUID id = UUID.randomUUID();
        long turn = store.begin(user, id, NAVI, TurnKind.REACTIVE, "안녕", "m");

        store.discard(turn, USAGE, 400);

        assertThat(store.recallRecent(user, NAVI, 20)).isEmpty();
        assertThat(jdbc.sql("SELECT count(*) FROM turn_message WHERE turn_id = ?")
                .param(turn).query(Long.class).single()).isZero();
        assertThat(status(id)).isEqualTo("discarded");
        assertThat(jdbc.sql("SELECT input_tokens + output_tokens FROM turn WHERE turn_id = ?")
                .param(turn).query(Long.class).single()).isEqualTo(120);
    }

    @Test
    void lateFinishDoesNotResurrectADiscardedTurn() {
        // 연결 끊김(폐기)과 생성 종료가 엇갈린 경우.
        UUID id = UUID.randomUUID();
        long turn = store.begin(user, id, NAVI, TurnKind.REACTIVE, "안녕", "m");
        store.discard(turn, Usage.ZERO, 10);

        store.finishProvisional(turn, "응", "neutral", USAGE, 1, 2);

        assertThat(status(id)).isEqualTo("discarded");
        assertThat(store.recallRecent(user, NAVI, 20)).isEmpty();
    }

    @Test
    void unreportedProvisionalTurnsAreConfirmedWhole() {
        UUID id = exchange("안녕", "응, 왔어?");

        assertThat(store.confirmUnreported(user)).isEqualTo(1);

        assertThat(status(id)).isEqualTo("confirmed");
        assertThat(store.recallRecent(user, NAVI, 20)).hasSize(2);
    }

    @Test
    void sameTurnIdTwiceIsRejected() {
        UUID id = UUID.randomUUID();
        store.begin(user, id, NAVI, TurnKind.REACTIVE, "안녕", "m");

        assertThatThrownBy(() -> store.begin(user, id, NAVI, TurnKind.REACTIVE, "안녕", "m"))
                .isInstanceOf(DuplicateTurnException.class);
        // 다른 사용자의 같은 ID는 충돌이 아니다 — ID는 클라이언트가 만든다.
        long other = devices.register().identity().userId();
        store.begin(other, id, NAVI, TurnKind.REACTIVE, "안녕", "m");
    }

    @Test
    void proactiveMaterialIsNotRecordedAsSomethingTheUserSaid() {
        long turn = store.begin(
                user, UUID.randomUUID(), NAVI, TurnKind.PROACTIVE, "GTA 6 출시가 미뤄졌다.", "m");
        store.finishProvisional(turn, "GTA 6 또 밀렸대.", "neutral", USAGE, 1, 2);

        assertThat(store.recallRecent(user, NAVI, 20))
                .containsExactly(Message.assistant("GTA 6 또 밀렸대."));
    }

    @Test
    void conversationsAreSeparatePerPersonaAndPerUser() {
        exchange("나비야", "응");
        long turn = store.begin(user, UUID.randomUUID(), "example_jp", TurnKind.REACTIVE, "レイ", "m");
        store.finishProvisional(turn, "はい", "neutral", USAGE, 1, 2);
        long other = devices.register().identity().userId();

        assertThat(store.recallRecent(user, NAVI, 20)).extracting(Message::text)
                .containsExactly("나비야", "응");
        assertThat(store.recallRecent(user, "example_jp", 20)).extracting(Message::text)
                .containsExactly("レイ", "はい");
        assertThat(store.recallRecent(other, NAVI, 20)).isEmpty();
    }

    @Test
    void turnsLeftGeneratingByACrashAreDiscardedAtBoot() {
        UUID crashed = UUID.randomUUID();
        store.begin(user, crashed, NAVI, TurnKind.REACTIVE, "안녕", "m");
        UUID finished = exchange("안녕", "응");

        store.discardAbandoned();

        assertThat(status(crashed)).isEqualTo("discarded");
        assertThat(status(finished)).isEqualTo("provisional");
        assertThat(List.copyOf(store.recallRecent(user, NAVI, 20))).hasSize(2);
    }

    @Test
    void clientMetricsAttachOnlyToTheOwnersTurn() {
        UUID id = exchange("안녕", "응");
        long other = devices.register().identity().userId();

        assertThat(store.recordClientMetrics(other, id, "{\"ttfa_ms\":1}")).isFalse();
        assertThat(store.recordClientMetrics(user, id, "{\"ttfa_ms\":812}")).isTrue();
        assertThat(jdbc.sql("SELECT (client_metrics->>'ttfa_ms')::int FROM turn WHERE client_turn_id = ?")
                .param(id).query(Integer.class).single()).isEqualTo(812);
    }
}
