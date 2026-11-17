package navi.server.memory;

import static org.assertj.core.api.Assertions.assertThat;

import java.time.OffsetDateTime;
import navi.server.PostgresTestConfig;
import navi.server.identity.DeviceService;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.context.annotation.Import;

/** 사실 인출 규칙 — 단독 모드 recall_relevant_facts와 같은 것을 고르는가. */
@SpringBootTest
@Import(PostgresTestConfig.class)
class FactStoreTest {

    @Autowired FactStore facts;
    @Autowired DeviceService devices;
    long user;

    @BeforeEach
    void setUp() {
        user = devices.register().identity().userId();
    }

    private long add(String kind, String text) {
        return facts.insert(user, kind, text, null, null);
    }

    @Test
    void profileFactsComeFirstThenTheNewestOfTheRest() {
        add("profile", "동생 이름은 민준이다.");
        add("preference", "매운 걸 못 먹는다.");
        add("ongoing", "이직을 준비 중이다.");
        add("profile", "서울에 산다.");
        add("plan", "주말에 등산을 간다.");

        // k=4: 신상 둘을 먼저 다 넣고, 남는 두 자리를 나머지의 최신 것으로. 각 구간은 오래된 순.
        assertThat(facts.recall(user, 4)).extracting(FactStore.Fact::text).containsExactly(
                "동생 이름은 민준이다.", "서울에 산다.", "이직을 준비 중이다.", "주말에 등산을 간다.");
    }

    @Test
    void profileAloneCanFillTheBudget() {
        add("preference", "고양이를 좋아한다.");
        add("profile", "하나");
        add("profile", "둘");
        add("profile", "셋");

        assertThat(facts.recall(user, 2)).extracting(FactStore.Fact::text).containsExactly("둘", "셋");
        assertThat(facts.recall(user, 0)).isEmpty();
    }

    @Test
    void expiredSupersededAndOtherUsersFactsAreNotRecalled() {
        long old = add("ongoing", "회사에 다닌다.");
        long fresh = add("ongoing", "회사를 그만뒀다.");
        facts.supersede(user, old, fresh);
        facts.insert(user, "plan", "어제 약속이 있었다.", null, OffsetDateTime.now().minusHours(1));
        facts.insert(user, "plan", "내일 약속이 있다.", null, OffsetDateTime.now().plusDays(1));
        long other = devices.register().identity().userId();
        facts.insert(other, "profile", "남의 사실.", null, null);

        assertThat(facts.recall(user, 10)).extracting(FactStore.Fact::text)
                .containsExactly("회사를 그만뒀다.", "내일 약속이 있다.");
        // 남의 사실은 대체할 수 없다.
        assertThat(facts.supersede(other, fresh, fresh)).isFalse();
    }

    @Test
    void cursorNeverMovesBackwards() {
        assertThat(facts.cursor(user)).isZero();
        facts.advanceCursor(user, 10);
        facts.advanceCursor(user, 4);

        assertThat(facts.cursor(user)).isEqualTo(10);
    }
}
