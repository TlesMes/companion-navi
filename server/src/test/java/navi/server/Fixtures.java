package navi.server;

import java.nio.file.Path;
import tools.jackson.databind.JsonNode;
import tools.jackson.databind.ObjectMapper;

/**
 * Python과 함께 쓰는 픽스처(저장소 루트의 {@code tests/fixtures})와 카드({@code personas}).
 *
 * <p>기대값은 Python 조립을 실제로 돌려 뜬 것이다({@code tests/assembly_fixtures.py}). 여기서
 * 테스트가 깨지면 서버 쪽 이식이 원본과 갈린 것이다 — 픽스처를 고치지 않는다.
 */
public final class Fixtures {

    private static final ObjectMapper MAPPER = new ObjectMapper();

    private Fixtures() {}

    public static Path repoRoot() {
        String root = System.getProperty("navi.repoRoot");
        if (root == null) {
            throw new IllegalStateException("navi.repoRoot가 없다 — Gradle test 태스크로 돌린다");
        }
        return Path.of(root);
    }

    public static Path persona(String file) {
        return repoRoot().resolve("personas").resolve(file);
    }

    public static JsonNode read(String name) {
        return MAPPER.readTree(repoRoot().resolve("tests/fixtures").resolve(name).toFile());
    }
}
