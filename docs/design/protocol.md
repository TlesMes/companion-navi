# 클라이언트-서버 프로토콜 (턴 스트림 v1)

> 상태: **구현됨(2026.11.16)** — `server/`의 턴 게이트웨이가 이 문서대로 동작한다.
> [service.md](./service.md) §8과 D22의 "남은 것(메시지 명세)"을 여기서 정한다.
> 구현: [TurnStreamHandler](../../server/src/main/java/navi/server/gateway/TurnStreamHandler.java),
> [TurnRunner](../../server/src/main/java/navi/server/gateway/TurnRunner.java).
> 클라이언트 구현: [navi/remote/](../../navi/remote/) (연결 모드, 2026.11.18). 켜는 법은 §6.

## 1. 전체 모양

- **REST** — 기기 등록·확인.
- **WebSocket 1개** — 턴. JSON 텍스트 프레임, 한 프레임에 메시지 하나.
- 서버로 가는 건 **텍스트뿐**이다. 음성은 PC를 떠나지 않는다(service.md §10, G8 ①).
- 클라이언트가 보내는 건 **발화 텍스트·턴 종류·페르소나 키**다. 카드 본문·시스템 프롬프트·
  대화 이력은 받지 않는다 — 조립은 서버가 한다(service.md §2.2).

## 2. 인증 (D23)

| 요청 | 응답 |
| :-- | :-- |
| `POST /v1/devices` | `201` `{"device_token": "navi_dt_…", "user_id": 1, "device_id": 1}` |
| `GET /v1/me` + `Authorization: Bearer <토큰>` | `200` `{"user_id", "device_id"}` / `401` |

- 등록하면 익명 사용자와 기기 토큰이 생긴다. **토큰은 이 응답에서 한 번만 나온다** — 서버는
  해시만 갖는다. 클라이언트가 잃어버리면 그 사용자의 기억을 되찾을 방법이 없다(계정이 붙기 전의 한계).
- WebSocket은 `GET /v1/turn-stream`에 같은 `Authorization` 헤더를 실어 연다. 토큰이 틀리면
  핸드셰이크가 `401`로 거절된다.

## 3. 메시지

모든 메시지는 `type`을 갖고, 턴에 속한 메시지는 `turn_id`를 갖는다.

### 3.1 클라이언트 → 서버

| type | 필드 | 뜻 |
| :-- | :-- | :-- |
| `turn.start` | `turn_id`, `kind`, `persona`, `text` | 턴을 시작한다 |
| `turn.cancel` | `turn_id` | 진행 중인 턴을 멈춘다(끼어들기) |
| `turn.played` | `turn_id`, `played_chars`, `interrupted` | 재생 보고 — 어디까지 들렸는가 |
| `turn.metrics` | `turn_id`, `metrics` | 클라이언트가 잰 수치 |
| `session.activate` | — | 이 기기에서 사용자를 감지했다(웨이크워드) — 말이 오기 전에 활성권을 가져간다 |

- `turn_id` — **클라이언트가 만드는 UUID.** 멱등 키다: 같은 값이 다시 오면 `duplicate_turn`으로 거절한다.
- `kind` — `reactive`(기본) / `proactive` / `proactive_callback`. **지금은 `reactive`만 받는다**
  (`unsupported_kind`). 선제 턴의 소재는 서버 피드에서 와야 하는데 피드가 아직 서버에 없다.
  클라이언트가 소재를 보내게 하면 임의 텍스트를 프레임에 싣는 통로가 된다.
- `persona` — 페르소나 키(`session.ready`의 `personas` 중 하나).
- `text` — 발화(STT 결과). 비어 있으면 안 된다.
- `played_chars` — 사용자가 **실제로 들은 본문의 길이.** 단위는 유니코드 코드 포인트다
  (Python의 `len()`). 서버가 보낸 `turn.delta`의 `text`를 이어 붙인 문자열에서 센다.
- `interrupted` — 끝까지 듣지 못했으면 `true`.
- `metrics` — JSON 객체, 4,096자 이하. 권장 키: `ttfa_ms`(발화 종료 → 첫 오디오),
  `tts_rtf`, `stt_ms`. 틀려도 오류를 돌려주지 않는다(수치는 턴을 방해하지 않는다).

### 3.2 서버 → 클라이언트

