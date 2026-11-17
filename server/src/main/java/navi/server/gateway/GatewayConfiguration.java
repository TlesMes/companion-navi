package navi.server.gateway;

import java.util.Map;
import java.util.Optional;
import navi.server.identity.DeviceIdentity;
import navi.server.identity.DeviceService;
import navi.server.turn.TurnStore;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.boot.ApplicationRunner;
import org.springframework.boot.context.properties.EnableConfigurationProperties;
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;
import org.springframework.http.HttpHeaders;
import org.springframework.http.HttpStatus;
import org.springframework.http.server.ServerHttpRequest;
import org.springframework.http.server.ServerHttpResponse;
import org.springframework.web.socket.WebSocketHandler;
import org.springframework.web.socket.config.annotation.EnableWebSocket;
import org.springframework.web.socket.config.annotation.WebSocketConfigurer;
import org.springframework.web.socket.config.annotation.WebSocketHandlerRegistry;
import org.springframework.web.socket.server.HandshakeInterceptor;

@Configuration
@EnableWebSocket
@EnableConfigurationProperties(TurnProperties.class)
class GatewayConfiguration implements WebSocketConfigurer {

    private static final Logger log = LoggerFactory.getLogger(GatewayConfiguration.class);

    private final TurnStreamHandler handler;
    private final DeviceService devices;

    GatewayConfiguration(TurnStreamHandler handler, DeviceService devices) {
        this.handler = handler;
        this.devices = devices;
    }

    @Override
    public void registerWebSocketHandlers(WebSocketHandlerRegistry registry) {
        // 브라우저가 아니라 데몬이 붙는다 — Origin 검사는 의미가 없고, 인증은 토큰이 한다.
        registry.addHandler(handler, "/v1/turn-stream")
                .addInterceptors(new DeviceAuthInterceptor(devices))
                .setAllowedOriginPatterns("*");
    }

    /** 서버가 턴 도중 죽었으면 생성 중인 채 남은 턴이 있다 — 부팅 때 폐기한다. */
    @Bean
    ApplicationRunner discardAbandonedTurns(TurnStore store) {
        return args -> {
            int n = store.discardAbandoned();
            if (n > 0) {
                log.warn("생성 중인 채 남은 턴 {}건을 폐기했습니다", n);
            }
        };
    }

    /** 핸드셰이크에서 기기 토큰을 확인한다. 틀리면 연결이 열리지 않는다(401). */
    private record DeviceAuthInterceptor(DeviceService devices) implements HandshakeInterceptor {

        @Override
        public boolean beforeHandshake(
                ServerHttpRequest request, ServerHttpResponse response, WebSocketHandler wsHandler,
                Map<String, Object> attributes) {
            Optional<DeviceIdentity> identity = devices.authenticateHeader(
                    request.getHeaders().getFirst(HttpHeaders.AUTHORIZATION));
            if (identity.isEmpty()) {
                response.setStatusCode(HttpStatus.UNAUTHORIZED);
                return false;
            }
            attributes.put(TurnStreamHandler.IDENTITY_ATTRIBUTE, identity.get());
            return true;
        }

        @Override
        public void afterHandshake(
                ServerHttpRequest request, ServerHttpResponse response, WebSocketHandler wsHandler,
                Exception exception) {}
    }
}
