package navi.server.brain;

import static org.assertj.core.api.Assertions.assertThat;

import java.util.List;
import navi.server.assembly.LlmRequest;
import navi.server.assembly.Message;
import navi.server.assembly.MoodPeeler;
import org.junit.jupiter.api.Test;

class EchoBrainTest {

    @Test
    void echoesLastMessageWithMoodTagInSeveralChunks() {
        LlmRequest request = new LlmRequest("카드", "", List.of(Message.user("안녕 나비")), "echo");
        StringBuilder streamed = new StringBuilder();
        int[] chunks = {0};

        BrainResult result = new EchoBrain().stream(
                request, t -> { streamed.append(t); chunks[0]++; }, new CancelToken());

        assertThat(chunks[0]).isGreaterThan(1);
        assertThat(streamed.toString()).isEqualTo(result.fullText());
        // 실제 두뇌처럼 태그가 붙어 온다 — 태그 분리 경로가 키 없이도 돈다.
        assertThat(MoodPeeler.strip(result.fullText())).isEqualTo("(echo) 안녕 나비");
        assertThat(result.usage().outputTokens()).isPositive();
    }
}
