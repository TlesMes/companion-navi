# 감정 태그 → 레퍼런스 전환 실행 계획 (체크리스트 B3)

> 작성: 2026.07.22. 대상: Phase 3 감정선(체크리스트 B3). 근거 설계: [aliveness.md](./aliveness.md) §0·§1
> (빠른/느린 경로 분리, D14 선행 태그). **이 문서는 실행 계획** — 확정 결정은 커밋 본문과
> progress.md에 남긴다.

## 0. 한 줄

두뇌가 응답 **첫 토큰으로 무드 태그**(`[mood:comfort]`)를 뱉고, 데몬이 그걸 흡수해 그 무드의
**레퍼런스 클립**을 고른 뒤 **나머지 본문만** TTS로 흘린다. 태그 자체는 절대 합성하지 않는다.
D3의 "톤=레퍼런스 제어"를 처음 실제로 쓴다 — **음색(가중치)은 그대로, 레퍼런스만 무드별로 교체**.

## 1. 왜 이 형태인가 (설계 고정점)

- **빠른 경로에 선행 태그로만** 받는다(aliveness §0). 무드는 합성 *전에* 필요해 느린 경로(턴 후
  성찰 패스)에 못 둔다. JSON 한 덩어리 = 스트리밍 붕괴라 기각.
- **벤더 중립.** 태그 흡수·무드→톤 선택은 **파이프라인/데몬 계층**이 소유한다. Mouth 어댑터
  (gptsovits)는 **무수정** — 이미 `speak_stream(tokens, voice)`가 턴마다 VoiceProfile을 받고,
  `voice.vendor_voice_id`(레퍼런스 wav)+`voice.ref_text`로 톤을 정한다([gptsovits.py:340-343](../navi/mouth/gptsovits.py)).
  무드는 그 VoiceProfile을 *이번 턴만* 바꾸는 것으로 환원된다.
- **레퍼런스 교체지 가중치 교체가 아니다.** `set_weights`(torch.load ~수초)와 무관 — 톤 전환은
  `voice`만 갈아끼우면 되므로 지연 0. 페르소나가 소유한 `tones`가 이미 그 데이터
  ([persona/voice.py](../navi/persona/voice.py) `ToneSpec`·`VendorVoice.tones`)다.
- **Brain 계약 최소 변경.** mood는 별도 필드가 아니라 **스트림 앞머리의 텍스트**로 온다. Brain
  어댑터는 여전히 텍스트 토큰만 흘린다(`BrainResult` 확장 불필요). 강제는 **시스템 프롬프트
  말미의 출력 규칙**으로, 파싱은 데몬이.

## 2. 데이터 흐름

```
LLM 스트림:  [mood:comfort] 고생했네. 뭐가 제일 힘들었어?
             └─ 데몬 흡수 ──┘ └────────── TTS 직행 ──────────┘
                  │
                  └→ mood=comfort → tone("comfort") → VoiceProfile(ref=comfort.wav, ref_text=…)
                                                        → 이번 턴 speak_stream(voice=그것)
```

- 태그 없음/미매칭 → **`mood:neutral`(기본 톤=`tones[0]`) 폴백** (aliveness §1.2 "폴백은 결정론").
- 무드 셋(1차): **`neutral`(평상)·`bright`(신남)·`comfort`(위로)·`calm`(차분)** — aliveness §1.1의
  차분/신남/위로/평상에 대응. 영문 키로 고정(태그 파싱·톤 name 매칭 안정).

## 3. 구현 부품

### 3.1 무드 파서 — `navi/mouth/mood.py` (신규)
스트림 앞머리에서 `[mood:<key>]`를 떼어내는 스트림 미들웨어.
- 입력 `AsyncIterator[str]` → 출력 `(mood_key, AsyncIterator[str])` 또는 파이프라인이 쓰기 쉬운
  형태(예: `async def peel_mood(tokens) -> tuple[str, AsyncIterator[str]]`, 앞 토큰만 버퍼링 후
  나머지는 지연 없이 통과).
- 태그는 토큰 경계에 걸쳐 쪼개져 온다(`[`, `mood`, `:`, `com`, `fort]`) → `]` 또는 짧은 안전
  한도(예: 앞 32자)까지만 버퍼. 그 안에 여는 `[mood:`가 없으면 **즉시 폴백 통과**(TTFA 무손상).
- 미지 키·형식 오류 → `neutral`. 앞의 공백 흡수(태그 뒤 첫 글자부터 합성).
- **불변식: 태그 문자열은 절대 하류(TTS)로 새지 않는다** — "대괄호 기쁨" 사고 방지.

### 3.2 무드 → 톤 해석 — `PersonaVoice` 확장 ([persona/voice.py](../navi/persona/voice.py))
- `ToneSpec`에 `mood: str = ""` 필드 추가(카드 톤에 `mood: comfort` 선언). 빈 값 톤은 무드
  매칭 대상 아님(기본 톤은 항상 폴백이라 별도).
- `PersonaVoice.tone_for_mood(vendor, mood) -> ToneSpec` — 그 무드의 톤, 없으면 `default_tone`.
- `VoiceProfile`은 그대로(`profile(tone)`이 이미 톤→프로필 변환). **모델 계약 무변경.**

