package navi.server.persona;

import java.io.IOException;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.Map;
import java.util.Optional;
import java.util.Set;
import java.util.TreeMap;
import java.util.stream.Stream;
import navi.server.assembly.CharacterCard;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.stereotype.Component;

/**
 * 서버가 아는 페르소나 카드. 부팅 때 디렉터리의 {@code *.yaml}을 읽는다. 키는 파일 이름(확장자 제외).
 *
 * <p>지금은 운영자 제공 카드뿐이다. 소유자·공개 범위·버전이 있는 {@code persona} 테이블과
 * 사용자가 만든 카드(service.md §16)는 뒤 단계다 — 그때 이 클래스가 저장소 뒤로 간다.
 *
 * <p>클라이언트는 키만 보낸다. 카드 본문을 받지 않는 것이 핵심이다: 받으면 임의 시스템
 * 프롬프트를 실어 주는 통로가 된다(service.md §2.2).
 */
@Component
public class PersonaRegistry {

    private static final Logger log = LoggerFactory.getLogger(PersonaRegistry.class);

    private final Map<String, CharacterCard> cards = new TreeMap<>();

    // 문자열로 받는다 — 웹 컨텍스트는 Path를 서블릿 리소스 경로로 바꾸려 들어 "../"에서 죽는다.
    public PersonaRegistry(@Value("${navi.personas.dir:../personas}") String dir) {
        Path root = Path.of(dir).toAbsolutePath().normalize();
        try (Stream<Path> files = Files.list(root)) {
            files.filter(p -> p.getFileName().toString().endsWith(".yaml")).sorted().forEach(p -> {
                String key = p.getFileName().toString().replaceFirst("\\.yaml$", "");
                try {
                    cards.put(key, CharacterCard.load(p));
                } catch (RuntimeException e) {
                    // 카드 하나가 깨졌다고 서버가 못 뜨면 안 된다. 그 카드만 뺀다.
                    log.warn("페르소나 카드를 건너뜁니다: {} — {}", key, e.getMessage());
                }
            });
        } catch (IOException e) {
            throw new IllegalStateException("페르소나 디렉터리를 읽지 못했습니다: " + root, e);
        }
        if (cards.isEmpty()) {
            throw new IllegalStateException("페르소나 카드가 하나도 없습니다: " + root);
        }
        log.info("페르소나 {}개: {}", cards.size(), cards.keySet());
    }

    public Optional<CharacterCard> find(String key) {
        return Optional.ofNullable(key).map(cards::get);
    }

    public Set<String> keys() {
        return cards.keySet();
    }
}
