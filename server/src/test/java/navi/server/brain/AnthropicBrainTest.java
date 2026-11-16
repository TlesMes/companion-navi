package navi.server.brain;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;

import com.anthropic.client.okhttp.AnthropicOkHttpClient;
import java.util.ArrayList;
import java.util.List;
import java.util.concurrent.CompletableFuture;
import java.util.concurrent.CopyOnWriteArrayList;
import java.util.concurrent.CountDownLatch;
import java.util.concurrent.TimeUnit;
import navi.server.assembly.LlmRequest;
import navi.server.assembly.Message;
import org.junit.jupiter.api.AfterEach;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import tools.jackson.databind.JsonNode;
import tools.jackson.databind.ObjectMapper;

/** Anthropic 어댑터 — 진짜 SDK를 가짜 서버에 붙여 본다. */
class AnthropicBrainTest {

    private FakeAnthropic fake;
    private AnthropicBrain brain;

    @BeforeEach
    void setUp() throws Exception {
        fake = new FakeAnthropic();
        // 재시도를 끈다 — 실패 응답 테스트가 백오프를 기다리지 않게.
        brain = new AnthropicBrain(
                AnthropicOkHttpClient.builder()
                        .apiKey("test-key")
                        .baseUrl(fake.baseUrl())
                        .maxRetries(0)
                        .build(),
                1024);
    }

    @AfterEach
    void tearDown() {
        fake.close();
    }

    private static LlmRequest request(String context, Message... messages) {
        return new LlmRequest("카드", context, List.of(messages), "some-model");
    }

    @Test
    void streamsTokensInOrderAndReturnsResult() {
        fake.respondWith("[mood:bright]", " 안녕", ", 왔어?");
        List<String> tokens = new ArrayList<>();

        BrainResult result =
                brain.stream(request("", Message.user("나 왔어")), tokens::add, new CancelToken());

        assertThat(tokens).containsExactly("[mood:bright]", " 안녕", ", 왔어?");
        assertThat(result.fullText()).isEqualTo("[mood:bright] 안녕, 왔어?");
        assertThat(result.cancelled()).isFalse();
        // 입력은 message_start, 출력은 message_delta의 최종값. 캐시 토큰은 따로 센다.
        assertThat(result.usage()).isEqualTo(new Usage(7, 17, 13, 11));
        assertThat(result.usage().totalInputTokens()).isEqualTo(31);
    }

    @Test
    void sendsCardAndFactsAsSeparateCachedSystemBlocks() {
        fake.respondWith("응");

        brain.stream(request("[기억]\n- 사실", Message.user("안녕")), t -> {}, new CancelToken());

        JsonNode body = new ObjectMapper().readTree(fake.lastRequestBody.get());
        assertThat(fake.lastApiKey.get()).isEqualTo("test-key");
        assertThat(body.get("model").asString()).isEqualTo("some-model");
        assertThat(body.get("max_tokens").asLong()).isEqualTo(1024);
        assertThat(body.get("stream").asBoolean()).isTrue();
        JsonNode system = body.get("system");
        assertThat(system).hasSize(2);
        assertThat(system.get(0).get("text").asString()).isEqualTo("카드");
        assertThat(system.get(1).get("text").asString()).isEqualTo("[기억]\n- 사실");
        for (JsonNode block : system) {
            assertThat(block.get("cache_control").get("type").asString()).isEqualTo("ephemeral");
        }
    }

    @Test
    void omitsFactsBlockWhenThereAreNoFacts() {
        fake.respondWith("응");

        brain.stream(request("", Message.user("안녕")), t -> {}, new CancelToken());

        JsonNode system = new ObjectMapper().readTree(fake.lastRequestBody.get()).get("system");
        assertThat(system).hasSize(1);
    }

    @Test
    void normalizesMessagesBeforeSending() {
        fake.respondWith("응");

        brain.stream(
                request("", Message.assistant("일어났어?"), Message.user("응"), Message.user("방금")),
                t -> {},
                new CancelToken());

        JsonNode messages = new ObjectMapper().readTree(fake.lastRequestBody.get()).get("messages");
        assertThat(messages).hasSize(3);
        assertThat(messages.get(0).get("role").asString()).isEqualTo("user");
        assertThat(messages.get(0).get("content").asString()).isEqualTo("(대화 시작)");
        assertThat(messages.get(1).get("role").asString()).isEqualTo("assistant");
        assertThat(messages.get(2).get("content").asString()).isEqualTo("응\n방금");
    }

    @Test
    void rejectedKeyBecomesAuthFailure() {
        fake.failWith(401);
        assertThatThrownBy(
                        () -> brain.stream(request("", Message.user("안녕")), t -> {}, new CancelToken()))
                .isInstanceOf(BrainException.Auth.class);
    }

    @Test
    void vendorOutageBecomesUnavailableWithoutLeakingTheConversation() {
        fake.failWith(529);
        assertThatThrownBy(
                        () -> brain.stream(
                                request("", Message.user("비밀 이야기")), t -> {}, new CancelToken()))
                .isInstanceOf(BrainException.Unavailable.class)
                // 예외 메시지는 로그로 간다 — 대화 본문이 섞이면 안 된다(G8 ③).
                .hasMessageNotContaining("비밀 이야기");
    }

    @Test
    void cancelStopsTheStreamAndClosesTheVendorConnection() throws Exception {
        fake.respondWith("하나", "둘", "셋", "넷").stallAfter(2);
        CancelToken cancel = new CancelToken();
        List<String> tokens = new CopyOnWriteArrayList<>();
        CountDownLatch gotTwo = new CountDownLatch(2);

        CompletableFuture<BrainResult> call =
                CompletableFuture.supplyAsync(
                        () -> brain.stream(
                                request("", Message.user("세어 봐")),
                                t -> {
                                    tokens.add(t);
                                    gotTwo.countDown();
                                },
                                cancel));
        // 서버가 멈춰 섰고 **클라이언트가 앞의 둘을 받은 뒤에** 끊는다.
        assertThat(fake.awaitStalled()).isTrue();
        assertThat(gotTwo.await(5, TimeUnit.SECONDS)).isTrue();
        cancel.cancel();

        BrainResult result = call.get(5, TimeUnit.SECONDS);
        assertThat(result.cancelled()).isTrue();
        assertThat(result.fullText()).isEqualTo("하나둘");
        assertThat(tokens).containsExactly("하나", "둘");
        // 서버 쪽에서 연결이 끊긴 게 보여야 한다 — 안 끊기면 쓰지 않을 출력에 돈을 낸다.
        assertThat(fake.clientGone.await(5, TimeUnit.SECONDS)).isTrue();
    }

    @Test
    void cancelledBeforeStartSendsNothingToTheCaller() {
        fake.respondWith("하나");
        CancelToken cancel = new CancelToken();
        cancel.cancel();
        List<String> tokens = new ArrayList<>();

        BrainResult result = brain.stream(request("", Message.user("안녕")), tokens::add, cancel);

        assertThat(result.cancelled()).isTrue();
        assertThat(tokens).isEmpty();
    }
}
