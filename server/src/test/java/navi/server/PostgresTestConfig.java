package navi.server;

import org.springframework.boot.test.context.TestConfiguration;
import org.springframework.boot.testcontainers.service.connection.ServiceConnection;
import org.springframework.context.annotation.Bean;
import org.testcontainers.postgresql.PostgreSQLContainer;

/**
 * 테스트용 PostgreSQL — Testcontainers가 띄우고 Spring이 접속 정보를 받아 간다.
 *
 * <p>H2 같은 대역을 쓰지 않는다. 행 잠금, {@code RETURNING}, {@code left()}의 문자 단위 동작처럼
 * 이 서버가 기대는 것들이 PostgreSQL의 거동이다. Docker가 떠 있어야 한다.
 */
@TestConfiguration(proxyBeanMethods = false)
public class PostgresTestConfig {

    @Bean
    @ServiceConnection
    PostgreSQLContainer postgres() {
        return new PostgreSQLContainer("postgres:17");
    }
}
