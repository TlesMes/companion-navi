package navi.server.assembly;

import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.ArrayList;
import java.util.Comparator;
import java.util.List;
import java.util.Map;
import java.util.Set;
import java.util.stream.Collectors;
import org.yaml.snakeyaml.LoaderOptions;
import org.yaml.snakeyaml.Yaml;
import org.yaml.snakeyaml.constructor.SafeConstructor;

/**
 * 캐릭터 카드 — YAML을 읽어 시스템 프롬프트로 만든다. 원본은 {@code navi/persona/card.py}.
 *
 * <p>말투 예시를 messages가 아니라 시스템 프롬프트 안에 넣는 건 원본의 결정이다: messages에
 * 넣으면 모델이 예시를 실제 있었던 대화로 착각한다.
 *
 * <p>카드의 {@code voice} 섹션(음색·톤)은 읽지 않는다 — 목소리는 클라이언트 소유다(service.md
 * §16). 서버가 아는 건 글과 언어뿐이다.
 */
public record CharacterCard(
        String character, String language, String profileName, String background, String traits,
        List<Example> examples) {

    /** 말투 예시 한 쌍. */
    public record Example(String user, String assistant) {}

    /** 발화 언어로 쓸 수 있는 값 — Python {@code SUPPORTED_LANGUAGES}와 같다. */
    public static final Set<String> SUPPORTED_LANGUAGES = Set.of("ko", "ja", "zh", "en");

    public CharacterCard {
        examples = List.copyOf(examples);
    }

    public static CharacterCard load(Path path) {
        String text;
        try {
            text = Files.readString(path, StandardCharsets.UTF_8);
        } catch (IOException e) {
            throw new IllegalArgumentException("캐릭터 카드를 읽지 못했습니다: " + path, e);
        }
        return parse(text, path.toString());
    }

    @SuppressWarnings("unchecked")
    static CharacterCard parse(String yamlText, String source) {
        Map<String, Object> raw =
                new Yaml(new SafeConstructor(new LoaderOptions())).load(yamlText);
        if (raw == null) {
            throw new IllegalArgumentException("캐릭터 카드가 비어 있습니다: " + source);
        }

        List<Map<String, Object>> profiles =
                new ArrayList<>((List<Map<String, Object>>) raw.getOrDefault("profiles", List.of()));
        if (profiles.isEmpty()) {
            throw new IllegalArgumentException("캐릭터 카드에 프로필이 없습니다: " + source);
        }
        // 친밀도(D9)는 폐기됐고 원본은 늘 친밀도 0으로 고른다: min_intimacy 오름차순에서
        // 0 이하인 마지막 프로필, 없으면 첫 프로필. 카드는 사실상 프로필 하나지만 원본과
        // 같은 것을 고르도록 규칙을 그대로 옮긴다. (정렬은 안정 정렬 — Python sorted와 같다.)
        profiles.sort(Comparator.comparingDouble(p -> number(p.get("min_intimacy"))));
        Map<String, Object> chosen = profiles.get(0);
        for (Map<String, Object> p : profiles) {
            if (0.0 >= number(p.get("min_intimacy"))) {
                chosen = p;
            }
        }

        String language = str(raw.getOrDefault("language", "")).strip();
        String supported = SUPPORTED_LANGUAGES.stream().sorted().collect(Collectors.joining(", "));
        if (language.isEmpty()) {
            throw new IllegalArgumentException(
                    "캐릭터 카드에 language가 없습니다: " + source + " (가능한 값: " + supported + ")");
        }
        if (!SUPPORTED_LANGUAGES.contains(language)) {
            throw new IllegalArgumentException(
                    "지원하지 않는 language: '" + language + "' (" + source + ") — 가능한 값: "
                            + supported);
        }

        List<Example> examples =
                ((List<Map<String, Object>>) chosen.get("example_dialogues"))
                        .stream()
                                .map(d -> new Example(str(d.get("user")), str(d.get("assistant"))))
                                .toList();

        return new CharacterCard(
                str(raw.get("character")),
                language,
                str(chosen.get("name")),
                str(chosen.get("background")).strip(),
                str(chosen.get("traits")).strip(),
                examples);
    }

    /**
     * 시스템 프롬프트. 캐시 대상이라 같은 카드면 매번 같은 문자열이어야 한다.
     *
     * <p>문구는 원본과 한 글자도 다르면 안 된다 — 공유 픽스처가 지킨다.
     */
    public String systemPrompt() {
        String rendered =
                examples.stream()
                        .map(e -> "사용자: " + e.user() + "\n" + character + ": " + e.assistant())
                        .collect(Collectors.joining("\n\n"));
        // 틀에 자리 표시를 두고 치환하지 않고 토막을 잇는다 — 카드 본문에 자리 표시와 같은
        // 글자가 있어도 다시 치환될 일이 없다.
        return "너는 '" + character + "'다. 아래 캐릭터 설정에서 벗어나지 않는다.\n"
                + "\n"
                + "## 배경\n"
                + background + "\n"
                + "\n"
                + "## 성격과 말투 규칙\n"
                + traits + "\n"
                + "\n"
                + "## 말투 예시\n"
                + "아래는 말투를 보여주는 예시일 뿐, 실제로 있었던 대화가 아니다. 예시 속 내용을 기억처럼 언급하지 않는다.\n"
                + "\n"
                + rendered + "\n"
                + "\n"
                + "## 대화 규칙\n"
                + "- 이어지는 대화 기록만이 실제로 있었던 일이다. 기록에 없는 일을 기억하는 척하지 않는다.\n"
                + "- 현재 관계 단계: " + profileName + "\n"
                + "\n"
                + OUTPUT_FORMAT;
    }

    private static final String OUTPUT_FORMAT =
            """
            ## 출력 형식 (반드시 지킨다)
            - 응답의 **맨 앞에 반드시** `[mood:neutral]`·`[mood:bright]`·`[mood:calm]` 중 하나를 붙인다.
              neutral=평상, bright=신남·기쁨, calm=차분·위로. 애매하면 neutral.
            - 이 태그는 목소리 톤을 고르는 **시스템 신호이지 대사가 아니다** — 소리 내어 읽히지 않는다.
              태그 바로 뒤에 실제로 할 말을 잇는다. 예: `[mood:bright] 오, 자랑해 봐.`
            - 위 '말투 규칙'의 대괄호 금지는 *대사 안의* 행동·감정 묘사를 말한다. 이 **맨 앞 무드 태그
              1개는 예외**이며 필수다.""";

    private static double number(Object value) {
        return value instanceof Number n ? n.doubleValue() : Double.parseDouble(str(value));
    }

    private static String str(Object value) {
        return value == null ? "" : value.toString();
    }
}
