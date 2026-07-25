# 턴 조립 전략 — 선제/응답 요청 분기 (Conductor TurnKind)

> 작성: 2026.07.25. 대상: Conductor 요청 조립([conductor.py](../navi/conductor.py))의 선제/응답
> 분기. 근거: [feed.md](./feed.md)(선제 발화에 실 재료가 들어오며 드러난 결함)·[architecture.md](./architecture.md)
> §4.6(Conductor 계약)·[mood_reference.md](./mood_reference.md)(출력 규칙은 카드 코어 소유).
> **이 문서는 설계 결정 기록** — 확정은 커밋 본문·progress.md에.
>
> **결정 요지(2026.07.25):** ①카드 코어(캐릭터+기본 상황+출력 규칙)는 **단일 캐시 prefix로
> 고정**, mode별로 쪼개지 않는다. ②선제/응답 차이는 **메시지 tail의 mode 블록**으로만 조립한다
> (시스템 프롬프트 포크 금지). ③선제 트리거는 **자기설명형 자연어**(심볼 태그+시스템 바인딩 방식
> 기각). ④지금은 **TurnKind 팩토리**(enum+전략맵), 조립 조각이 늘면 Builder 승격.

## 0. 한 줄

나비의 요청은 `[고정: 캐릭터 카드]`(캐시) + `[매번: 기억 + 트리거]`로 조립된다. 그런데
**선제 발화와 응답은 트리거의 성격이 달라서**(응답=진짜 사용자 말, 선제=나비에게 *주어진*
소재) 같은 방식으로 user 슬롯에 넣으면 LLM이 선제 소재를 "사용자가 한 말"로 오해한다. 카드
코어는 그대로 두고 **"어떻게 응답할지"만 turn kind별로 조립**해 이 오해를 없앤다.

## 1. 고치는 결함 (왜 지금)

현재 [conductor.py:46](../navi/conductor.py:46)은 trigger_text를 **응답이든 선제든 똑같이
`role="user"` 슬롯**에 넣는다. 선제 발화([daemon.py:594](../navi/daemon.py:594) `run_initiation`)도
이 경로를 타므로, 나비가 먼저 말 걸 때조차 트리거가 "사용자 발화인 척" 대화 기록에 얹힌다.

지금까진 우연히 넘어갔다 — 3층 고정 힌트([topic.py](../navi/heartbeat/topic.py))가 **명령형**
("아침 인사를 건네고 물어봐")이라 사용자 말로 안 읽혔다. 그러나 **[feed.md](./feed.md)가 서술형
요약**("○○팀이 3대1로 이겼다")을 흘려 넣기 시작하면 깨진다:

- 원함: `[mood:bright] 야, 어제 그 경기 봤어? 3대1이었잖아!` (먼저 꺼냄)
- 사고: `오 그래? 그거 어떻게 알았어?` (사용자가 알려준 줄 알고 반응)

즉 이건 feed가 만든 문제가 아니라 **feed가 드러낸 기존 잠재 결함**이다. 그래서 feed 배선
(feed.md PR3)의 **선행**으로 여기서 먼저 분기한다.

## 2. 설계 고정점

- **카드 코어는 단일 캐시 prefix — 절대 mode별로 쪼개지 않는다.** [conductor.py:3-5](../navi/conductor.py:3)의
  조립 순서(`[고정: 카드]→[변함: 기억+트리거]`)는 입력비 0 수렴이 목적이다. 카드 코어(배경+
  성격+13개 예시+출력 규칙, 길다)를 선제용/응답용 두 벌로 포크하면 값비싼 캐시가 쪼개진다
  (게다가 친밀도 단계 × 2). **mode 차이를 카드 코어에 끼워 넣지 않는 것**이 최우선 제약.
