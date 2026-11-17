package navi.server.gateway;

import static org.assertj.core.api.Assertions.assertThat;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.never;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.when;

import java.time.Clock;
import java.time.Instant;
import java.time.ZoneOffset;
import navi.server.PostgresTestConfig;
import navi.server.identity.DeviceIdentity;
import navi.server.identity.DeviceService;
import org.junit.jupiter.api.Nested;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.boot.test.context.SpringBootTest.WebEnvironment;
import org.springframework.boot.test.web.server.LocalServerPort;
import org.springframework.context.annotation.Import;
import org.springframework.web.socket.CloseStatus;
import org.springframework.web.socket.PingMessage;
import org.springframework.web.socket.WebSocketSession;
import tools.jackson.databind.json.JsonMapper;

/** 하트비트 — 대답하는 연결은 남고, 대답이 끊긴 연결은 닫힌다. */
class HeartbeatTest {

    /** 움직일 수 있는 시계. */
    static final class MovableClock extends Clock {
        Instant now = Instant.parse("2026-11-18T00:00:00Z");

        @Override
        public Instant instant() {
            return now;
        }

        @Override
        public java.time.ZoneId getZone() {
            return ZoneOffset.UTC;
        }

        @Override
        public Clock withZone(java.time.ZoneId zone) {
            return this;
        }
    }

    private static ClientConnection connection(WebSocketSession session, long user) {
        when(session.isOpen()).thenReturn(true);
        when(session.getId()).thenReturn("s" + user);
        return new ClientConnection(new DeviceIdentity(user, user), session, JsonMapper.builder().build());
    }

    @Test
    void silentConnectionIsClosedAndLiveOneIsPinged() throws Exception {
        MovableClock clock = new MovableClock();
        ActiveConnections connections =
                new ActiveConnections(new ConnectionProperties(1000, 3000), clock, false);
        WebSocketSession silentSession = mock(WebSocketSession.class);
        WebSocketSession liveSession = mock(WebSocketSession.class);
        ClientConnection silent = connection(silentSession, 1);
        ClientConnection live = connection(liveSession, 2);
        connections.connect(silent);
        connections.connect(live);

        clock.now = clock.now.plusMillis(2500);
        live.markPong(clock.now); // 하나만 대답했다
        assertThat(connections.beat()).isZero();

        clock.now = clock.now.plusMillis(1000); // 조용한 쪽은 3.5초째 대답이 없다
        assertThat(connections.beat()).isEqualTo(1);

        verify(silentSession).close(CloseStatus.SESSION_NOT_RELIABLE);
        verify(liveSession, never()).close(any());
        verify(liveSession, org.mockito.Mockito.atLeastOnce()).sendMessage(any(PingMessage.class));
    }

    /** 진짜 서버·진짜 소켓 — 주기를 짧게 줄여 타임아웃보다 오래 붙어 있어 본다. */
    @Nested
    @SpringBootTest(webEnvironment = WebEnvironment.RANDOM_PORT, properties = {
        "navi.connection.ping-interval-ms=100", "navi.connection.pong-timeout-ms=400",
    })
    @Import({PostgresTestConfig.class, TurnStreamTest.Brain.class})
    class Live {

        @LocalServerPort int port;
        @Autowired DeviceService devices;
        @Autowired ScriptedBrain brain;

        @Test
        void answeringClientStaysConnectedPastTheTimeout() throws Exception {
            brain.reset();
            try (TestClient client = TestClient.connect(port, devices.register().token())) {
                client.next("session.ready");

                Thread.sleep(1200); // 타임아웃(400ms)의 세 배 — pong이 안 닿았으면 이미 닫혔다

                assertThat(client.pings.get()).isGreaterThanOrEqualTo(5);
                client.start("아직 있어?");
                assertThat(client.until("turn.end")).isNotEmpty();
            }
        }
    }
}
