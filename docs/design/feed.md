# 관심사 피드(Feed) 실행 계획 — D13 (체크리스트 B4)

> 작성: 2026.07.24. 대상: Phase 3 능동성(체크리스트 B4, 결정 D13). 근거 설계:
> [architecture.md](./architecture.md) §4.10(계약 스케치)·§6(topic_candidate 모델)·§4.4(3층 pick_topic).
> **이 문서는 실행 계획** — 확정 결정은 커밋 본문과 progress.md에 남긴다.
>
> **D13 결정 요지(2026.07.24 합의):** 소스=**RSS만**(뉴스 API·커뮤니티 크롤링 기각) / 관심사
> 출처=**① 명시 등록 + ② 대화 빈도 자동 추출 둘 다** / 안전 필터=**MVP엔 없음**(씨앗만 남김) /
> 수집 트리거=**DaemonCore tick piggyback**(별도 스케줄러 없음). 근거는 §1.

## 0. 한 줄

나비가 먼저 말 걸 **재료**를 하루 1~2회 배치로 모아 둔다. ①사용자가 등록한 관심사 RSS를
긁어 저가 LLM으로 소화하고, ②최근 대화에서 자주 나온 주제를 콜백 문장으로 뽑아, 둘 다
`topic_candidate`에 적재한다. tick이 선제 발화를 결정하면 [topic.py](../navi/heartbeat/topic.py)
`pick_topic`이 그 후보를 **고정 힌트보다 우선**해 트리거 문자열로 쓴다. **이게 A3(음성 선제
발화 E2E)의 유일한 선행** — 지금은 `topic_feed=[]` 더미라 요청문이 플레이스홀더다
([daemon.py:309](../navi/daemon.py:309)).

## 1. 왜 이 형태인가 (설계 고정점)

- **소스는 RSS만.** 무료·ToS 무해(RSS는 소비 목적 배포)·한국어 피드 풍부(연합뉴스 등)·`feedparser`
  한 줄 파싱. 뉴스 API는 무료 티어 제한+캐싱 ToS 제약으로 원가·복잡도↑, 커뮤니티 크롤링은
  ToS 위반 리스크로 **로컬 1인용 데몬엔 과하다** → 둘 다 기각(D13).
