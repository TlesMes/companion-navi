package navi.server.identity;

import static org.assertj.core.api.Assertions.assertThat;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.post;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.header;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.status;

import navi.server.PostgresTestConfig;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.context.annotation.Import;
import org.springframework.jdbc.core.simple.JdbcClient;
import org.springframework.test.web.servlet.MockMvc;
import org.springframework.test.web.servlet.ResultActions;
import org.springframework.test.web.servlet.setup.MockMvcBuilders;
import org.springframework.web.context.WebApplicationContext;

/** 같은 주소의 등록을 시간당 N개로 — 토큰을 새로 받아 한도를 비껴가지 못하게. */
@SpringBootTest(properties = "navi.registration.per-source-per-hour=3")
@Import(PostgresTestConfig.class)
class RegistrationLimitTest {

    @Autowired WebApplicationContext context;
    @Autowired JdbcClient jdbc;
    MockMvc mvc;

    @BeforeEach
    void setUp() {
        mvc = MockMvcBuilders.webAppContextSetup(context).build();
        jdbc.sql("DELETE FROM device_registration").update();
    }

    private ResultActions register(String addr, String forwardedFor) throws Exception {
        var req = post("/v1/devices").with(r -> {
            r.setRemoteAddr(addr);
            return r;
        });
        if (forwardedFor != null) {
            req.header("X-Forwarded-For", forwardedFor);
        }
        return mvc.perform(req);
    }

    @Test
    void sameSourceIsCappedPerHour() throws Exception {
        for (int i = 0; i < 3; i++) {
            register("203.0.113.7", null).andExpect(status().isCreated());
        }

        register("203.0.113.7", null)
                .andExpect(status().isTooManyRequests())
                .andExpect(header().exists("Retry-After"));
        // 다른 주소는 따로 센다.
        register("198.51.100.2", null).andExpect(status().isCreated());
    }

    @Test
    void windowPassesAfterAnHour() throws Exception {
        for (int i = 0; i < 3; i++) {
            register("203.0.113.8", null).andExpect(status().isCreated());
        }
        jdbc.sql("UPDATE device_registration SET created_at = created_at - interval '61 minutes'")
                .update();

        register("203.0.113.8", null).andExpect(status().isCreated());
    }

    @Test
    void forwardedForIsIgnoredUnlessTrusted() throws Exception {
        // 프록시 없이 열린 서버에서 헤더를 믿으면 누구나 지어내 제한을 비껴간다.
        for (int i = 0; i < 3; i++) {
            register("203.0.113.9", "10.0.0." + i).andExpect(status().isCreated());
        }

        register("203.0.113.9", "10.0.0.99").andExpect(status().isTooManyRequests());
    }

    @Test
    void sourceAddressIsNotStoredInTheClear() throws Exception {
        register("203.0.113.10", null).andExpect(status().isCreated());

        String stored = jdbc.sql("SELECT source_hash FROM device_registration")
                .query(String.class).single();
        assertThat(stored).hasSize(64).doesNotContain("203.0.113.10");
    }
}
