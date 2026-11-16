package navi.server.assembly;

import static org.assertj.core.api.Assertions.assertThat;

import java.util.ArrayList;
import java.util.List;
import java.util.stream.Stream;
import navi.server.Fixtures;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.params.ParameterizedTest;
import org.junit.jupiter.params.provider.Arguments;
import org.junit.jupiter.params.provider.MethodSource;
import tools.jackson.databind.JsonNode;

/** 무드 태그 분리가 Python 원본과 같은지 — 토큰이 어디서 잘려 와도. */
class MoodPeelerFixtureTest {

    static Stream<Arguments> peelCases() {
        List<Arguments> out = new ArrayList<>();
        for (JsonNode c : Fixtures.read("mood.json").get("peel")) {
            out.add(Arguments.of(c.get("name").asString(), c));
        }
        assertThat(out).isNotEmpty();
        return out.stream();
    }

    @ParameterizedTest(name = "{0}")
    @MethodSource("peelCases")
    void peelsSameAsPython(String name, JsonNode c) {
        MoodPeeler peeler = new MoodPeeler();
        StringBuilder body = new StringBuilder();
        for (JsonNode token : c.get("tokens")) {
            body.append(peeler.feed(token.asString()));
        }
        body.append(peeler.finish());

        assertThat(peeler.mood()).contains(c.get("mood").asString());
        assertThat(body.toString()).isEqualTo(c.get("body").asString());
    }

    static Stream<Arguments> stripCases() {
        List<Arguments> out = new ArrayList<>();
        for (JsonNode c : Fixtures.read("mood.json").get("strip")) {
            out.add(Arguments.of(c.get("text").asString(), c.get("expected").asString()));
        }
        return out.stream();
    }

    @ParameterizedTest
    @MethodSource("stripCases")
    void stripsSameAsPython(String text, String expected) {
        assertThat(MoodPeeler.strip(text)).isEqualTo(expected);
    }

    @Test
    void moodIsUnknownUntilDecided() {
        // 서버는 무드가 정해진 뒤에야 본문을 보낸다 — 판정 전에는 값을 주지 않아야 한다.
        MoodPeeler peeler = new MoodPeeler();
        assertThat(peeler.feed("[mood:br")).isEmpty();
        assertThat(peeler.mood()).isEmpty();
        assertThat(peeler.feed("ight] 응")).isEqualTo("응");
        assertThat(peeler.mood()).contains("bright");
        assertThat(peeler.feed(".")).isEqualTo(".");
    }
}
