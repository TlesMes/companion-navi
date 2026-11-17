package navi.server.persona;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;

import java.util.List;
import navi.server.Fixtures;
import org.junit.jupiter.api.Test;

/** 제공 목록 — 디렉터리에 있어도 목록 밖이면 모르는 페르소나다. */
class PersonaRegistryTest {

    private static String dir() {
        return Fixtures.repoRoot().resolve("personas").toString();
    }

    @Test
    void onlyOfferedCardsAreLoaded() {
        PersonaRegistry registry = new PersonaRegistry(dir(), List.of("navi", "example_jp"));

        assertThat(registry.keys()).containsExactly("example_jp", "navi");
        // 시험용 카드는 파일이 있어도 서비스에 없다.
        assertThat(registry.find("zz_no_ckpt")).isEmpty();
        assertThat(registry.find("example_kr")).isEmpty();
    }

    @Test
    void missingOrPathLikeKeysAreSkipped() {
        PersonaRegistry registry =
                new PersonaRegistry(dir(), List.of("navi", "no_such_card", "../config", " "));

        assertThat(registry.keys()).containsExactly("navi");
    }

    @Test
    void emptyOfferingRefusesToStart() {
        assertThatThrownBy(() -> new PersonaRegistry(dir(), List.of("no_such_card")))
                .isInstanceOf(IllegalStateException.class);
    }
}
