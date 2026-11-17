package navi.server.brain;

import static org.assertj.core.api.Assertions.assertThat;

import java.util.ArrayList;
import java.util.List;
import java.util.stream.Stream;
import navi.server.Fixtures;
import navi.server.assembly.Message;
import org.junit.jupiter.params.ParameterizedTest;
import org.junit.jupiter.params.provider.Arguments;
import org.junit.jupiter.params.provider.MethodSource;
import tools.jackson.databind.JsonNode;

/** 메시지 정규화가 Python 원본과 같은지 — 조립 픽스처의 정규화 전·후 쌍으로 본다. */
class AnthropicMessagesFixtureTest {

    static Stream<Arguments> cases() {
        List<Arguments> out = new ArrayList<>();
        for (JsonNode c : Fixtures.read("assembly.json").get("cases")) {
            JsonNode e = c.get("expected");
            out.add(Arguments.of(
                    c.get("name").asString(),
                    messages(e.get("messages")),
                    messages(e.get("anthropic_messages"))));
        }
        return out.stream();
    }

    @ParameterizedTest(name = "{0}")
    @MethodSource("cases")
    void normalizesSameAsPython(String name, List<Message> input, List<Message> expected) {
        assertThat(AnthropicMessages.normalize(input)).isEqualTo(expected);
    }

    private static List<Message> messages(JsonNode array) {
        List<Message> out = new ArrayList<>();
        for (JsonNode m : array) {
            out.add(new Message(m.get("role").asString(), m.get("text").asString()));
        }
        return out;
    }
}
