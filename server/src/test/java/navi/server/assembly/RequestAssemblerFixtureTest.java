package navi.server.assembly;

import static org.assertj.core.api.Assertions.assertThat;

import java.util.ArrayList;
import java.util.List;
import java.util.stream.Stream;
import navi.server.Fixtures;
import org.junit.jupiter.params.ParameterizedTest;
import org.junit.jupiter.params.provider.Arguments;
import org.junit.jupiter.params.provider.MethodSource;
import tools.jackson.databind.JsonNode;

/** 조립 결과가 Python 원본과 문자 단위로 같은지 — 공유 픽스처 전 건. */
class RequestAssemblerFixtureTest {

    static Stream<Arguments> cases() {
        List<Arguments> out = new ArrayList<>();
        for (JsonNode c : Fixtures.read("assembly.json").get("cases")) {
            out.add(Arguments.of(c.get("name").asString(), c));
        }
        assertThat(out).isNotEmpty();
        return out.stream();
    }

    @ParameterizedTest(name = "{0}")
    @MethodSource("cases")
    void assemblesSameRequestAsPython(String name, JsonNode c) {
        JsonNode input = c.get("input");
        JsonNode expected = c.get("expected");
        CharacterCard card = CharacterCard.load(Fixtures.persona(input.get("card").asString()));

        LlmRequest request =
                RequestAssembler.assemble(
                        card,
                        messages(input.get("turns")),
                        strings(input.get("facts")),
                        TurnKind.fromWire(input.get("kind").asString()).orElseThrow(),
                        input.get("trigger").asString(),
                        "fixture-model");

        assertThat(card.language()).isEqualTo(expected.get("language").asString());
        assertThat(request.system()).isEqualTo(expected.get("system").asString());
        assertThat(request.context()).isEqualTo(expected.get("context").asString());
        assertThat(request.messages()).isEqualTo(messages(expected.get("messages")));
        assertThat(request.model()).isEqualTo("fixture-model");
    }

    static List<Message> messages(JsonNode array) {
        List<Message> out = new ArrayList<>();
        for (JsonNode m : array) {
            out.add(new Message(m.get("role").asString(), m.get("text").asString()));
        }
        return out;
    }

    private static List<String> strings(JsonNode array) {
        List<String> out = new ArrayList<>();
        for (JsonNode s : array) {
            out.add(s.asString());
        }
        return out;
    }
}
