# 클라이언트-서버 프로토콜 (턴 스트림 v1)

> 상태: **구현됨(2026.11.16)** — `server/`의 턴 게이트웨이가 이 문서대로 동작한다.
> [service.md](./service.md) §8과 D22의 "남은 것(메시지 명세)"을 여기서 정한다.
> 구현: [TurnStreamHandler](../../server/src/main/java/navi/server/gateway/TurnStreamHandler.java),
> [TurnRunner](../../server/src/main/java/navi/server/gateway/TurnRunner.java).
> 클라이언트(Python 데몬)의 연결 모드는 아직 없다 — 이 문서가 그쪽의 구현 명세다.

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
| `session.ready` | `protocol`, `user_id`, `personas` | 연결됨. 첫 메시지 |
| `turn.accepted` | `turn_id` | 턴을 받았다. 생성 시작 |
| `turn.mood` | `turn_id`, `mood` | 이 응답의 무드. **본문보다 먼저 온다** |
| `turn.delta` | `turn_id`, `seq`, `text` | 본문 조각 |
| `turn.end` | `turn_id`, `last_seq`, `chars`, `mood`, `usage`, `server_ms` | 생성 끝 |
| `turn.cancelled` | `turn_id` | 취소됨. 이 턴은 폐기됐다 |
| `turn.confirmed` | `turn_id`, `ok` | 재생 보고를 처리했다 |
| `turn.error` | `turn_id`, `code`, `message` | 거절 또는 실패 |

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
| `duplicate_turn` | 이미 받은 `turn_id` | 새 `turn_id`로 보낸다 |
| `brain_unavailable` | 두뇌 호출 실패 | 사용자에게 안내 문구를 재생한다(service.md §3, G5) |
| `internal` | 서버 오류 | 〃 |

`turn.accepted` 전의 오류는 턴이 만들어지지 않은 것이다. `turn.accepted` 뒤의 오류는 턴이 폐기된 것이다.

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

### 4.2 연결이 끊기면 (D22)

- **생성 중에 끊기면 그 턴은 폐기다.** 서버는 두뇌 호출을 그 자리에서 끊고, 사용자 발화와 응답을
  지운다. 이어받기는 없다 — 다시 연결해 **새 `turn_id`로** 처음부터 보낸다.
- 폐기된 턴이 쓴 토큰은 쿼터에서 빠지지 않는다(벤더에는 이미 값을 냈다).
- 클라이언트는 끊긴 턴의 조각을 버린다. 일부만 재생했더라도 서버는 그 턴을 기억하지 않는다.

## 5. 정하지 않은 것

- **재연결 뒤의 재생 보고** — 연결이 끊긴 사이의 보고를 나중에 올리는 길(이벤트 발신함,
  service.md §5 R2). 지금은 연결이 닫히면 전문으로 확정한다.
- **한 사용자의 연결 여럿** — 활성 연결 하나 + 대기 연결(D20). 지금은 같은 토큰으로 여러 번
  연결해도 막지 않는다.
- **선제 턴** — 서버 피드가 생긴 뒤 `kind`와 소재 전달 방식을 정한다.
- **번들 버전** — 클라이언트 번들과 서버 카드의 버전이 다를 때(service.md §9).
- **하트비트** — 죽은 연결을 빨리 알아채는 ping 주기.
