package navi.server.persona;

import java.nio.file.Files;
import java.nio.file.Path;
import java.util.List;
import java.util.Map;
import java.util.Optional;
import java.util.Set;
import java.util.TreeMap;
import navi.server.assembly.CharacterCard;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.stereotype.Component;

/**
 * 서버가 제공하는 페르소나 카드. 부팅 때 디렉터리에서 <b>제공 목록에 있는 카드만</b> 읽는다.
 * 키는 파일 이름(확장자 제외).
 *
 * <p>목록이 따로 있는 이유: {@code personas/}에는 시험용 카드({@code zz_*})와 저작권 때문에 로컬에만
 * 두는 카드가 섞여 있다. 디렉터리를 통째로 열면 그런 카드가 서비스 사용자에게 보이고, 누구나
 * 그 시스템 프롬프트로 턴을 돌릴 수 있다. 제공할 것을 명시한다(목록 밖은 모르는 페르소나다).
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
    public PersonaRegistry(
            @Value("${navi.personas.dir:../personas}") String dir,
            @Value("${navi.personas.offered:navi,example_kr,example_jp}") List<String> offered) {
        Path root = Path.of(dir).toAbsolutePath().normalize();
        if (!Files.isDirectory(root)) {
            throw new IllegalStateException("페르소나 디렉터리가 없습니다: " + root);
        }
        for (String raw : offered) {
            String key = raw.strip();
            if (key.isEmpty()) {
                continue;
            }
            if (!key.matches("[A-Za-z0-9_-]+")) {
                // 키가 곧 파일 이름이다 — 경로 문자를 받으면 디렉터리 밖을 읽게 된다.
                log.warn("페르소나 키가 이상해 건너뜁니다: {}", key);
                continue;
            }
            try {
                cards.put(key, CharacterCard.load(root.resolve(key + ".yaml")));
            } catch (RuntimeException e) {
                // 카드 하나가 없거나 깨졌다고 서버가 못 뜨면 안 된다. 그 카드만 뺀다.
                log.warn("페르소나 카드를 건너뜁니다: {} — {}", key, e.getMessage());
            }
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
