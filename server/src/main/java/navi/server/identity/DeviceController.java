package navi.server.identity;

import java.util.Map;
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
 * <p>등록은 누구에게나 열려 있다 — 계정이 없으니 막을 기준이 없다. 남용(토큰을 계속 새로 받아
 * 쿼터를 비껴가기)을 막는 IP 제한은 아직 없다. 공개 전에 넣는다.
 */
@RestController
public class DeviceController {

    private final DeviceService devices;

    public DeviceController(DeviceService devices) {
        this.devices = devices;
    }

    /** 새 기기 등록. 토큰은 이 응답에서 한 번만 볼 수 있다 — 클라이언트가 보관한다. */
    @PostMapping("/v1/devices")
    public ResponseEntity<Map<String, Object>> register() {
        DeviceService.Registered r = devices.register();
        return ResponseEntity.status(HttpStatus.CREATED)
                .body(Map.of(
                        "device_token", r.token(),
                        "user_id", r.identity().userId(),
                        "device_id", r.identity().deviceId()));
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