| type | 필드 | 뜻 |
| :-- | :-- | :-- |
| `session.ready` | `protocol`, `user_id`, `personas`, `limits`, `role` | 연결됨. 첫 메시지. `role`은 늘 `active` — 접속한 연결이 활성권을 가져간다 |
| `session.standby` | — | 다른 연결이 활성권을 가져갔다. 연결은 그대로 산다 |
| `session.active` | — | 이 연결이 활성권을 (되)찾았다 |
| `turn.accepted` | `turn_id` | 턴을 받았다. 생성 시작 |
| `turn.mood` | `turn_id`, `mood` | 이 응답의 무드. **본문보다 먼저 온다** |
| `turn.delta` | `turn_id`, `seq`, `text` | 본문 조각 |
| `turn.end` | `turn_id`, `last_seq`, `chars`, `mood`, `usage`, `server_ms` | 생성 끝 |
| `turn.cancelled` | `turn_id` | 취소됨. 이 턴은 폐기됐다 |
| `turn.confirmed` | `turn_id`, `ok` | 재생 보고를 처리했다 |
| `turn.error` | `turn_id`, `code`, `message` | 거절 또는 실패 |

- `limits` — `max_text_chars`, `turns_per_minute`, `daily_tokens`. 클라이언트가 보내기 전에 걸러
  낼 수 있게 알려 줄 뿐이고, 집행은 서버가 한다(§3.4).
- `mood` — `neutral` / `bright` / `calm`. 서버가 응답 맨 앞의 `[mood:key]` 태그를 떼어 이 필드로
  보낸다(service.md §8, G2). **본문에는 태그가 없다.** 클라이언트는 `turn.mood`를 받고 톤(레퍼런스)을
  고른 뒤 본문을 합성한다. 무드 키의 뜻은 페르소나 번들이 정한다.
- `text` — 두뇌가 낸 조각 그대로다. **문장 단위가 아니다.** 문장 분할은 클라이언트(TTS 옆)가 한다.
  본문은 공백으로 시작하지 않는다.
- `seq` — 턴마다 0부터 1씩 는다. 이어받기가 없으므로(§4) 용도는 누락·순서 확인뿐이다.
- `last_seq` — 마지막 `turn.delta`의 `seq`. 본문이 없으면 `-1`.
- `chars` — 본문 전체 길이(코드 포인트). `played_chars`의 상한이다.
- `usage` — `input_tokens`(캐시 포함 입력 전체), `output_tokens`, `cache_read_tokens`.
- `server_ms` — `ttft`(턴 수신 → 두뇌 첫 토큰), `total`(턴 수신 → 생성 끝).
- `ok` — `false`면 임시 상태의 턴이 아니었다(이미 확정됐거나 폐기됐다). 다시 보내지 않는다.

### 3.3 오류 코드

| code | 언제 | 클라이언트가 할 일 |
| :-- | :-- | :-- |
| `bad_request` | JSON이 아님, 필드 누락·형식 오류, 모르는 `type` | 고친다(버그) |
| `unsupported_kind` | `reactive`가 아닌 `kind` | 보내지 않는다 |
| `unknown_persona` | 서버가 모르는 페르소나 키 | 번들을 맞춘다 |
| `busy` | 이 연결에 진행 중인 턴이 있다 | 취소하고 `turn.cancelled`를 기다린 뒤 다시 보낸다 |
| `not_active` | 대기 연결이 선제 턴을 보냈다 | 보내지 않는다 — 먼저 말을 거는 건 활성 기기 하나뿐이다 |
| `duplicate_turn` | 이미 받은 `turn_id` | 새 `turn_id`로 보낸다 |
| `text_too_long` | 발화가 `max_text_chars`를 넘는다 | 보내지 않는다(기다려도 같다) |
| `rate_limited` | 최근 60초의 턴이 `turns_per_minute`에 닿았다 | `retry_after_s`초 뒤에 보낸다 |
| `daily_quota_exceeded` | 오늘 쓴 토큰이 `daily_tokens`에 닿았다 | `retry_after_s`초(사용자 시간대 자정) 뒤에 보낸다 |
| `brain_unavailable` | 두뇌 호출 실패 | 사용자에게 안내 문구를 재생한다(service.md §3, G5) |
| `internal` | 서버 오류 | 〃 |

`turn.accepted` 전의 오류는 턴이 만들어지지 않은 것이다. `turn.accepted` 뒤의 오류는 턴이 폐기된 것이다.

### 3.4 한도 (G7, D24)

| 한도 | 임시값 | 세는 법 |
| :-- | :-- | :-- |
| 발화 길이 | 500자 | `text`의 코드 포인트 수 |
| 분당 턴 | 20 | 최근 60초에 **받은** 턴. 거절된 요청은 세지 않고, 취소·폐기된 턴은 센다 |
| 일일 토큰 | 300,000 | 입력(캐시 포함) + 출력. "오늘"은 사용자 시간대의 날짜 |