### 3.3 파이프라인 배선 — [pipeline.py](../navi/pipeline.py)
- `run_turn`이 brain 스트림을 `peel_mood`로 감싸 `(mood, body_tokens)`를 얻고, **이번 턴 voice**를
  고른 뒤 `speak_stream(body_tokens, turn_voice)` 호출.
- 톤 해석은 파이프라인이 페르소나를 몰라도 되게 **주입된 resolver**로: `mood_voice:
  Callable[[str], VoiceProfile] | None`. `None`이면 기존 `self._voice` 고정(하위호환).
- **소유는 SwapRuntime** — 이미 `PersonaVoice`와 현재 벤더·기본 톤을 쥐고 있다
  ([control/runtime.py](../navi/control/runtime.py)). resolver = `mood → tone_for_mood → profile`.
  컨트롤 플레인의 `set_voice`(사용자가 고른 기본 톤)는 **폴백 기준**으로 남는다 — 무드는 그 위의
  *일시* 오버라이드, 영속 교체 아님.

### 3.4 시스템 프롬프트 출력 규칙 — [card.py](../navi/persona/card.py) `system_prompt`
- 말미(대화 규칙 뒤)에 강제 문구 추가: "응답의 **맨 앞에 반드시** `[mood:neutral|bright|comfort|calm]`
  중 하나를 붙인다. 이 태그는 시스템 신호이며 **대사가 아니다** — 그 뒤에 실제 말을 잇는다."
- **캐싱 불변식 유지**: 친밀도 단계와 무관한 고정 문자열이라 캐시 안정(system_prompt docstring 준수).
- **traits의 "감정 태그 금지"와 상충 아님**을 명시 — 금지 대상은 *대사 안의* 대괄호 행동/감정
  묘사고, 이건 *맨 앞 1개 제어 토큰*이다. 카드 traits는 건드리지 않는다.

## 4. 검증

- **유닛(헤드리스):** ① 파서 — 분할 토큰/태그 누락/미지 키/정상 4케이스, 태그가 body에 안 샘 ·
  ② `tone_for_mood` — 매칭/미매칭 폴백 · ③ 파이프라인 — mock brain이 `[mood:comfort]…`를 흘리면
  `speak_stream`이 comfort 톤 VoiceProfile로 불리고 합성 텍스트에 `[mood`가 없음.
- **실기(A 트랙, 사용자):** gptsovits 다톤 카드 필요 — `aris` 카드에 무드 톤 2~3개(comfort·bright)
  레퍼런스 wav 추가 후, "피곤하다"→comfort / "칭찬받았어"→bright 톤으로 **레퍼런스가 실제
  바뀌는지 청취**. supertonic 카드(navi)는 톤이 기본 1개뿐이라 항상 폴백(무해) — 실동은 gptsovits에서만.

## 5. 범위 경계 (비목표)

- **느린 경로 전체**(fact 추출·친밀도 신호·성찰 패스, aliveness §1.3) — 별도 작업. 여기선 빠른
  경로의 mood만.
- **D14 계약 확정**(BrainResult 확장 형태) — 이번엔 mood를 스트림 텍스트로 받아 계약을 안 넓힌다.
  나머지 메타데이터가 필요해질 때 D14로 재론.
- **결정론 무드 분석기**(규칙/소형분류기) — 폴백은 `neutral` 상수로 충분. 정확도 튜닝은 후속.
- **감정별 가중치** — D3 위반. 무드는 레퍼런스만 바꾼다(음색=가중치 고정).

## 6. PR 단위 · 커밋 분할(안)

**PR 1개**(feat, `feat/mood-reference` 또는 `feat/order5-mood`). 커밋 3개 제안:
1. `feat(mouth): 무드 선행 태그 파서 + 톤 매핑` — 3.1·3.2 + 유닛(어댑터·파이프라인 무관, 독립 검증).
2. `feat(pipeline): 무드→레퍼런스 일시 전환 배선` — 3.3 + SwapRuntime resolver + 통합 테스트.
3. `feat(persona): mood 출력 규칙 + aris 무드 톤` — 3.4 + 카드 톤 자산(실동 조건).

머지 조건: 유닛 green + PR 본문에 §4 검증 방법·관련 결정(D3·D14 선행 태그·aliveness §1.2). 실기
청취는 A 트랙(사용자)로 물리며, 자산(무드 레퍼런스 wav)은 gitignore된 로컬이라 PR은 헤드리스로 닫는다.

## 7. 열린 질문 (구현 착수 전 확인)

- 무드 키 4종이 맞나, 아니면 `neutral`만 두고 점진 확장? → **4종 스키마로 열되 카드가 선언한
  톤만 실동**(미선언 무드는 폴백)이 안전. 카드가 최소 톤만 가져도 깨지지 않음.
- resolver 주입 위치: 생성자 vs `set_mood_voice()`. 페르소나 핫스왑 시 재바인딩이 필요하므로
  **SwapRuntime이 페르소나 교체 때 갱신**하는 세터가 자연스럽다(set_card·swap_persona 옆).
