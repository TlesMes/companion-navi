package navi.server.identity;

import static org.assertj.core.api.Assertions.assertThat;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.get;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.post;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.status;

import navi.server.PostgresTestConfig;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.context.annotation.Import;
import org.springframework.jdbc.core.simple.JdbcClient;
import org.springframework.test.web.servlet.MockMvc;
import org.springframework.test.web.servlet.setup.MockMvcBuilders;
import org.springframework.web.context.WebApplicationContext;
import tools.jackson.databind.JsonNode;
import tools.jackson.databind.ObjectMapper;

/** 기기 등록과 토큰 인증(D23). */
@SpringBootTest
@Import(PostgresTestConfig.class)
class DeviceApiTest {

    @Autowired WebApplicationContext context;
    @Autowired JdbcClient jdbc;
    @Autowired DeviceService devices;
    MockMvc mvc;

    @BeforeEach
    void setUp() {
        mvc = MockMvcBuilders.webAppContextSetup(context).build();
        // 여기 테스트는 같은 주소에서 여러 번 등록한다 — 등록 제한은 RegistrationLimitTest가 본다.
        jdbc.sql("DELETE FROM device_registration").update();
    }

    private JsonNode register() throws Exception {
        String body = mvc.perform(post("/v1/devices")).andExpect(status().isCreated())
                .andReturn().getResponse().getContentAsString();
        return new ObjectMapper().readTree(body);
    }

    @Test
    void registrationCreatesAnonymousUserAndReturnsTokenOnce() throws Exception {
        JsonNode r = register();
        String token = r.get("device_token").asString();
        long userId = r.get("user_id").asLong();

        assertThat(token).startsWith(DeviceTokens.PREFIX).hasSizeGreaterThan(40);
        // user와 device는 다른 테이블의 다른 행이다 — 토큰이 사용자 키가 아니다.
        assertThat(jdbc.sql("SELECT user_id FROM device WHERE device_id = ?")
                .param(r.get("device_id").asLong()).query(Long.class).single()).isEqualTo(userId);
        assertThat(jdbc.sql("SELECT timezone FROM app_user WHERE user_id = ?")
                .param(userId).query(String.class).single()).isEqualTo("Asia/Seoul");
    }

    @Test
    void tokenIsStoredOnlyAsHash() throws Exception {
        String token = register().get("device_token").asString();

        assertThat(jdbc.sql("SELECT count(*) FROM device WHERE token_hash = ?")
                .param(DeviceTokens.hash(token)).query(Long.class).single()).isEqualTo(1);
        assertThat(jdbc.sql("SELECT count(*) FROM device WHERE token_hash = ?")
                .param(token).query(Long.class).single()).isZero();
    }

    @Test
    void eachRegistrationGetsItsOwnUser() throws Exception {
        assertThat(register().get("user_id").asLong()).isNotEqualTo(register().get("user_id").asLong());
    }

    @Test
    void validTokenAuthenticatesAndStampsLastSeen() throws Exception {
        JsonNode r = register();
        String token = r.get("device_token").asString();

        String body = mvc.perform(get("/v1/me").header("Authorization", "Bearer " + token))
                .andExpect(status().isOk()).andReturn().getResponse().getContentAsString();

        assertThat(new ObjectMapper().readTree(body).get("user_id").asLong())
                .isEqualTo(r.get("user_id").asLong());
        assertThat(jdbc.sql("SELECT last_seen_at IS NOT NULL FROM device WHERE device_id = ?")
                .param(r.get("device_id").asLong()).query(Boolean.class).single()).isTrue();
    }

    @Test
    void missingMalformedOrUnknownTokenIsRejected() throws Exception {
        mvc.perform(get("/v1/me")).andExpect(status().isUnauthorized());
        mvc.perform(get("/v1/me").header("Authorization", "Basic abc"))
                .andExpect(status().isUnauthorized());
        mvc.perform(get("/v1/me").header("Authorization", "Bearer not-a-device-token"))
                .andExpect(status().isUnauthorized());
        mvc.perform(get("/v1/me").header("Authorization", "Bearer " + DeviceTokens.generate()))
                .andExpect(status().isUnauthorized());
    }

    @Test
    void revokedTokenIsRejected() throws Exception {
        JsonNode r = register();
        jdbc.sql("UPDATE device SET revoked_at = now() WHERE device_id = ?")
                .param(r.get("device_id").asLong()).update();

        assertThat(devices.authenticate(r.get("device_token").asString())).isEmpty();
    }
}