- 한도에 걸린 요청은 턴이 만들어지지 않고 두뇌도 부르지 않는다. 연결은 유지된다.
- 일일 토큰은 **턴 앞에서 확인하고 턴 뒤에 차감한다.** 그래서 마지막 한 턴만큼은 넘을 수 있다.
- 폐기된 턴도 차감한다 — 취소를 반복해 한도를 비껴갈 수 없다.
- 값은 설정이고 임시다. 좋은 값은 사용 로그가 쌓인 뒤 정한다.

## 4. 턴의 흐름

```
클라이언트                          서버
   │ turn.start ───────────────────▶ │  이력 인출 → 조립 → 두뇌 호출
   │ ◀─────────────── turn.accepted  │
   │ ◀─────────────────── turn.mood  │  (톤을 고른다)
   │ ◀────────── turn.delta seq=0…n  │  (문장으로 잘라 합성·재생)
   │ ◀──────────────────── turn.end  │  임시 저장
   │ turn.played ──────────────────▶ │  들린 데까지 잘라 확정
   │ ◀────────────── turn.confirmed  │
   │ turn.metrics ─────────────────▶ │
```

- **연결당 진행 턴은 하나다.** 진행 중에 `turn.start`가 오면 `busy`다.
- **끼어들기**는 `turn.cancel` → `turn.cancelled` → 새 `turn.start` 순서다. 취소된 턴은 사용자
  발화까지 **없던 일**이 된다 — 다음 턴의 이력에 나오지 않는다.
- 취소와 생성 종료는 엇갈릴 수 있다. `turn.cancel`을 보냈는데 `turn.end`가 오면 그 턴은 끝까지
  생성된 것이다 — `played_chars`로 실제 들린 지점을 보고하면 된다.

### 4.1 턴 확정 (G1)

서버는 생성이 끝나면 응답을 **임시로** 저장하고, 재생 보고를 받아 확정한다. 사용자가 듣지 못한
말을 나비가 "했다"고 기억하지 않게 하려는 것이다.

| 보고 | 결과 |
| :-- | :-- |
| `played_chars` = `chars` | 전문 확정 |
| 0 < `played_chars` < `chars` | 그 글자 수에서 잘라 확정 |
| `played_chars` = 0 | 나비의 발화를 지운다. 사용자 발화는 남는다 |
| 보고 없이 연결이 닫힘 | **전문으로 확정**(service.md §3의 미정 항목을 이렇게 정했다, 2026.11.16) |

임시 상태의 턴도 다음 턴의 이력에 들어간다 — 재생 보고가 다음 발화보다 늦게 올 수 있다.

### 4.2 활성 연결 하나 (D20)

- 한 사용자의 연결은 **활성 하나 + 대기 여럿**이다. 접속한 연결이 활성권을 가져가고, 밀린 연결은
  `session.standby`를 받고 **그대로 연결돼 있다**(로그아웃하지 않는다 — 아무도 없는 기기에서 혼자 도는
  데몬이라 다시 들여보낼 사람이 없다).
- 대기 연결에서 **반응 턴**(사용자가 그 기기에 말을 걸었다)이 오면 그 연결이 활성권을 가져가고 턴은
  그대로 돈다. 말을 거는 쪽은 무엇과도 상관없이 이긴다. 선제 턴은 `not_active`로 거절된다.
- 활성 연결이 닫히면 가장 최근에 활성이었던 대기 연결이 `session.active`를 받는다.
- 계정이 없는 동안 사용자당 기기는 하나라, 여기서 여럿이 되는 건 같은 토큰의 프로세스 여럿이다.
  "기본 기기"와 단절 유예는 계정이 붙을 때 정한다(§7). 상태는 서버 프로세스 메모리에 있다.

### 4.3 하트비트

- 서버가 `navi.connection.ping-interval-ms`(20초)마다 WebSocket ping을 보내고, `pong-timeout-ms`(60초)
  동안 pong이 없으면 연결을 닫는다. 닫히면 진행 중 턴은 폐기된다(§4.4).
- pong은 WebSocket 라이브러리가 알아서 돌려준다(Python `websockets`, JDK `HttpClient` 모두). 클라이언트도
  자기 쪽 ping으로 죽은 서버를 알아챈다(`websockets` 기본 20초).

### 4.4 연결이 끊기면 (D22)

- **생성 중에 끊기면 그 턴은 폐기다.** 서버는 두뇌 호출을 그 자리에서 끊고, 사용자 발화와 응답을
  지운다. 이어받기는 없다 — 다시 연결해 **새 `turn_id`로** 처음부터 보낸다.
- 폐기된 턴이 쓴 토큰은 쿼터에서 빠지지 않는다(벤더에는 이미 값을 냈다).
- 클라이언트는 끊긴 턴의 조각을 버린다. 일부만 재생했더라도 서버는 그 턴을 기억하지 않는다.

## 5. 클라이언트 구현 (navi/remote)

