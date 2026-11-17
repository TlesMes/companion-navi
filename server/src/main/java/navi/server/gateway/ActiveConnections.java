package navi.server.gateway;

import java.time.Clock;
import java.time.Duration;
import java.time.Instant;
import java.util.ArrayList;
import java.util.HashMap;
import java.util.LinkedList;
import java.util.List;
import java.util.Map;
import java.util.concurrent.Executors;
import java.util.concurrent.ScheduledExecutorService;
import java.util.concurrent.TimeUnit;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.beans.factory.DisposableBean;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.context.properties.EnableConfigurationProperties;
import org.springframework.stereotype.Component;
import org.springframework.web.socket.CloseStatus;

/**
 * 사용자별 연결 — 활성 하나 + 대기 여럿(D20), 그리고 하트비트.
 *
 * <p><b>활성권:</b> 새로 접속한 연결이 가져간다(기기를 켰다 = 사용자가 그 앞에 있다). 대기 연결에서
 * 사용자가 말을 걸면(반응 턴) 그 연결이 가져간다 — 말을 거는 쪽은 무엇과도 상관없이 이긴다.
 * 밀린 연결은 끊지 않는다: 아무도 앞에 없는 기기에서 혼자 도는 데몬이라 로그아웃시키면 다시
 * 들여보낼 사람이 없다. 활성 연결이 닫히면 가장 최근에 활성이었던 대기 연결을 올린다.
 *
 * <p>계정이 없는 동안(D23) 사용자당 기기는 하나라, 여기서 여럿이 되는 건 같은 토큰을 쓰는 프로세스
 * 여럿(데몬 + 텍스트 CLI 등)이다. D20의 "기본 기기"와 단절 유예는 계정이 붙어 기기가 여럿이 될 때
 * 정한다 — 지금은 순간 단절로 밀린 연결도 다시 접속하면 활성권을 되찾는다.
 *
 * <p>상태는 이 프로세스의 메모리에 있다. 서버가 여러 대가 되면 이 자리가 공유 저장소로 가야
 * 한다(D25가 Redis의 첫 실수요로 짚은 곳).
 */
@Component
@EnableConfigurationProperties(ConnectionProperties.class)
class ActiveConnections implements DisposableBean {

    private static final Logger log = LoggerFactory.getLogger(ActiveConnections.class);

    /** 사용자별 연결. 맨 앞이 활성, 뒤는 최근에 활성이었던 순. */
    private final Map<Long, LinkedList<ClientConnection>> byUser = new HashMap<>();
    private final ConnectionProperties props;
    private final Clock clock;
    private final ScheduledExecutorService heartbeat;

    @Autowired
    ActiveConnections(ConnectionProperties props) {
        this(props, Clock.systemUTC(), true);
    }

    ActiveConnections(ConnectionProperties props, Clock clock, boolean schedule) {
        this.props = props;
        this.clock = clock;
        if (schedule) {
            this.heartbeat = Executors.newSingleThreadScheduledExecutor(
                    Thread.ofVirtual().name("heartbeat").factory());
            this.heartbeat.scheduleAtFixedRate(
                    this::beat, props.pingIntervalMs(), props.pingIntervalMs(), TimeUnit.MILLISECONDS);
        } else {
            this.heartbeat = null;
        }
    }

    /** 새 연결이 활성권을 가져간다. 밀린 연결을 돌려준다(대기로 알릴 대상). */
    synchronized ClientConnection connect(ClientConnection conn) {
        conn.markPong(clock.instant());
        LinkedList<ClientConnection> list = byUser.computeIfAbsent(userOf(conn), k -> new LinkedList<>());
        ClientConnection previous = list.peekFirst();
        list.addFirst(conn);
        return previous;
    }

    /**
     * 이 연결이 활성권을 가져간다(사용자가 그 기기에 말을 걸었다).
     *
     * @return 밀린 연결. 이미 활성이었으면 null
     */
    synchronized ClientConnection activate(ClientConnection conn) {
        LinkedList<ClientConnection> list = byUser.get(userOf(conn));
        if (list == null || list.peekFirst() == conn || !list.remove(conn)) {
            return null;
        }
        ClientConnection previous = list.peekFirst();
        list.addFirst(conn);
        return previous;
    }

    synchronized boolean isActive(ClientConnection conn) {
        LinkedList<ClientConnection> list = byUser.get(userOf(conn));
        return list != null && list.peekFirst() == conn;
    }

    /**
     * 연결이 닫혔다. 활성이었으면 가장 최근 대기 연결이 올라간다.
     *
     * @return 새로 활성이 된 연결(알릴 대상). 없으면 null
     */
    synchronized ClientConnection disconnect(ClientConnection conn) {
        LinkedList<ClientConnection> list = byUser.get(userOf(conn));
        if (list == null) {
            return null;
        }
        boolean wasActive = list.peekFirst() == conn;
        list.remove(conn);
        if (list.isEmpty()) {
            byUser.remove(userOf(conn));
            return null;
        }
        return wasActive ? list.peekFirst() : null;
    }

    /**
     * 하트비트 한 번: 대답이 끊긴 연결은 닫고, 나머지에는 ping을 보낸다.
     *
     * @return 닫은 연결 수
     */
    int beat() {
        Instant now = clock.instant();
        List<ClientConnection> all;
        synchronized (this) {
            all = byUser.values().stream().flatMap(List::stream).toList();
        }
        List<ClientConnection> dead = new ArrayList<>();
        Duration timeout = Duration.ofMillis(props.pongTimeoutMs());
        for (ClientConnection conn : all) {
            if (Duration.between(conn.lastPong(), now).compareTo(timeout) > 0) {
                dead.add(conn);
            } else {
                conn.ping();
            }
        }
        for (ClientConnection conn : dead) {
            log.info("응답 없는 연결을 닫습니다 user={}", userOf(conn));
            // 닫힘 처리(진행 중 턴 폐기·활성권 넘김)는 afterConnectionClosed가 한다.
            conn.close(CloseStatus.SESSION_NOT_RELIABLE);
        }
        return dead.size();
    }

    Instant now() {
        return clock.instant();
    }

    private static long userOf(ClientConnection conn) {
        return conn.identity().userId();
    }

    @Override
    public void destroy() {
        if (heartbeat != null) {
            heartbeat.shutdownNow();
        }
    }
}