- **mode 차이는 tail(맨 끝)에 조립한다.** 두 가지 이점: ①카드 코어를 안 건드려 캐시 유지,
  ②지시가 생성 직전(recency 최고) 자리라 저가 모델(haiku·gemini-flash)의 심볼 바인딩
  리스크가 없다. mode 블록은 짧아 매 턴 재전송해도 비용 무시할 만하다.
- **선제 트리거 = 자기설명형 자연어, 심볼 태그 아님.** `[먼저 말 걸기]` 같은 태그를 던지고
  시스템 프롬프트가 그 뜻을 설명하는 방식은 **간접 참조(태그↔시스템 절 바인딩)** 를 만든다.
  저가 모델은 긴 시스템 속 작은 절을 태그로 소환하는 데서 자주 흘린다. 트리거 자체가 상황을
  매 턴 서술("사용자는 지금 아무 말도 안 했어. 아래 소재로 먼저 말 꺼내줘")하면 모델의 규약
  내면화에 안 기대므로 더 안정적이다(2026.07.25 논의 — B안 채택, A안 기각).
- **출력 규칙(무드 태그)은 카드 코어에 남는다 = 전 kind 공통 상속.** [card.py:99-105](../navi/persona/card.py:99)의
  `[mood:...]` 규칙은 응답·선제 모두 필요하므로 kind별 조립 밖(카드 코어)에 둔다. mood_reference
  설계와 정합.
- **소유 원칙 준수.** turn kind를 정하는 것도, 프레이밍을 조립하는 것도 **데몬(결정론)**.
  LLM은 완성품만 본다. "언제/어떻게 말 거는지=규칙, 무엇을 말할까=모델".

## 3. 구현 부품

### 3.1 `TurnKind` + 프레이밍 전략 — [conductor.py](../navi/conductor.py)
```python
class TurnKind(Enum):
    REACTIVE  = "reactive"    # 사용자 발화에 답한다 (trigger = 진짜 발화)
    PROACTIVE = "proactive"   # 나비가 먼저 건다 (trigger = 주어진 소재)

def build_request(self, trigger_text, kind: TurnKind, user_id, session_id):
    system   = self._card.system_prompt(intimacy)   # ① 카드 코어 — 공통, 캐시 (무변경)
    messages = [Message(t.role, t.text) for t in turns]
    messages += self._frame(kind, trigger_text)      # ② mode 블록 + ③ 페이로드
    return LlmRequest(system=system, messages=messages, model=...)

_FRAME = {
  TurnKind.REACTIVE:  lambda t: [Message("user", t)],
  TurnKind.PROACTIVE: lambda t: [Message("user",
      f"(사용자는 지금 아무 말도 안 했어. 아래 소재로 네가 먼저 말을 꺼내줘 — "
      f"사용자가 알려준 게 아니야. 짧게, 되묻지 말고: {t})")],
}
```
- `build_request`에 `kind` 파라미터 추가(기본값 `REACTIVE`로 두면 기존 호출부 무해).
- 프레이밍 문구는 튜닝 대상 — 구조만 확정. 좋은 문구는 실 재료로 A/B(진행 원칙 2).

### 3.2 호출부 분기 — [pipeline.py](../navi/pipeline.py) / [daemon.py](../navi/daemon.py)
- `run_turn`(응답)은 `kind=REACTIVE`, `run_initiation`(선제, [daemon.py:594](../navi/daemon.py:594))은
  `kind=PROACTIVE`. 데몬이 이미 두 경로를 구분해 부르므로([daemon.py:320](../navi/daemon.py:320) vs
  UtteranceEnded 경로) kind 주입만 추가.
- 기억 적재는 무변경 — 선제는 여전히 나비 답변만 `trigger_type=proactive`로 남기고 user 턴을
  지어내지 않는다([daemon.py:598](../navi/daemon.py:598)). **프레이밍 문구도 기억에 안 남는다**
  (트리거는 프롬프트 재료일 뿐).

