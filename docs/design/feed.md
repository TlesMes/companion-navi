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
- **안전 필터는 MVP에서 뺀다(D13), 씨앗만 남긴다.** 관심사가 이미 큐레이션돼(사용자가 고른
  피드·사용자가 한 말) 범용 뉴스 헤드라인을 긁을 때만 필요한 게이트다. `collect()`에
  `_is_safe(item)` 훅 자리만 두고 지금은 항상 True. **②가 사용자의 무거운 화제(이별·질병)를
  선제로 되꺼낼 위험**은 실재하므로(§5) 이 자리를 남겨 두는 것 자체가 설계다.

## 2. 데이터 흐름

```
[배치, tick이 12h 게이트 통과 시]  collect():
  ① RssSource(url).fetch() ──▶ 신선·미적재 아이템 몇 개 ──▶ summarize(LLM) ──┐
  ② recall_recent_for_user ──▶ 빈도 상위 주제 ──▶ callback(LLM) ───────────┤
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
      expires_at   TEXT,                   -- 뉴스 2~3일 TTL, memory 콜백은 NULL(길게)
      used_at      TEXT                    -- 선제 발화 사용 시각(중복방지)
  );
  CREATE UNIQUE INDEX IF NOT EXISTS idx_candidate_dedup ON topic_candidate (dedup_key);
  ```
- 메서드: `insert_candidate(...)`(dedup 충돌 시 무시), `fresh_candidates(k, now)`(used_at IS NULL
  AND (expires_at IS NULL OR expires_at > now), 최신순 k개), `mark_candidate_used(id, now)`,
  `last_collect_at()`/`set_last_collect_at(now)`(수집 게이트 상태 — mode_state처럼 작은 메타 행 or
  `feed_meta` 1행 테이블. 재기동 직후 재수집 방지).

### 3.2 소스 어댑터 — `navi/feed/rss.py` (신규) + `base.py`
- `base.py`: `FeedSource` 프로토콜 — `fetch() -> list[RawItem]`(제목·요약·링크·게시시각).
  나중 소스 확장의 봉합선. `RawItem`은 벤더 중립 dataclass.
- `rss.py`: `RssSource(feed_url, topic_key)` — `feedparser.parse`로 항목 추출. 블로킹 HTTP라
  호출부(`collect`)가 `to_thread`로 감싼다. 실패(네트워크·파싱)는 빈 리스트로 삼켜 배치가
  한 피드 때문에 안 죽게.

### 3.3 오케스트레이터 — `navi/feed/collect.py` (신규)
계약(arch §4.10) 3함수를 소유한다.
- `collect(now)`:
  1. `_is_safe` 훅(현재 항상 True — §5 씨앗) 통과한 ① 아이템만 남김.
  2. 각 아이템 원문 → `summarize`(저가 LLM) → `insert_candidate(source='rss', ...)`.
     이미 적재된 건(dedup_key) 요약 호출 **전에** 걸러 LLM 낭비 방지.
  3. ② `recall_recent_for_user` → 빈도 상위 주제 → `callback`(저가 LLM, 최근 턴을 콜백
     문장으로) → `insert_candidate(source='memory', ...)`.
  4. `set_last_collect_at(now)`.
- `get_fresh_topics(k) -> list[TopicCandidate]`, `mark_used(candidate_id)` — 저장소 위임.
- **저가 LLM은 `BrainAdapter` 재사용.** `generate_stream`을 한 번 소진해 전문을 모으는
  1회성 요약 헬퍼. 벤더는 config `feed.summarizer`(기본 echo=헤드리스, 실동은 gemini-flash).
  스트리밍 아님·티어 분리(D1)와 무관.

### 3.4 ② 대화 빈도 추출 — `collect.py` 내 함수
- 규칙(결정론)으로 "요즘 자주 나온 주제"를 고르는 건 한국어 형태소 분석(konlpy 등) 부담이
  커서, **최근 N턴을 저가 LLM에 넘겨 "자주 등장한 주제 1~2개 + 자연스러운 되물음 문장"을
  받는다**(요약/추출 = "무엇을 말할까"라 LLM 허용). 1회 호출. 추출 결과가 비면 ② 스킵.
- topic_key는 LLM이 준 주제 라벨, summary는 되물음 문장. dedup_key로 같은 주제 반복 적재 방지.

### 3.5 데몬 배선 — `navi/daemon.py`
- `_run`에서 `Feed`를 짓고(저장소·summarizer·설정 주입), DaemonCore에 `feed` 주입
  (`memory_snapshot` 람다와 같은 패턴, [daemon.py:710](../navi/daemon.py:710) 근처).
- `_maybe_initiate`: `pick_topic` 호출 직전 `cands = feed.get_fresh_topics(k)`,
  `topic_feed=[c.summary for c in cands]`. 반환 topic이 `cands[0].summary`면 `mark_used`.
  **후보 수명(fetch→선택→used)은 데몬이 소유** — 3층 `pick_topic`은 순수 힌트 생성기로 남긴다
  (DB 쓰기 없음, Conductor가 사실 인출하는 것과 같은 층위 분리).
- tick 상단(또는 `_maybe_initiate` 밖)에 `await feed.maybe_collect(now)` — 시각 게이트
  통과 시에만 `to_thread(collect)`. 발화 판정과 독립(수집은 발화 안 해도 돎).

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
- **PR2 `feat(feed): 대화 빈도 자동 추출(② source=memory)`** — 3.4 + 유닛 §4④. ①과 별개
  검증 단위라 분리(작으면 PR1에 합쳐도 무방).
- **PR3 `feat(daemon): 관심사 피드 배선 — tick 수집 + pick_topic 후보 주입`** — 3.5·3.6 +
  통합 테스트 §4. **A3(실기) 여기서 해제.** **선행:** [turn_assembly.md](./turn_assembly.md)의
  선제/응답 분기(TurnKind) — 서술형 피드 요약이 오해 없이 선제 발화로 나가려면 이 분기가 먼저다.

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