- **켜는 법:** `config.local.yaml`에 `server.url`(예: `http://localhost:8080`) 또는 실행 인자
  `--server URL`(데몬·CLI 둘 다). 빈 값이면 단독 모드다 — 기본이 단독이다.
- **토큰:** 처음 연결할 때 `POST /v1/devices`로 받아 `server.token_path`(기본 `secrets/server_token`)에
  둔다. **401이 와도 새로 등록하지 않는다** — 새 토큰은 새 사용자이고, 나비가 아무 말 없이 기억을
  잃는다. 사용자에게 파일 위치와 결과를 알린다.
- **파이프라인은 모드를 모른다.** `RemoteConductor`가 조립 대신 발화·종류·페르소나 키를 담고,
  `RemoteBrain`이 `turn.mood`를 `[mood:key] ` 선행 태그로 되돌려 흘린다 — 단독 모드와 같은 경로다.
- **재생 보고:** TTS가 끝까지 소리 낸 문장의 끝 위치(`MouthAdapter.played_chars`)를 보낸다.
  끊기지 않았으면 `chars` 전부, 끊겼는데 위치를 모르면 0이다.
- **로컬 DB에 대화를 적지 않는다.** 서버가 원본이다. 모드 상태·상호작용 로그 같은 기기 상태는
  계속 로컬에 쓴다.
- **연결 모드에서 꺼지는 것:** 선제 발화, 로컬 피드 수집, 로컬 사실 추출, GUI 두뇌 교체.
- **서버에 못 닿으면** 그 턴은 실패로 끝나고 기억에 남지 않는다. 다음 턴에 다시 연결한다.
  사전 합성 안내 문구(G5)는 아직 없다 — 지금은 화면에 오류가 찍힌다.

## 6. 워커 API (서버 ↔ Python 워커)

LLM을 부르는 배치(지금은 사실 추출)는 Python 워커가 한다(service.md §11 R1). 워커는 DB에 붙지
않고 이 API로 작업을 pull한다. 구현: [WorkerController](../../server/src/main/java/navi/server/jobs/WorkerController.java),
[navi/worker](../../navi/worker/).

- **인증:** `Authorization: Bearer <NAVI_WORKER_TOKEN>`. 서버에 토큰이 비어 있으면 API가 닫힌다(503).
- `POST /v1/worker/jobs/claim` `{"kinds": ["extract_facts"]}` → `204`(없음) 또는
  `200 {"job_id", "kind", "attempt", "payload"}`.
- `POST /v1/worker/jobs/{id}/complete` `{"attempt", "facts": [...], "cursor", "usage"}` → `200` /
  `409`(그 시도는 무효 — 결과를 버린다).
- `POST /v1/worker/jobs/{id}/fail` `{"attempt", "error"}` → 백오프 뒤 다시 내준다. 한도를 넘으면 버린다.
- **`attempt`는 펜싱 토큰이다.** 가져갈 때마다 오르고, 완료·실패는 그 값을 내밀어야 받아들여진다.
  임대(기본 300초)가 끝나 다른 워커가 다시 가져간 작업을 늦게 깨어난 워커가 덮어쓰지 못한다.

`extract_facts`의 payload:

| 필드 | 뜻 |
| :-- | :-- |
| `user_id`, `now` | 대상 사용자, 서버 시각(UTC) |
| `utc_offset_minutes` | 그 사용자 시간대의 지금 오프셋 — 워커는 이걸로 "말한 날"과 만료 자정을 센다 |
| `turns` | `{id, role, text, created_at}` — 커서 뒤의 **확정된** 발화, 아직 안 끝난 턴의 앞까지, 최대 40행 |
| `known_facts` | `{id, kind, text}` — 지금 유효한 사실 최신 80건 |
| `max_facts` | 한 번에 받아들일 사실 수 |

결과의 `facts[]`는 `{kind, text, source_id, expires_at, replaces}`, `cursor`는 이번에 읽은 마지막 발화 ID다.
서버는 `source_id`·`replaces`가 그 사용자의 것일 때만 받고, 적재·커서 이동·토큰 차감·완료 표시를 한
트랜잭션으로 한다.

## 7. 정하지 않은 것

- **재연결 뒤의 재생 보고** — 연결이 끊긴 사이의 보고를 나중에 올리는 길(이벤트 발신함,
  service.md §5 R2). 지금은 연결이 닫히면 전문으로 확정한다.
- **기본 기기·단절 유예**(D20) — 계정이 붙어 사용자당 기기가 여럿이 될 때.
- **선제 턴** — 서버 피드가 생긴 뒤 `kind`와 소재 전달 방식을 정한다.
- **번들 버전** — 클라이언트 번들과 서버 카드의 버전이 다를 때(service.md §9).