### 3.3 팩토리 → Builder 승격 기준 (지금은 안 함)
- **지금은 팩토리 수준으로 충분**: `kind` enum + `_FRAME` 전략맵. 조립 조각이 2개(발화/소재)뿐.
- **Builder 승격은 조각이 늘 때**: Phase 4의 `relevant_facts`(장기기억, [conductor.py:5](../navi/conductor.py:5)
  자리 예고)·weather·툴 목록처럼 **선택적 파트가 여러 개** 붙기 시작하면 `.with_facts()
  .with_weather().for_proactive()` 빌더가 값을 한다. 지금 만들면 빈 껍데기 — 만들지 않는다.

## 4. 검증

- **유닛(헤드리스):** ① `build_request(kind=REACTIVE)`는 trigger를 그대로 user 메시지로,
  `kind=PROACTIVE`는 프레이밍으로 감싼 user 메시지로 — 마지막 메시지 텍스트로 확인. ② 두 kind
  모두 **system(카드 코어) 문자열이 동일**(캐시 prefix 불변 회귀 방지). ③ 기억 턴은 kind와
  무관하게 앞에 동일 삽입.
- **통합(헤드리스):** mock brain에 REACTIVE/PROACTIVE 각각 요청 → 프레이밍 차이가 messages에
  실려 나가는지. 선제 프레이밍 문구가 `conversation_turn`에 저장 안 됨을 확인.
- **실기(A3, 사용자):** [feed.md](./feed.md) 실 재료로 선제 발화 시 나비가 소재에 "어떻게
  알았어?"로 되묻지 않고 **먼저 꺼내는지** 청취. 이게 프레이밍이 실제 듣는 것.

## 5. 범위 경계 (비목표)

- **turn kind 세분화** — `PROACTIVE_NEWS`(뉴스)·`PROACTIVE_CALLBACK`(대화 콜백, feed ②)·
  `CRISIS`(정서 안전 D10)로 쪼개는 건 각 기능이 올 때. 지금은 REACTIVE/PROACTIVE 2종.
  구조(enum+전략맵)가 확장을 이미 받는다.
- **시스템 프롬프트에 선제 행동 규칙 상시 절 추가** — 하지 않는다. 행동 지시는 tail 프레이밍에
  둬 카드 코어 캐시를 지킨다. 정말 페르소나급 상시 규칙이 필요해지면 그때 카드 코어 *뒤*
  캐시 breakpoint 후 suffix로(포크 아님) 재론.
- **프레이밍 문구 최적화** — 값은 배선용. 실 재료 A/B 후 튜닝.
- **Builder 패턴 도입** — §3.3 기준 충족(Phase 4 조각 증가) 전까지 안 함.

## 6. PR 단위

- **PR(단독) `feat(conductor): 선제/응답 턴 조립 분기 (TurnKind)`** — 3.1·3.2 + 유닛/통합 §4.
  **feed.md PR3의 선행** — 피드 후보를 흘려 넣기 전에 이 분기가 있어야 서술형 소재가 오해 없이
  선제 발화로 나간다. feed 없이도 독립 검증 가능(mock 재료)하므로 별도 PR.

머지 조건: 테스트 green + 본문에 §4 검증·§2 근거(카드 코어 캐시 불변·tail 조립·자연어 B안).

## 7. 열린 질문 (구현 착수 전 확인)

- `kind` 기본값: `REACTIVE`로 둬 기존 호출부 무해 vs 필수 인자로 강제. → **기본 REACTIVE**
  (하위호환, 선제만 명시 주입).
- 프레이밍을 Conductor(요청 조립) vs Pipeline(턴 실행) 어디가 소유? → **Conductor** — 요청
  조립이 그 책임이고, mood resolver처럼 Pipeline이 페르소나를 모르게 유지.
- 선제 프레이밍이 messages에 들어가면 다음 턴 기억 인출 때 섞이나? → 안 섞임. 프레이밍은
  요청 조립 순간에만 생성되고 `conversation_turn`엔 나비 답변만 저장([daemon.py:616](../navi/daemon.py:616)).
