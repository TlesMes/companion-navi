package navi.server.identity;

import jakarta.servlet.http.HttpServletRequest;
import java.util.Map;
import java.util.OptionalLong;
import org.springframework.http.HttpHeaders;
import org.springframework.http.HttpStatus;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestHeader;
import org.springframework.web.bind.annotation.RestController;

/**
 * 기기 등록·확인 REST.
 *
 * <p>등록은 누구에게나 열려 있다 — 계정이 없으니 막을 기준이 없다. 대신 같은 접속 주소의
 * 등록을 시간당 몇 개로 묶는다({@link RegistrationLimiter}) — 토큰을 계속 새로 받아 한도를
 * 비껴가는 것을 막는다.
 */
@RestController
public class DeviceController {

    private final DeviceService devices;
    private final RegistrationLimiter limiter;

    public DeviceController(DeviceService devices, RegistrationLimiter limiter) {
        this.devices = devices;
        this.limiter = limiter;
    }

    /** 새 기기 등록. 토큰은 이 응답에서 한 번만 볼 수 있다 — 클라이언트가 보관한다. */
    @PostMapping("/v1/devices")
    public ResponseEntity<Map<String, Object>> register(HttpServletRequest request) {
        OptionalLong wait = limiter.admit(source(request));
        if (wait.isPresent()) {
            return ResponseEntity.status(HttpStatus.TOO_MANY_REQUESTS)
                    .header(HttpHeaders.RETRY_AFTER, Long.toString(wait.getAsLong()))
                    .body(Map.of("error", "too_many_registrations",
                            "retry_after_s", wait.getAsLong()));
        }
        DeviceService.Registered r = devices.register();
        return ResponseEntity.status(HttpStatus.CREATED)
                .body(Map.of(
                        "device_token", r.token(),
                        "user_id", r.identity().userId(),
                        "device_id", r.identity().deviceId()));
    }

    /** 등록을 센 기준 — 프록시를 믿기로 했을 때만 X-Forwarded-For의 첫 주소. */
    private String source(HttpServletRequest request) {
        if (limiter.trustForwardedFor()) {
            String forwarded = request.getHeader("X-Forwarded-For");
            if (forwarded != null && !forwarded.isBlank()) {
                return forwarded.split(",")[0].strip();
            }
        }
        return request.getRemoteAddr();
    }

    /** 토큰이 살아 있는지 확인. */
    @GetMapping("/v1/me")
    public ResponseEntity<Map<String, Object>> me(
            @RequestHeader(value = HttpHeaders.AUTHORIZATION, required = false) String auth) {
        return devices.authenticateHeader(auth)
                .map(id -> ResponseEntity.ok(
                        Map.<String, Object>of("user_id", id.userId(), "device_id", id.deviceId())))
                .orElseGet(() -> ResponseEntity.status(HttpStatus.UNAUTHORIZED)
                        .body(Map.of("error", "unauthorized")));
    }
}
