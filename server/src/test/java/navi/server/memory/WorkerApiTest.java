package navi.server.memory;

import static org.assertj.core.api.Assertions.assertThat;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.post;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.status;

import java.util.List;
import java.util.Map;
import java.util.UUID;
import navi.server.PostgresTestConfig;
import navi.server.assembly.TurnKind;
import navi.server.brain.Usage;
import navi.server.identity.DeviceService;
import navi.server.turn.TurnStore;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.context.annotation.Import;
import org.springframework.http.MediaType;
import org.springframework.jdbc.core.simple.JdbcClient;
import org.springframework.test.web.servlet.MockMvc;
import org.springframework.test.web.servlet.ResultActions;
import org.springframework.test.web.servlet.setup.MockMvcBuilders;
import org.springframework.web.context.WebApplicationContext;
import tools.jackson.databind.JsonNode;
import tools.jackson.databind.ObjectMapper;

/** 사실 추출 작업의 한 바퀴 — 언제 생기고, 워커에 무엇이 보이고, 결과를 어떻게 거르는가. */
@SpringBootTest(properties = {"navi.worker.token=test-worker", "navi.facts.min-new-user-turns=2"})
@Import(PostgresTestConfig.class)
class WorkerApiTest {

    private static final ObjectMapper JSON = new ObjectMapper();

    @Autowired WebApplicationContext context;
    @Autowired DeviceService devices;
    @Autowired TurnStore turns;
    @Autowired FactJobs factJobs;
    @Autowired FactStore facts;
    @Autowired JdbcClient jdbc;
    MockMvc mvc;
    long user;

    @BeforeEach
    void setUp() {
        mvc = MockMvcBuilders.webAppContextSetup(context).build();
        jdbc.sql("DELETE FROM job").update();
        user = devices.register().identity().userId();
    }

    /** 한 왕복을 확정까지. */
    private void confirmedExchange(String said, String replied) {
        UUID id = UUID.randomUUID();
        long turn = turns.begin(user, id, "navi", TurnKind.REACTIVE, said, "m");
        turns.finishProvisional(turn, replied, "neutral", Usage.ZERO, 1, 2);
        turns.confirm(user, id, 999, false);
        factJobs.maybeEnqueue(user);
    }

    private ResultActions postJson(String path, Object body, String token) throws Exception {
        var req = post(path).contentType(MediaType.APPLICATION_JSON)
                .content(JSON.writeValueAsString(body));
        if (token != null) {
            req.header("Authorization", "Bearer " + token);
        }
        return mvc.perform(req);
    }

    private JsonNode claim() throws Exception {
        String body = postJson("/v1/worker/jobs/claim", Map.of("kinds", List.of("extract_facts")),
                "test-worker").andReturn().getResponse().getContentAsString();
        return body.isEmpty() ? null : JSON.readTree(body);
    }

    private long jobs() {
        return jdbc.sql("SELECT count(*) FROM job WHERE user_id = ?").param(user)
                .query(Long.class).single();
    }

    @Test
    void workerApiNeedsTheSharedToken() throws Exception {
        postJson("/v1/worker/jobs/claim", Map.of(), null).andExpect(status().isUnauthorized());
        postJson("/v1/worker/jobs/claim", Map.of(), "wrong").andExpect(status().isUnauthorized());
        postJson("/v1/worker/jobs/claim", Map.of(), "test-worker").andExpect(status().isNoContent());
    }

    @Test
    void jobAppearsOnlyAfterEnoughConfirmedUserTurns() throws Exception {
        confirmedExchange("안녕", "응");
        assertThat(jobs()).isZero();

        confirmedExchange("나 동생 있어. 이름은 민준이야.", "민준이구나.");

        assertThat(jobs()).isEqualTo(1);
    }

    @Test
    void payloadShowsConfirmedTurnsUpToTheFirstUnfinishedOne() throws Exception {
        confirmedExchange("나 동생 있어.", "그래?");
        confirmedExchange("이름은 민준이야.", "민준이구나.");
        // 아직 재생 보고가 안 온 턴 — 잘릴 수 있으니 추출기에 보이면 안 되고, 그 뒤도 막힌다.
        long open = turns.begin(user, UUID.randomUUID(), "navi", TurnKind.REACTIVE, "임시", "m");
        turns.finishProvisional(open, "응", "neutral", Usage.ZERO, 1, 2);
        facts.insert(user, "preference", "매운 걸 못 먹는다.", null, null);

        JsonNode job = claim();

        JsonNode payload = job.get("payload");
        assertThat(payload.get("user_id").asLong()).isEqualTo(user);
        assertThat(payload.get("utc_offset_minutes").asInt()).isEqualTo(540); // Asia/Seoul
        assertThat(payload.get("turns")).extracting(t -> t.get("text").asString())
                .containsExactly("나 동생 있어.", "그래?", "이름은 민준이야.", "민준이구나.");
        assertThat(payload.get("known_facts").get(0).get("text").asString())
                .isEqualTo("매운 걸 못 먹는다.");
    }

