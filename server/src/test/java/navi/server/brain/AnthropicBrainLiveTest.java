package navi.server.brain;

import static org.assertj.core.api.Assertions.assertThat;

import java.util.List;
import navi.server.Fixtures;
import navi.server.assembly.CharacterCard;
import navi.server.assembly.LlmRequest;
import navi.server.assembly.MoodPeeler;
import navi.server.assembly.RequestAssembler;
import navi.server.assembly.TurnKind;
import org.junit.jupiter.api.Tag;
import org.junit.jupiter.api.Test;

/**
 * 실호출 스모크 — 조립한 요청을 진짜 벤더에 보내 페르소나 말투가 스트리밍되는지 본다.
 *
 * <p>{@code ./gradlew liveTest}로만 돈다(과금: Haiku 기준 한 번에 약 $0.003). 사람이 읽고
 * 판단하라고 응답을 그대로 출력한다 — 말투는 단언으로 못 잡는다.
 */
@Tag("live")
class AnthropicBrainLiveTest {

    @Test
    void streamsPersonaReplyFromRealVendor() {
        String model = System.getProperty("navi.liveModel", "claude-haiku-4-5");
        AnthropicBrain brain = AnthropicBrain.create(System.getenv("ANTHROPIC_API_KEY"), "", 1024);
        CharacterCard card = CharacterCard.load(Fixtures.persona("navi.yaml"));
        LlmRequest request = RequestAssembler.assemble(
                card, List.of(), List.of("사용자는 매운 음식을 못 먹는다."),
                TurnKind.REACTIVE, "나 방금 퇴근했어. 저녁 뭐 먹지?", model);

        MoodPeeler peeler = new MoodPeeler();
        StringBuilder body = new StringBuilder();
        int[] chunks = {0};
        long started = System.nanoTime();
        long[] firstTokenMs = {-1};

        BrainResult result = brain.stream(
                request,
                token -> {
                    if (firstTokenMs[0] < 0) {
                        firstTokenMs[0] = (System.nanoTime() - started) / 1_000_000;
                    }
                    chunks[0]++;
                    body.append(peeler.feed(token));
                },
                new CancelToken());
        body.append(peeler.finish());

        System.out.printf(
                "%n[live] model=%s ttft=%dms chunks=%d mood=%s%n[live] %s%n[live] usage=%s%n",
                model, firstTokenMs[0], chunks[0], peeler.mood().orElse("?"), body, result.usage());

        assertThat(result.cancelled()).isFalse();
        assertThat(chunks[0]).isGreaterThan(1);
        assertThat(body.toString()).isNotBlank().doesNotContain("[mood:");
        assertThat(peeler.mood()).isPresent();
        assertThat(result.usage().totalInputTokens()).isGreaterThan(500);
        assertThat(result.usage().outputTokens()).isPositive();
    }
}