- **① 뉴스 재료 ≠ ② 대화 콜백 — 둘은 다른 종류다.** ①은 외부에서 온 신선한 소재("네가
  좋아하는 팀이 어제 이겼대"), ②는 사용자가 *이미 한 말*에서 뽑은 되물음("지난번 이직
  고민 어떻게 됐어?"). **②는 RSS가 필요 없다** — 메모리에서만 만들어진다. 그래서 "②의
  추출 키워드를 무슨 피드에 매핑하나" 문제가 아예 안 생긴다. 반려 AI에겐 뉴스보다 콜백이
  더 값지므로 ②를 일급으로 둔다. 둘 다 **같은 테이블·같은 `collect()`**로 흐르고 `source`만
  다르다(`rss` / `memory`).
- **"언제 말할까=규칙, 무엇을 말할까=모델" 원칙 준수.** 수집·필터·주기·중복방지는 전부
  결정론(규칙·시각 게이트·`used_at`). LLM은 **요약/추출**이라는 "무엇을 말할까"에만 쓴다
  (①뉴스 원문→나비 입말 소화, ②최근 턴→콜백 문장). 저가 티어 1회성 호출, 스트리밍 아님.
- **모든 외부 접점은 어댑터 뒤로**(CLAUDE.md 원칙). RSS 접근은 `FeedSource` 프로토콜 뒤에
  숨긴다 — 나중에 소스를 늘리거나 교체해도 `collect()`·저장·`pick_topic`은 무변경. 요약
  LLM은 **기존 `BrainAdapter` 계약을 재사용**(새 벤더 계층 안 만듦).
- **tick piggyback — 새 스케줄러 없음.** DaemonCore 루프가 이미 돈다([daemon.py](../navi/daemon.py)
  `_maybe_initiate`). 거기에 "지난 수집 후 N시간 지났나" 시각 게이트를 하나 더 얹어
  `collect()`를 부른다(daily_cap 판정과 같은 패턴). 블로킹 작업(HTTP fetch)은 `to_thread`로
  오디오 핫패스와 분리 — 음색 핫스왑이 쓰는 방식 그대로.
  **단, `to_thread`에 넣는 건 `collect()` 전체가 아니라 fetch뿐이다** — 근거는 §3.3.
- **안전 필터는 MVP에서 뺀다(D13), 씨앗만 남긴다.** 관심사가 이미 큐레이션돼(사용자가 고른
  피드·사용자가 한 말) 범용 뉴스 헤드라인을 긁을 때만 필요한 게이트다. `collect()`에
  `_is_safe(item)` 훅 자리만 두고 지금은 항상 True. **②가 사용자의 무거운 화제(이별·질병)를
  선제로 되꺼낼 위험**은 실재하므로(§5) 이 자리를 남겨 두는 것 자체가 설계다.

## 2. 데이터 흐름

```
[배치, tick이 12h 게이트 통과 시]  collect():
  ① RssSource(url).fetch() ──▶ 신선·미적재 아이템 몇 개 ──▶ summarize(LLM) ──┐
  ② recall_turns(주입 클로저) ──▶ 사용자 턴만 ──▶ 추출(LLM 1회) ───────────┤
                                                                            ▼
                                              insert_candidate(source, topic_key,
                                                summary, fetched_at, expires_at)

[매 tick, 선제 발화 판정 통과 시]  _maybe_initiate():
  cands = feed.get_fresh_topics(k)                 # 미사용·TTL 유효 후보
  topic = pick_topic(snapshot, None, tod, [c.summary for c in cands])
  if topic == cands[0].summary:                    # 피드 후보가 채택됨
      feed.mark_used(cands[0].id)                  # 같은 이슈 재발화 방지
  → conductor.build_request(trigger=topic) → brain → mouth
```

- 후보 없음 → `topic_feed=[]` → `pick_topic`이 기존 시간대 힌트로 폴백(무변경, 하위호환).
- 우선순위는 이미 [topic.py:37](../navi/heartbeat/topic.py:37)에 배선됨(`if topic_feed: return topic_feed[0]`).
  이 문서는 그 빈 자리에 실제 후보를 흘려 넣을 뿐, 3층 계약(입력 `list[str]`, 출력 `str|None`)은
  **무변경**.

## 3. 구현 부품

### 3.1 `topic_candidate` 저장소 — `navi/memory/store.py` 확장 + `schema.sql`
같은 SQLite 파일·같은 커넥션을 쓴다(별도 DB·conn 안 만듦 — MemoryStore가 이미 이 역할).
- 테이블(arch §6 스케치 확정):
  ```sql
  CREATE TABLE IF NOT EXISTS topic_candidate (
      candidate_id INTEGER PRIMARY KEY AUTOINCREMENT,
      source       TEXT NOT NULL CHECK (source IN ('rss', 'memory')),
      topic_key    TEXT NOT NULL,          -- 관심사 분류(등록명 or 추출 주제)
      summary      TEXT NOT NULL,          -- 나비 입말 재료(트리거 문자열로 직행)
      dedup_key    TEXT NOT NULL,          -- source+topic_key+원문해시 → 재적재 방지
      fetched_at   TEXT NOT NULL,
      expires_at   TEXT,                   -- 뉴스 96h(발행 기준) / 콜백 7일. 값은 설정 가능
      used_at      TEXT                    -- 선제 발화 사용 시각(중복방지)
  );
  CREATE UNIQUE INDEX IF NOT EXISTS idx_candidate_dedup ON topic_candidate (dedup_key);
  ```
- 메서드: `insert_candidate(...)`(dedup 충돌 시 무시), `candidate_exists(dedup_key)`(§3.3이
  요약 *전에* 거르는 데 필요 — 최초 목록에서 누락됐다), `fresh_candidates(k, now)`(used_at IS NULL
  AND (expires_at IS NULL OR expires_at > now), 최신순 k개), `mark_candidate_used(id, now)`,
  `last_collect_at()`/`set_last_collect_at(now)`(수집 게이트 상태 — mode_state처럼 작은 메타 행 or
  `feed_meta` 1행 테이블. 재기동 직후 재수집 방지).

### 3.2 소스 어댑터 — `navi/feed/rss.py` (신규) + `base.py`
- `base.py`: `FeedSource` 프로토콜 — `fetch() -> list[RawItem]`(제목·요약·링크·게시시각).
  나중 소스 확장의 봉합선. `RawItem`은 벤더 중립 dataclass.
- `rss.py`: `RssSource(feed_url, topic_key, *, timeout_s=10.0, fetcher=None)` — 블로킹 HTTP라
  호출부(`collect`)가 `to_thread`로 감싼다. 실패(네트워크·파싱)는 빈 리스트로 삼켜 배치가
  한 피드 때문에 안 죽게.
  **`feedparser.parse(url)`에 URL을 직접 넘기지 않는다**(구현 시 정정, 2026.07.31) —
  feedparser 자체 HTTP엔 타임아웃 손잡이가 없어 응답 없는 서버 하나가 `to_thread` 워커를
  영구 점유한다. `urlopen(timeout=)`으로 바이트를 받아 `feedparser.parse(data)`에 넘기고,
  그 김에 주입 가능한 `fetcher`가 테스트를 네트워크 없이 만든다.
  깨진 XML은 **관대 정책** — feedparser는 예외 대신 `bozo` 플래그를 세우므로 warning만 남기고
  파싱된 항목은 살린다(잘린 피드의 멀쩡한 앞 기사를 버릴 이유가 없다). 진짜 쓰레기는
  `entries`가 비어 자연히 빈 리스트가 된다.
  **`title`·`summary`의 HTML은 모든 피드에서 균일하게 걷어낸다**(`strip_html`, 추가 2026.08.02).
  RSS 명세가 `description`에 escaped HTML을 허용해서 생기는 일이라 **사이트 대응이 아니라
  포맷 대응**이다 — 이미 깨끗한 피드에선 no-op. 실측에서 한 국내 피드의 첫 항목이 인라인
  스타일·CDN 이미지 URL 포함 2010자였고, 안 걷으면 요약 LLM 입력의 대부분이 마크업이 된다.
  **소스별 분기는 `RssSource` 안에 두지 않는다** — 특정 피드가 정말 고유 처리를 요구하면
  답은 `if`가 아니라 `FeedSource` 구현체를 하나 더 만드는 것이다. 피드 목록은 코드가 아니라
  데이터(`config.yaml`/`config.local.yaml`)이므로 사용자가 관심사를 바꿔도 코드는 무변경.

### 3.3 오케스트레이터 — `navi/feed/collect.py` (신규)
계약(arch §4.10) 3함수를 소유한다.

> **`collect`는 `async def`이고 통째로 `to_thread`에 넣지 않는다** (구현 시 정정, 2026.07.31).
> 두 가지가 성립하지 않는다: ①본문이 요약 LLM을 `await` 해야 하는데 워커 스레드에서
> 돌리려면 두 번째 이벤트 루프가 필요하고 벤더 클라이언트는 생성 루프에 묶인다.
> ②`MemoryStore`의 sqlite 커넥션은 `check_same_thread` 없이 열려 **만든 스레드에 묶여**
> 있어 워커에서 쓰면 `ProgrammingError`다. → **`to_thread`는 `source.fetch()`에만**, DB 쓰기는
> 루프 스레드에. 저장소 불변식을 넓히지 않는 쪽을 택했다 — 넓히면 이후 모든 호출부가
> "아무 스레드에서나 써도 되나"를 다시 판단해야 한다.

- `collect(now)`:
  1. `_is_safe` 훅(현재 항상 True — §5 씨앗) 통과한 ① 아이템만 남김.
  2. 각 아이템 원문 → `summarize`(저가 LLM) → `insert_candidate(source='rss', ...)`.
     이미 적재된 건(dedup_key) 요약 호출 **전에** 걸러 LLM 낭비 방지.
     **소스당 `max_items_per_source`(기본 5)개 상한**(구현 시 추가) — 문서의 "몇 개"가
     코드에선 "전부"가 돼서, 첫 수집에 50개짜리 피드를 만나면 LLM 50회를 그대로 태운다.
     요약은 **순차 처리**(`asyncio.gather` 금지) — 어댑터 하나는 동시 요청 1건이 계약이다.
  3. ② 주입된 `recall_turns()` → 사용자 턴만 → `extract_callbacks`(저가 LLM 1회, 중립 진술
     문장으로) → `insert_candidate(source='memory', ...)`.
  4. `set_last_collect_at(now)`.
- `get_fresh_topics(k) -> list[TopicCandidate]`, `mark_used(candidate_id)` — 저장소 위임.
- **저가 LLM은 `BrainAdapter` 재사용.** `generate_stream`을 한 번 소진해 전문을 모으는
  1회성 요약 헬퍼. 벤더는 config `feed.summarizer`(기본 echo=헤드리스, 실동은 gemini-flash).
  스트리밍 아님·티어 분리(D1)와 무관.
- **요약 summary는 중립 시점으로.** 2인칭("네가 응원하는 팀이…")으로 쓰면 두뇌가 화자를
  재해석한다(실측 — [turn_assembly.md](./turn_assembly.md) §4.1). 요약 프롬프트에 "사실만
  중립 시점으로, 청자를 지칭하지 말 것" 제약을 넣는다. 인칭·말투는 발화 시점에 두뇌가 입힌다.
- **재료 언어는 한국어로 고정하고 프롬프트에 명시한다**(실측 후 확정 2026.08.02). 재료는
  사용자에게도 페르소나에게도 안 보이는 **내부 표현**이고 오직 두뇌만 읽는다. 페르소나에
  맞출 수도 없다 — 수집은 배치라 **어느 카드가 이 재료로 말할지 그 시점엔 모르고**, 사용자는
  런타임에 페르소나를 갈아끼운다([runtime.py](../navi/control/runtime.py)). 즉 재료의 페르소나
  독립성은 타협이 아니라 구조가 강제하는 것이다. 실측에선 명시 없이도 한국어가 나왔지만
  (시스템 프롬프트가 한국어라 모델이 따라온 **부작용**) 규칙이 아니라 모델이 바뀌면 흔들린다.
- **모델이 얹은 서식은 결정론 후처리로 걷어낸다.** 재료는 그대로 발화 트리거가 되므로
  마크다운이 남으면 나비가 소리 내 읽는다(실측 2026.08.02: haiku가 `# 요약` 머리말을 붙였다 —
  TTS로 "샵 요약"이 된다). 프롬프트 제약은 확률만 낮추므로 `clean_summary`를 둔다
  (`peel_mood`가 무드 태그를 흡수하는 것과 같은 자리).
- **TTL 기준은 발행 시각과 수집 시각 중 이른 쪽**(D13 보강 2026.08.02, 선택 ⓒ). 수집 시각만
  쓰면 이미 이틀 된 기사가 거기서 또 TTL만큼 살아 나비가 "어제 소식"인 양 나흘 전 기사를
  꺼낸다(실측: 발행이 뜸한 해외 매체는 피드 맨 앞이 41시간 경과). 발행 시각만 쓰면 그런 매체가
  통째로 무용지물이라 TTL을 96h로 넉넉히 잡아 상쇄한다. `min`은 발행 시각이 미래인 피드
  (시계 어긋남·예약 발행)에 대한 방어이기도 하다.

### 3.4 ② 대화 콜백 추출 — `summarize.py` + `collect.py`
- 규칙(결정론)으로 "요즘 자주 나온 주제"를 고르는 건 한국어 형태소 분석(konlpy 등) 부담이
  커서, **최근 N턴을 저가 LLM에 넘겨 주제 1~2개를 받는다**(요약/추출 = "무엇을 말할까"라
  LLM 허용). 1회 호출. 추출 결과가 비면 ② 스킵.
- **기준은 "빈도"가 아니라 "반복 + 미결"**(구현 시 정정 2026.08.25). 콜백을 값지게 만드는 건
  몇 번 나왔느냐가 아니라 **열려 있느냐**다. 절 제목의 "빈도"는 §7이 이미 기각한 형태소 빈도
  방식의 잔재다.
- **summary는 되물음 문장이 아니라 중립 진술이다**(구현 시 정정 2026.08.25). 원문은 "자연스러운
  되물음 문장"이었는데 **§3.3("중립 시점으로, 청자를 지칭하지 말 것. 인칭·말투는 발화 시점에
  두뇌가 입힌다")과 정면 충돌**한다. §3.3이 [turn_assembly.md](./turn_assembly.md) §4.1 실측을
  근거로 든 쪽이라 그걸 따랐다. 완성된 되물음은 **반말·종결어미가 재료에 박혀** 존댓말 카드로
  갈아끼울 때 카드와 싸운다 — 수집은 배치라 어느 카드가 이 재료로 말할지 그 시점엔 모른다.
  대신 프롬프트가 **미결 여부를 함께 적게** 해서 되물음의 *근거*를 남긴다. 문장 형태는 두뇌 몫.
  ```
  [topic:이직] 사용자는 1주 전부터 이직을 고민하고 있으며 … 결론이 나지 않음.   (실측 출력)
  ```
- **출력 형식은 `[topic:라벨] 문장` 줄 태그**(JSON 아님). 선례가
  [mood.py](../navi/mouth/mood.py) `peel_mood`의 `[mood:key]`이고, 부분 실패가 국소적이라
  모델이 앞에 산문을 붙여도 그 줄만 무시된다(JSON은 전체 파싱이 터진다). 파싱 실패는 예외가
  아니라 폴백.
- **입력은 사용자 턴만.** `recall_recent_for_user`는 assistant 턴도 돌려주는데, 나비가 자기 말에서
  화제를 뽑아 되물으면 세계관이 깨진다. 줄마다 결정론 상대 날짜("어제"·"3일 전")를 붙여
  summary에 시점이 들어가게 한다.
- **dedup_key는 라벨 × 시간창**(구현 시 보강). 원문의 "dedup_key로 같은 주제 반복 적재 방지"를
  문자 그대로 읽으면 **주제가 DB 수명 내내 영구 봉인**돼, 한 번 되묻고 나면 다시는 못 되묻는다.
  창(기본 7일) 안에선 화제당 1건, 창을 넘겨도 사용자가 그 얘길 계속하면 새 후보가 난다 —
  그만뒀다면 최근 N턴에 안 잡혀 자연히 사라지므로 **자기 제한적**이다. 라벨은 LLM이 주므로
  드리프트("이직" vs "이직 고민")를 흡수하는 결정론 정규화를 거쳐 저장한다.

### 3.5 데몬 배선 — `navi/daemon.py`
- `_run`에서 `Feed`를 짓고(저장소·summarizer·설정 주입), DaemonCore에 `feed` 주입
  (`memory_snapshot` 람다와 같은 패턴, [daemon.py:710](../navi/daemon.py:710) 근처).
- `_maybe_initiate`: `pick_topic` 호출 직전 `cands = feed.get_fresh_topics(k)`,
  `topic_feed=[c.summary for c in cands]`. 반환 topic이 `cands[0].summary`면 `mark_used`.
  **후보 수명(fetch→선택→used)은 데몬이 소유** — 3층 `pick_topic`은 순수 힌트 생성기로 남긴다
  (DB 쓰기 없음, Conductor가 사실 인출하는 것과 같은 층위 분리).
- tick 상단(또는 `_maybe_initiate` 밖)에 `feed.maybe_collect(now)` — 시각 게이트 통과 시에만
  수집. 발화 판정과 독립(수집은 발화 안 해도 돎).
  **인라인 `await` 금지 — `asyncio.create_task`로 띄운다**(구현 시 정정, 2026.07.31).
  `collect`는 (N피드 HTTP + M아이템 LLM)이라 최악 수십 초고, `_dispatch`에서 인라인으로
  기다리면 그동안 TICK·UTTERANCE 디스패치가 멎는다. 재진입은 `Feed`의 단일 실행 가드가
  막으므로(PR1에서 구현) 배선은 한 줄이다.

### 3.6 설정 — `config.yaml` `feed:` 섹션 + `navi/config.py` `FeedConfig`
```yaml
feed:
  collect_interval_s: 43200      # 12h → 하루 1~2회
  fresh_topics_k: 3              # pick_topic에 넘길 후보 수
  summarizer:
    vendor: gemini               # echo=헤드리스 검증, gemini=실동(저가 flash)
  interests:                     # 관심사 출처 ① — 사용자 명시 등록
    - topic_key: 축구
      feed_url: https://...rss
  auto_extract: true             # 관심사 출처 ② on/off
  recent_turns_for_extract: 30
```
- 값은 튜닝 대상, 구조 확정(config.py 관례). 섹션 없으면 기본값으로 뜸(하위호환).

## 4. 검증

- **유닛(헤드리스):**
  ① `RssSource` — 픽스처 피드 파싱(정상/빈/깨진 XML → 빈 리스트). ② 저장소 — insert 후
  `fresh_candidates`가 TTL 만료·used 후보를 제외, dedup 충돌 무시, `mark_used`. ③ `collect` —
  fake 소스 + echo summarizer로 rss·memory 후보가 적재되고, 재실행 시 dedup으로 안 늘어남,
  `last_collect_at` 갱신. ④ ② 추출 — 픽스처 턴(같은 주제 반복)에서 콜백 후보 생성.
- **통합(헤드리스):** DaemonCore tick에 fake feed 주입 → `pick_topic`이 후보 summary를
  트리거로 반환하고 `mark_used` 호출됨 → 다음 tick에 같은 후보 재선택 안 됨. 후보 0이면
  기존 시간대 힌트로 폴백(무변경).
- **실기(A3, 사용자):** **이 기능의 목적** — 실 RSS(연합뉴스 or 등록 관심사) + gemini-flash로
  나비가 **실제 뉴스/콜백 재료로 먼저 말 걸기** E2E. TTFA·발화 내용이 플레이스홀더가 아님을
  확인. 그동안 보류였던 A3를 여기서 닫는다(checklist B4→A3).

## 5. 범위 경계 (비목표)

- **안전 필터 본체** — `_is_safe` 훅 자리만 두고 규칙/키워드 blocklist는 넣지 않는다(D13
  MVP 제외). **②가 사용자의 무거운 화제(이별·질병·자책)를 선제로 되꺼내는 위험**이 실재하므로
  필요해지면 이 훅에 결정론 규칙을 채운다(LLM 판단은 안 씀 — 안전 게이트는 규칙 원칙).
  PR2에서 시그니처를 `_is_safe(*, source, topic_key, text)`로 넓혔다 — `RawItem` 바인딩이면
  정작 위험한 콜백 텍스트를 볼 수 없어 **심을 수 없는 씨앗**이었다. 콜백은 지금 추출 *출력*에
  훅이 걸려 있는데, 무거운 대화가 애초에 추출기에 안 닿게 하는 **입력측 게이트**가 더 안전한
  자리다 — 규칙을 채울 때 두 자리를 다 검토할 것.
- **`TurnKind.PROACTIVE_CALLBACK` — PR3 선행 과제(PR2에서 등록).** 현
  [`_PROACTIVE_FRAME`](../navi/conductor.py)은 *"사용자가 알려준 게 아니야"* 라고 말하는데
  **콜백은 사용자가 문자 그대로 알려준 것**이라 사실이 어긋난다.
  [turn_assembly.md](./turn_assembly.md) §5가 이 분기를 *"각 기능이 올 때"* 로 남겨 뒀고 지금이
  그때지만, **데몬이 `candidate.source`를 보고 kind를 고르는 자리라 PR3 소유**다. 그전까지
  콜백은 뉴스와 같은 프레임을 타므로 A3 실기에서 출처 어색함이 관측될 수 있다.
- **뉴스 API·커뮤니티 크롤링** — D13에서 기각. 소스 추가가 필요하면 `FeedSource` 뒤로.
- **②의 외부 뉴스 검색 보강**(추출 키워드 → 검색형 RSS) — MVP ②는 메모리 콜백만, 외부 fetch
  없음. 뉴스로 살 붙이는 건 후속.
- **의미 기반 주제 군집화·중복 판정** — 빈도 카운트 + dedup_key(문자열 해시)로 충분. 임베딩
  (D6)·유사도 병합은 후속.
- **좋은 수집 주기·후보 개수·랭킹 튜닝** — 값은 배선용(진행 원칙 2). interaction_log
  응답률/무시율이 쌓인 뒤 튜닝.
- **다중 사용자** — 단일 사용자 전제 유지(MemoryStore 관례).

## 6. PR 단위 · 커밋 분할(안)

독립 검증 가능한 덩어리 기준으로 나눈다.

- **PR1 `feat(feed): topic_candidate 저장소 + RSS 소스 + collect 오케스트레이터`** — 3.1·3.2·3.3(①
  경로) + summarizer 재사용 + 유닛 §4①②③. **데몬 무관, 헤드리스로 독립 검증.**
  **(완료 2026.07.31 — 349 tests green. `config.py`·`daemon.py` 무변경, 협력자는 전부 주입.
  구현 중 드러난 정정 4건은 §3.2·§3.3·§3.5에 반영.)**
- **PR2 `feat(feed): 대화 빈도 자동 추출(② source=memory)`** — 3.4 + 유닛 §4④. ①과 별개
  **(완료 2026.08.25 — 396 tests green. 재료를 중립 진술로 확정하며 §3.4를 정정했고,
  콜백 TTL·dedup 창을 생성자 인자로 뺐다. `Feed`는 여전히 Config를 모른다.)**
  검증 단위라 분리(작으면 PR1에 합쳐도 무방).
- **PR3 `feat(daemon): 관심사 피드 배선 — tick 수집 + pick_topic 후보 주입`** — 3.5·3.6 +
  통합 테스트 §4. **A3(실기) 여기서 해제.** **선행:** [turn_assembly.md](./turn_assembly.md)의
  선제/응답 분기(TurnKind) — 서술형 피드 요약이 오해 없이 선제 발화로 나가려면 이 분기가 먼저다.
  **(완료 — PR #40 머지)**
  **PR3 착수 시 선행 리팩터 1건**(PR1에서 드러남): `create_brain(config)`가 `config.brain.vendor`만
  읽어([brain/__init__.py](../navi/brain/__init__.py)) `feed.summarizer.vendor`로 별도 벤더를 못
  만든다. `create_brain(config, *, vendor: str | None = None)`로 넓히는 걸 권한다 — 키 부재
  에러 메시지가 한 곳에 남는다. 요약기는 **반드시 자기 인스턴스**여야 한다(어댑터당 동시
  1요청 계약 — 대화용 두뇌와 공유하면 선제 발화 중 수집이 돌 때 `last_result`가 경합).

머지 조건: 각 PR 테스트 green + 본문에 §4 검증 방법·관련 결정(D13)·완료 기준. 실 RSS 피드
URL·API 키는 로컬/`.env`라 PR은 헤드리스(echo·fake 소스)로 닫고, 실동은 A3 트랙(사용자).

## 7. 열린 질문 (구현 착수 전 확인)

- `get_fresh_topics` 반환형: 후보 객체 vs summary 문자열. → **객체 반환**(daemon이 mark_used에
  candidate_id 필요), pick_topic엔 `.summary`만 뽑아 넘겨 3층 계약(`list[str]`)을 안 넓힌다.
- `last_collect_at` 저장 위치: 전용 `feed_meta` 1행 테이블 vs mode_state류 재사용. → 전용
  테이블이 깔끔(수집 상태는 모드와 무관).
- 요약 LLM 실패 시: 그 아이템만 스킵 vs 배치 중단. → **아이템 스킵**(한 피드·한 뉴스로
  배치가 안 죽게, RssSource 실패 처리와 동일 철학).
- ② 추출을 저가 LLM 대신 규칙(형태소 빈도)으로? → 한국어 형태소 부담 커서 **LLM 1회**가
  MVP엔 단순. 정확도 튜닝 시 재론.
- **나비는 바깥소식을 어떻게 아는가?** (2026.07.26 실측에서 드러남 —
  [turn_assembly.md](./turn_assembly.md) §4.1) 선제 발화 시 두뇌가 출처를 **지어낸다**:
  "미리 알아봤어"(gemini) · "어제 뉴스에서 봤는데"(haiku). 나비는 카드상 몸이 없어 집 밖에
  못 나가고 집 안 소리만 듣는 정령이라([navi.yaml](../personas/navi.yaml)) 둘 다 **페르소나
  위반**이다. 프레이밍이 출처를 안 주니 모델이 빈칸을 메운 결과. **PR3 착수 전 결정 필요** —
  선택지 ⓐ카드 background에 경위 서사 추가("사람들 말소리를 주워듣는다" 등), ⓑ선제 프레이밍에
  출처 한 줄 주입, ⓒ둘 다. 세계관 일관성 문제라 카드 소유자(사용자)의 판단 영역.