    @Test
    void completeStoresCheckedFactsMovesTheCursorAndChargesTheUser() throws Exception {
        confirmedExchange("나 동생 있어.", "그래?");
        confirmedExchange("이름은 민준이야.", "민준이구나.");
        long old = facts.insert(user, "profile", "사용자는 외동이다.", null, null);
        long other = devices.register().identity().userId();
        long othersFact = facts.insert(other, "profile", "남의 사실.", null, null);
        long othersMessage = jdbc.sql("SELECT max(message_id) FROM turn_message WHERE user_id <> ?")
                .param(user).query(Long.class).optional().orElse(-1L);
        JsonNode job = claim();
        JsonNode turnsSeen = job.get("payload").get("turns");
        long src = turnsSeen.get(2).get("id").asLong();
        long last = turnsSeen.get(3).get("id").asLong();

        postJson("/v1/worker/jobs/" + job.get("job_id").asLong() + "/complete", Map.of(
                "attempt", job.get("attempt").asInt(),
                "facts", List.of(
                        Map.of("kind", "profile", "text", "사용자의 동생 이름은 민준이다.",
                                "source_id", src, "replaces", old),
                        Map.of("kind", "plan", "text", "사용자는 주말에 등산을 간다.",
                                "source_id", othersMessage, "replaces", othersFact,
                                "expires_at", "2099-01-01T00:00:00Z"),
                        Map.of("kind", "gossip", "text", "종류가 틀렸다."),
                        Map.of("kind", "profile", "text", "   ")),
                "cursor", last,
                "usage", Map.of("input_tokens", 1500, "output_tokens", 40)), "test-worker")
                .andExpect(status().isOk());

        assertThat(facts.recall(user, 10)).extracting(FactStore.Fact::text).containsExactly(
                "사용자의 동생 이름은 민준이다.", "사용자는 주말에 등산을 간다.");
        // 출처·대체는 그 사용자의 것일 때만 받는다.
        List<Long> sources = jdbc.sql(
                        "SELECT source_message_id FROM fact WHERE user_id = ? AND status = 'active' ORDER BY fact_id")
                .param(user).query((rs, n) -> rs.getObject(1, Long.class)).list();
        assertThat(sources).containsExactly(src, null); // 민준(제 발화) / 등산(남의 발화라 버림)
        assertThat(facts.recall(other, 10)).extracting(FactStore.Fact::text).containsExactly("남의 사실.");
        assertThat(facts.cursor(user)).isEqualTo(last);
        assertThat(jdbc.sql("SELECT tokens FROM usage_daily WHERE user_id = ?").param(user)
                .query(Long.class).single()).isEqualTo(1540);
        assertThat(jdbc.sql("SELECT status FROM job WHERE job_id = ?")
                .param(job.get("job_id").asLong()).query(String.class).single()).isEqualTo("done");
    }

    @Test
    void lateCompletionIsRejected() throws Exception {
        confirmedExchange("하나", "1");
        confirmedExchange("둘", "2");
        JsonNode job = claim();
        jdbc.sql("UPDATE job SET lease_until = now() - interval '1 second'").update();
        JsonNode again = claim(); // 첫 워커가 죽은 줄 알고 다시 내줬다

        postJson("/v1/worker/jobs/" + job.get("job_id").asLong() + "/complete",
                Map.of("attempt", job.get("attempt").asInt(), "facts", List.of(
                        Map.of("kind", "profile", "text", "늦은 사실."))), "test-worker")
                .andExpect(status().isConflict());

        assertThat(again.get("attempt").asInt()).isEqualTo(2);
        assertThat(facts.recall(user, 10)).isEmpty();
    }

    @Test
    void moreTalkDuringAJobQueuesTheNextOneAfterItCompletes() throws Exception {
        confirmedExchange("하나", "1");
        confirmedExchange("둘", "2");
        JsonNode job = claim();
        long cursor = job.get("payload").get("turns").get(3).get("id").asLong();
        confirmedExchange("셋", "3"); // 작업이 도는 동안 대화가 더 쌓였다
        confirmedExchange("넷", "4");
        assertThat(jobs()).isEqualTo(1); // 대기 작업은 하나뿐

        postJson("/v1/worker/jobs/" + job.get("job_id").asLong() + "/complete",
                Map.of("attempt", 1, "facts", List.of(), "cursor", cursor), "test-worker")
                .andExpect(status().isOk());

        assertThat(jobs()).isEqualTo(2);
        assertThat(claim().get("payload").get("turns")).extracting(t -> t.get("text").asString())
                .containsExactly("셋", "3", "넷", "4");
    }
}
