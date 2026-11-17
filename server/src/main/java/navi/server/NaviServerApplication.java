package navi.server;

import org.springframework.boot.SpringApplication;
import org.springframework.boot.autoconfigure.SpringBootApplication;

/**
 * 턴 게이트웨이 — 서버 코어의 온라인 경로(docs/design/service.md §2.2, §11).
 *
 * <p>클라이언트가 발화 텍스트와 턴 종류를 보내면 서버가 요청을 조립하고 서버 키로 두뇌를 불러
 * 토큰을 흘려보낸다. 배치(사실 추출·피드)는 여기 없다 — Python 워커의 몫이다.
 */
@SpringBootApplication
public class NaviServerApplication {

    public static void main(String[] args) {
        SpringApplication.run(NaviServerApplication.class, args);
    }
}
