# navi-server — 턴 게이트웨이

companion-navi의 서버 코어 중 온라인 경로다([docs/design/service.md](../docs/design/service.md) §2.2).
클라이언트가 **발화 텍스트·턴 종류·페르소나 키**를 보내면 서버가 요청을 조립하고
(`카드 → 사실 → 최근 대화`), 서버 키로 두뇌를 불러 토큰을 WebSocket으로 흘린다.

프로토콜은 [docs/design/protocol.md](../docs/design/protocol.md).

## 필요한 것

- JDK 25 — 기본 `java`가 아니어도 된다. Gradle이 설치된 JDK 25를 찾아 쓴다(toolchain).
- Docker — 로컬 PostgreSQL(`compose.yaml`)과 테스트(Testcontainers)에 쓴다.

## 실행

```bash
cd server
./gradlew bootRun
```

`bootRun`이 `compose.yaml`의 PostgreSQL(호스트 포트 5433)을 띄우고 스키마를 올린다. 기본 두뇌는
`echo`다 — 키 없이 배선만 확인한다. 실제 모델로 띄우려면:

```bash
NAVI_BRAIN_VENDOR=anthropic ./gradlew bootRun
```

키는 환경 변수 `ANTHROPIC_API_KEY`, 없으면 저장소 루트 `.env`에서 읽는다.

한 턴 보내 보기(저장소 루트에서, Python 가상환경):

```bash
python scripts/try/try_server_turn.py "나 방금 퇴근했어." "저녁 뭐 먹지?" --overlong
```

## 워커 (서버 기억)

사실 추출은 Python 워커가 한다. 서버와 워커가 같은 공유 토큰을 써야 한다(`.env`의
`NAVI_WORKER_TOKEN` — `bootRun`도 거기서 읽는다). 토큰이 비면 워커 API가 닫힌다.

```bash
python -m navi.worker --server http://localhost:8080 --vendor anthropic   # 상주
python -m navi.worker --server http://localhost:8080 --vendor anthropic --once   # 큐가 빌 때까지
```

워커가 없으면 사실이 쌓이지 않고, 최근 대화 창은 상한(20행)에 머문다 — 대화는 그대로 된다.

## 테스트

```bash
./gradlew test        # Docker 필요. 과금 없음
./gradlew liveTest    # 실제 벤더 1회 호출(Haiku 기준 약 $0.003)
```

조립·무드 분리·메시지 정규화는 Python 원본이 뜬 공유 픽스처(`tests/fixtures/`)와 문자 단위로
비교한다. 픽스처는 `python -m tests.assembly_fixtures --write`로 다시 뜬다 — 손으로 고치지 않는다.

## 설정

| 환경 변수 | 기본값 | 뜻 |
| :-- | :-- | :-- |
| `NAVI_BRAIN_VENDOR` | `echo` | `echo` / `anthropic` |
| `NAVI_BRAIN_MODEL` | `claude-haiku-4-5` | 두뇌 모델 |
| `ANTHROPIC_API_KEY` | — | 서버 키. 클라이언트는 볼 수 없다 |
| `NAVI_DB_URL` / `NAVI_DB_USER` / `NAVI_DB_PASSWORD` | compose의 PostgreSQL | 저장소 |
| `NAVI_QUOTA_MAX_TEXT_CHARS` | `500` | 발화 길이 상한 |
| `NAVI_QUOTA_TURNS_PER_MINUTE` | `20` | 분당 턴 |
| `NAVI_QUOTA_DAILY_TOKENS` | `300000` | 일일 토큰 |
| `NAVI_WORKER_TOKEN` | — | 워커 API 공유 비밀. 비면 워커 API가 닫힌다 |
| `NAVI_FACTS_ENABLED` | `true` | 사실 주입·추출 작업 |

한도 값은 임시다(D24). 그 밖의 설정은 `src/main/resources/application.yaml`.

## 구조

| 패키지 | 맡는 일 | Python 원본 |
| :-- | :-- | :-- |
| `assembly` | 카드 → 시스템 프롬프트, 프레임, 사실 블록, 무드 태그 분리 | `conductor.py` · `persona/card.py` · `mouth/mood.py` |
| `brain` | 두뇌 포트, Anthropic 스트리밍 어댑터, Echo | `brain/` |
| `identity` | 기기 등록·토큰 인증 (`app_user` / `device`) | — |
| `turn` | 턴의 한살이와 저장 (`turn` / `turn_message` / `usage_daily`) | `memory/store.py`의 턴 부분 |
| `quota` | 한도 확인 + 턴 열기(한 트랜잭션), 사용량 장부 | — |
| `memory` | 사실 저장·인출, 추출 작업의 입력·결과 적재 | `memory/store.py`의 사실 부분 |
| `jobs` | 작업 큐(`SKIP LOCKED`)와 워커 API | — |
| `gateway` | WebSocket 턴 스트림 | — |
| `persona` | 서버가 아는 카드(`personas/*.yaml`) | — |

## 아직 없는 것

피드·선제 턴·학습 큐·서버 TTS·OAuth·본문 필드 암호화·등록 IP 제한·
다기기 활성권. 순서는 service.md §7.
