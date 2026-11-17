package navi.server.assembly;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;

import org.junit.jupiter.api.Test;

/** 카드 검증 — 픽스처가 다루지 않는 실패 경로. */
class CharacterCardTest {

    private static final String PROFILE =
            """
            profiles:
              - name: 기본
                min_intimacy: 0
                background: 배경
                traits: 말투
                example_dialogues:
                  - user: 안녕
                    assistant: 응
            """;

    @Test
    void rejectsMissingLanguage() {
        assertThatThrownBy(() -> CharacterCard.parse("character: 가\n" + PROFILE, "t"))
                .isInstanceOf(IllegalArgumentException.class)
                .hasMessageContaining("language가 없습니다");
    }

    @Test
    void rejectsUnsupportedLanguage() {
        assertThatThrownBy(
                        () -> CharacterCard.parse("character: 가\nlanguage: fr\n" + PROFILE, "t"))
                .isInstanceOf(IllegalArgumentException.class)
                .hasMessageContaining("지원하지 않는 language");
    }

    @Test
    void rejectsCardWithoutProfiles() {
        assertThatThrownBy(() -> CharacterCard.parse("character: 가\nlanguage: ko\n", "t"))
                .isInstanceOf(IllegalArgumentException.class)
                .hasMessageContaining("프로필이 없습니다");
    }

    @Test
    void ignoresVoiceSection() {
        // 목소리는 클라이언트 소유다 — 서버는 없는 wav·ckpt 경로 때문에 죽으면 안 된다.
        CharacterCard card =
                CharacterCard.parse(
                        "character: 가\nlanguage: ko\nvoice:\n  name: x\n  gptsovits:\n"
                                + "    gpt_ckpt: 없는/경로.ckpt\n"
                                + PROFILE,
                        "t");
        assertThat(card.character()).isEqualTo("가");
        assertThat(card.systemPrompt()).contains("사용자: 안녕\n가: 응");
    }
}
