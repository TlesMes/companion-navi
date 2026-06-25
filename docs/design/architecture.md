# 반려 AI 데몬 — 설계 문서 (Step 1, v2 — 2026.06.11 개정)

> 범위: 기술 선택(LLM/STT/TTS 벤더, DB 엔진, 하드웨어)처럼
> **결정이 필요한 부분은 의도적으로 비워두고**, 그 결정과 무관하게 확정할 수 있는
> 아키텍처 / 모듈 인터페이스 / 데이터 모델까지만 작성한다.
> 비워둔 항목은 문서 맨 끝 **[보류된 결정사항]** 에 모아둔다.

## v2 개정 요지

1. **D4 결정 완료: 실시간 음성 API(Gemini Live 등) 채택하지 않음.** 직렬 스트리밍 파이프라인으로 간다.
   - 근거: ① 목소리는 데몬의 자산이어야 한다(벤더 내장 음성에 묶이면 나비의 연속성이 벤더에 종속됨) ② 즉답(0.3초급)은 불필요, 첫 오디오 ~1초면 충분 ③ 15분 세션 제한·세션 매니저 재설계 회피.
   - 대가: 턴테이킹(발화 종료 감지·끼어들기·에코)을 데몬이 직접 책임 → **신규 모듈 추가** (4.3절).
2. **혼잣말 전용 파이프라인 제거.** 수동/능동은 트리거 출처의 차이일 뿐, 발화 경로는 전부 단일 스트리밍 파이프라인을 공유한다. "응답을 요구하지 않는 발화" 경험은 유지 — 말 걸고 응답 없으면 그냥 침묵하면 된다.
3. **D2(STT)·D3(TTS) 격상.** Live가 흡수해주지 않으므로 STT는 스트리밍 필수, TTS 음색은 제품 정체성 그 자체가 됨.
4. 02 기술조사 리포트의 "Gemini Live 추천"(2장)은 본 개정으로 폐기.

---

## 1. 설계 원칙 (이 문서 전체를 관통하는 규칙)

1. **두뇌는 빌리고, 살아있음은 데몬이 만든다.** 인격·기억의 연속성은 100% 데몬 쪽에 있다. LLM은 매 호출마다 새로 고용되는 무상태(stateless) 배우다.
2. **목소리도 데몬의 자산이다.** (v2 신설) 음색(voice_profile)은 나비의 정체성이므로 단일 고정이며, TTS 벤더는 어댑터 뒤에 숨긴다. 두뇌를 갈아껴도, TTS 벤더를 갈아껴도 같은 목소리로 말해야 한다.
3. **"언제 말할까"는 멍청하지만 확실한 규칙으로, "무엇을 말할까"는 똑똑한 모델로.** 안전이 걸린 판단(수면 중 침묵 등)은 절대 모델에 맡기지 않는다.
4. **API는 깔때기 끝에서만 부른다.** 들리는 모든 소리를 두뇌로 보내지 않는다. 로컬에서 싸게 거르고 통과한 것만 호출한다.
5. **전 구간 스트리밍.** (v2 신설) 파이프라인의 각 단계는 앞 단계의 완료를 기다리지 않고 부분 결과를 흘려보낸다. 목표: 발화 종료 판정 후 첫 오디오까지 ≤ ~1초.
6. **인터페이스(계약)부터 고정한다.** 각 모듈은 "무엇을 받고 무엇을 내보내는지"만으로 정의된다. 내부 구현·벤더는 교체 가능해야 한다.
7. **상태를 가진 데몬.** 요청-응답 서버가 아니라, 스스로 시간을 인지하며 "지금 뭘 할까"를 주기적으로 자문하는 상시 이벤트 루프다.

---

## 2. 전체 아키텍처

```
        ┌───────────────────────── 데몬 (상시 이벤트 루프) ─────────────────────────┐
        │                                                                            │
마이크 ─►│ [귀 Ear]                [조정 TurnTaking]                                  │
        │  AEC→VAD→웨이크워드 ──►   발화종료 판정 / barge-in 판정                      │
        │      │                       │            ▲                                │
        │      │ (음성 프레임)          │ (발화 확정)  │ (재생 상태)                     │
        │      ▼                       ▼            │                                │
        │  [STT 스트리밍] ──텍스트──► [오케스트레이터: 프롬프트 조립] ──► [두뇌 Brain] │──► LLM API
        │                               ▲    ▲                │ (토큰 스트림)          │    (외부)
        │  [심장 Heartbeat]──능동 트리거─┘    │                ▼                       │
        │   모드게이트→타이밍→주제           [기억 Memory]   [입 Mouth: TTS 스트리밍]   │
        │                                  단기/장기/사실        │                    │
스피커 ◄─│◄───────────────────────────────────────────────────┘                    │
        └────────────────────────────────────────────────────────────────────────────┘
```

핵심 흐름 두 갈래 — **트리거 출처만 다르고 경로는 완전히 같다**:
- **능동(데몬이 먼저):** 능동성 엔진이 "지금 말 걸자" 결정 → 기억 인출 → 프롬프트 조립 → 두뇌(토큰 스트림) → TTS 스트리밍 → 스피커.
- **수동(사용자가 먼저):** 마이크 → 귀(AEC→VAD→웨이크워드) → 턴테이킹이 발화 종료 판정 → STT → 기억 인출 → 조립 → 두뇌 → TTS → 스피커.

레이턴시 예산 (수동 갈래, 발화 종료 판정 → 첫 오디오):

| 구간 | 예산 | 비고 |
| :-- | :-- | :-- |
| 발화 종료 판정 (침묵 대기) | ~500–800ms | **체감 지연에 포함됨.** 짧으면 말 끊고, 길면 멍청해 보임 — 핵심 튜닝값 |
| STT 확정 | ~0–300ms | 스트리밍이면 발화 중 이미 변환 진행, 종료 시 거의 즉시 확정 |
| LLM 첫 토큰 | ~300–500ms | 페르소나 프롬프트 캐싱으로 단축 |
| TTS 첫 오디오 | ~50–150ms | 저지연 TTS 전제 |
| **합계** | **~1–1.5초** | 즉답은 아니지만 "기계 같음"(2–3초 침묵)은 회피 |

---

## 3. 모듈 분담과 책임

| 모듈 | 한 줄 책임 | 안전등급 |
| :--- | :--- | :--- |
| 입력 깔때기 (Ear) | 소리를 듣고 "나한테 한 말인가"를 로컬에서 판별. 에코 제거 포함 | — |
| **턴테이킹 (TurnTaking)** | **(v2 신규)** 발화가 끝났는지, 끼어들었는지 판정하고 말차례를 중재 | — |
| STT | 통과한 음성을 스트리밍으로 텍스트 변환 | — |
| 능동성 엔진 (Heartbeat) | "지금/먼저 말 걸까, 건다면 무슨 주제로" 결정 | **모드게이트=결정론적** |
| 메모리 (Memory) | 대화·사실·친밀도·모드 상태를 저장하고 필요한 조각을 인출 | — |
| 오케스트레이터 (Conductor) | 페르소나+기억+트리거를 한 요청으로 조립 | — |
| 두뇌 어댑터 (Brain) | LLM API 호출(토큰 스트리밍). 벤더 교체 가능하게 추상화 | — |
| 출력 (Mouth) | 토큰 스트림을 음성으로 합성해 즉시 재생. 중단 가능해야 함 | — |
| 스케줄 동기화 (Schedule) | 외부 알람/캘린더에서 기상·취침·일정 읽어오기 | — |
| 관심사 피드 (Feed) | **(Phase 3)** 뉴스·RSS·커뮤니티에서 선톡 주제 재료를 주기 수집 | — |
| 루프 (Daemon Core) | 위 모듈을 묶어 상시 구동하는 이벤트 루프·상태관리 | — |

---

## 4. 모듈 인터페이스 (계약)

> 언어 중립 의사 표기. 시그니처만 고정하고 구현/벤더는 비워둔다.
> 이 계약만 지키면 각 모듈을 독립적으로 만들고 교체할 수 있다.

### 4.1 입력 깔때기 (Ear)
```
on_audio_frame(frame)              # 마이크에서 연속 호출됨
  → 내부: AEC(스피커 출력 신호 제거 — 자기 목소리를 사용자로 착각 방지)
        → VAD(사람 목소리?) → 웨이크워드(호출어?)
  → emit SpeechStarted / SpeechFrame(frame) / SpeechStopped   # 턴테이킹·STT가 구독
  → 웨이크워드 통과 시 emit WakeDetected
  → 강제기상 키워드는 모드게이트를 우회하는 플래그와 함께 emit
```

### 4.2 턴테이킹 (TurnTaking) — v2 신규
```
# 입력: Ear의 음성 이벤트 + Mouth의 재생 상태
on_speech_started(ts)
on_speech_stopped(ts)
  → 침묵이 endpoint_silence_ms 지속되면 emit UtteranceEnded   # "사용자 말 끝남" 확정
                                                              # → STT 확정 → 후단 파이프라인 가동

# barge-in: 데몬이 말하는 중 사용자 음성 감지
on_speech_started(ts) while mouth.is_playing():
  if 음성 길이 ≥ barge_in_min_speech_ms:                      # 기침·생활소음에 멈추지 않기 위한 최소 길이
      mouth.stop()                                            # 재생 즉시 중단
      brain.cancel(active_request_id)                         # 생성 중이던 응답 폐기
      emit BargeIn                                            # 이후 사용자 발화를 새 턴으로 처리
```

### 4.3 STT (스트리밍)
```
open_stream(lang) → stt_session
stt_session.feed(frame)                       # Ear의 SpeechFrame을 흘려보냄 (발화 중 변환 진행)
stt_session.finalize() → { text, confidence, lang }   # UtteranceEnded 시 호출, 거의 즉시 확정
```

### 4.4 능동성 엔진 (Heartbeat)
```
# 1층: 모드 게이트 (결정론적 — 모델 금지)
current_mode(now, schedule, user_overrides) → SLEEP | ACTIVE | DND | SNOOZE
can_speak_now(mode) → bool          # SLEEP/DND/SNOOZE면 false

# 2층: 타이밍 (가중치 + jitter)
should_initiate(now, last_interaction_at, time_weights, jitter) → bool

# 3층: 주제 도출 (작은 모델/LLM 허용)
pick_topic(memory_snapshot, weather, time_of_day, topic_feed) → topic_hint
#   topic_feed ← feed.get_fresh_topics()  (Phase 3 전까지는 빈 리스트)
```

### 4.5 메모리 (Memory)
```
append_turn(session_id, role, text, ts)            # 단기: 대화 기록 적재
recall_recent(session_id, n) → [turn]              # 단기: 최근 n턴
extract_and_store_facts(session_id)                # 장기: 대화→사실 요약 추출
recall_relevant_facts(query, k) → [fact]           # 장기: 관련 사실 k개 인출
get_intimacy(user_id) → score                      # 친밀도 조회
update_intimacy(user_id, delta)                    # 친밀도 갱신
get_mode_state(user_id) → mode_state               # 모드/오버라이드 상태
set_mode_state(user_id, mode_state)
```

### 4.6 오케스트레이터 (Conductor)
```
build_request(trigger, user_id, session_id) → llm_request
  # 조립 재료:
  #   persona_prompt   ← 친밀도에 맞는 페르소나 선택 (캐싱 대상으로 설계)
  #   intimacy_score   ← 수치로 톤 지시
  #   relevant_facts   ← memory.recall_relevant_facts
  #   recent_turns     ← memory.recall_recent
  #   trigger          ← (사용자 발화) 또는 (능동 주제 힌트)
```

### 4.7 두뇌 어댑터 (Brain)
```
generate_stream(llm_request) → token_stream + 종료 시 { full_text, usage }
cancel(request_id)                                 # barge-in 시 생성 중단
# usage(토큰/비용)는 원가 모니터링용으로 항상 반환. 벤더 무관 추상 인터페이스
```

### 4.8 출력 (Mouth)
```
speak_stream(token_stream, voice_profile) → 오디오 청크 합성·즉시 재생
stop()                                             # barge-in 시 즉시 중단 (계약상 필수)
is_playing() → bool                                # 턴테이킹이 구독
# voice_profile은 전 모드 공통 단일값 — 나비의 목소리 (설계 원칙 2)
```

### 4.9 스케줄 동기화 (Schedule)
```
get_wake_time(date) → time | none                  # 외부 알람에서 읽어옴
get_sleep_window() → (start, end)
get_busy_blocks(date) → [block]                    # 캘린더 일정 → DND 후보
# 데몬은 스케줄을 소유하지 않고 외부에서 '빌려온다'
# 주의: "빌려오는" 기술 경로(캘린더 API? 컴패니언 앱?)는 보류된 결정 D11
```

### 4.10 관심사 피드 (Feed) — Phase 3
```
collect()                                  # 배치(하루 1~2회): 소스 수집 → 저가 LLM 요약 → topic_candidate 적재
                                           # 자극적·부정적 이슈는 수집 단계에서 규칙으로 필터 (아침 첫마디 안전)
get_fresh_topics(k) → [topic_candidate]   # 미사용·TTL 유효한 후보 k개
mark_used(candidate_id)                    # 같은 이슈 중복 발화 방지
# 관심사 출처: ① 사용자가 명시 등록한 주제 ② 대화 기억에서 자주 등장하는 주제 자동 추출
```

### 4.11 루프 (Daemon Core)
```
loop tick (예: 수 초~수십 초마다):
  mode = heartbeat.current_mode(now, schedule, overrides)
  if mode == ACTIVE and heartbeat.should_initiate(...):
      topic  = heartbeat.pick_topic(...)
      req    = conductor.build_request(trigger=topic, ...)
      stream = brain.generate_stream(req)
      mouth.speak_stream(stream, voice)
      memory.append_turn(...)
      # 능동 발화 후 응답이 없어도 재촉하지 않는다 — "대답 안 해도 괜찮은" 경험

  # 사용자 발화: UtteranceEnded 이벤트로 들어와 동일 후단 경로 처리
  # barge-in: BargeIn 이벤트 시 현재 턴 폐기 후 새 턴 시작
```

---

## 5. 모드 상태 머신 (결정론적)

```
        알람시각 도달 / 강제기상 키워드
 SLEEP ───────────────────────────────► ACTIVE
   ▲  ◄───────────────────────────────   │
   │        취침창 진입 / "이제 잘게"       │
   │                                      │ "더 잘래" (스누즈)
   │                                      ▼
   │                                    SNOOZE ──(유예시간 경과)──► ACTIVE
   │
 ACTIVE ──"방해하지마"/캘린더 일정──► DND ──(일정 종료/해제)──► ACTIVE
```

규칙:
- SLEEP / DND / SNOOZE 상태에서는 **능동 발화 금지**. 단 강제기상 키워드는 SLEEP/SNOOZE 게이트를 우회.
- 사용자의 즉시 오버라이드("더 잘래" 등)는 항상 자동 판단을 이긴다.
- 상태 전이는 전부 명시적 규칙. 어떤 전이도 LLM 판단에 의존하지 않는다.

---

## 6. 데이터 모델 (논리 스키마 — DB 엔진 무관)

> 컬럼 타입은 논리 수준. 실제 엔진/타입은 [보류된 결정사항] 참고.
> 단, 어떤 엔진을 고르든 아래 엔터티 구조는 그대로 유지된다.

### user
| 필드 | 의미 |
| :-- | :-- |
| user_id (PK) | 사용자 식별자 |
| display_name | 호칭 |
| created_at | 생성 시각 |

### persona (캐릭터 카드)
| 필드 | 의미 |
| :-- | :-- |
| persona_id (PK) | 페르소나 식별자 |
| name | 프로필 이름 — 같은 캐릭터의 단계별 프로필 (예: 서먹한 단계 / 편해진 단계) |
| min_intimacy | 이 프로필이 적용되는 친밀도 하한 |
| background | 배경 서사 — 출신·역사·사용자와의 관계 설정 |
| traits | 성격·가치관·말투 규칙(어미, 호칭, 금지 표현) 서술 |
| example_dialogues | few-shot 대화쌍 10~20개 — 말투 형성의 핵심 재료 |
| dynamics | **(Phase 4)** 성격 역학 계수 JSON — intimacy_gain_rate, opinion_plasticity, mood_volatility 등. 데몬의 상태 갱신 규칙에 곱해져 "같은 말에도 다르게 영향받는 성격"을 만듦 |

> 프로필은 캐릭터당 여러 개(친밀도 단계별)로 작성하고 작성자가 일관성을 유지한다.
> Conductor가 카드를 시스템 프롬프트로 직렬화 — 캐싱 대상이라 카드가 커도 입력비 ≈ 0.

### intimacy
| 필드 | 의미 |
| :-- | :-- |
| user_id (FK) | 대상 사용자 |
| score | 친밀도 점수 (예: 0–100) |
| updated_at | 마지막 갱신 |

### conversation_turn (단기 기억)
| 필드 | 의미 |
| :-- | :-- |
| turn_id (PK) | 턴 식별자 |
| session_id | 대화 세션 |
| user_id (FK) | 사용자 |
| role | user / assistant |
| text | 발화 내용 |
| created_at | 시각 |
| trigger_type | manual(사용자호출) / proactive(능동) |
| interrupted | (v2 추가) barge-in으로 중단된 턴인지 — 턴테이킹 튜닝 데이터 |

### fact (장기 기억 — 구조화된 사실)
| 필드 | 의미 |
| :-- | :-- |
| fact_id (PK) | 사실 식별자 |
| user_id (FK) | 사용자 |
| key | 예: 반려견_이름 |
| value | 예: 콩이 |
| source_turn_id | 어느 대화에서 알게 됐는지 |
| confidence | 추출 신뢰도 |
| created_at / updated_at | 시각 |

### memory_embedding (장기 기억 — 의미 인출용)
| 필드 | 의미 |
| :-- | :-- |
| embedding_id (PK) | 식별자 |
| user_id (FK) | 사용자 |
| source_ref | 원본(turn_id 또는 fact_id) |
| text | 임베딩 원문(요약) |
| vector | 임베딩 벡터 |
> 벡터 저장 방식(전용 Vector DB vs 관계형+확장)은 [보류된 결정사항].

### mode_state
| 필드 | 의미 |
| :-- | :-- |
| user_id (FK) | 사용자 |
| current_mode | SLEEP / ACTIVE / DND / SNOOZE |
| override_until | 오버라이드(스누즈 등) 만료 시각 |
| updated_at | 시각 |

### schedule_cache
| 필드 | 의미 |
| :-- | :-- |
| user_id (FK) | 사용자 |
| wake_time | 외부에서 읽어온 기상 시각 |
| sleep_window_start / end | 취침창 |
| synced_at | 동기화 시각 |

### topic_candidate (관심사 피드 — Phase 3)
| 필드 | 의미 |
| :-- | :-- |
| candidate_id (PK) | 식별자 |
| source | 출처 (RSS / 뉴스 API / 커뮤니티) |
| topic_key | 관심사 분류 (명시 등록 or 대화 빈도 기반 자동 추출) |
| summary | 저가 LLM 요약 — 캐릭터의 입으로 소화시킬 원재료 |
| fetched_at / expires_at | 수집 시각 / 신선도 TTL (뉴스는 2~3일) |
| used_at | 선톡에 사용된 시각 (중복 발화 방지) |

### usage_log (원가 모니터링)
| 필드 | 의미 |
| :-- | :-- |
| log_id (PK) | 식별자 |
| ts | 호출 시각 |
| kind | llm / stt / tts |
| tokens_or_units | 사용량 |
| est_cost | 추정 비용 |

### interaction_log (능동성·턴테이킹 튜닝용)
| 필드 | 의미 |
| :-- | :-- |
| log_id (PK) | 식별자 |
| ts | 시각 |
| event | initiated / user_responded / user_ignored / user_overrode / **barge_in** (v2) / **false_endpoint** (v2 — 말 끝나기 전에 끊은 경우) |
| mode_at_time | 당시 모드 |
| note | 자유 메모("이 타이밍 별로") |

---

## 7. 설정 항목 (값은 튜닝 대상, 구조는 확정)

```
quiet_hours: 기본 취침창 (스케줄 미동기화 시 fallback)
jitter_range: 능동 발화 타이밍 난수 폭
min_gap_between_initiations: 능동 발화 최소 간격
wake_keywords: ["야 일어나", ...]   # 강제기상
override_phrases: ["더 잘래", "조용히 해", ...]
intimacy_thresholds: 페르소나 전환 경계값
proactive_daily_cap: 하루 최대 능동 발화 횟수 (원가/피로 방지)

# v2 신규 — 턴테이킹
endpoint_silence_ms: 발화 종료 판정 침묵 길이 (대화 자연스러움의 절반을 결정)
barge_in_min_speech_ms: 끼어들기로 인정하는 최소 음성 길이 (기침·생활소음 무시)
voice_profile: 단일 고정 — 나비의 목소리. 모드 무관, 벤더 교체 후에도 유지 노력
```

---

## 8. [보류된 결정사항] — 다음 단계에서 결정 필요

| # | 결정 항목 | 상태 / 메모 |
| :-- | :-- | :-- |
| D1 | LLM 벤더 (두뇌) | 보류. 티어 분리 추천(02 리포트): 잡담=저가, 깊은 대화=승격. 어댑터로 교체 자유 |
| D2 | STT 벤더 | **격상 — 스트리밍 필수.** 후보: Deepgram Nova-3 스트리밍($0.0077/분). 로컬 faster-whisper는 스트리밍 구현 부담 확인 필요 |
| D3 | TTS 벤더 | ✅ **결정 완료 (2026.06.18): GPT-SoVITS fine-tune (로컬, Windows native CPU).** 음색=가중치 안정, 톤=레퍼런스 제어. 실청취 합격. 클라우드 폴백(Cartesia 등)은 CPU RTF 부족 판정 시 재검토 |
| D4 | ~~실시간 음성 API 채택 여부~~ | ✅ **결정 완료 (v2): 채택 안 함.** 직렬 스트리밍 파이프라인 + 자체 턴테이킹 |
| D5 | DB 엔진 | 보류. 추천: SQLite (02 리포트) |
| D6 | 벡터 저장 방식 | 보류. 추천: Chroma 로컬 (02 리포트) |
| D7 | 웨이크워드 엔진 | 보류. 추천: Porcupine — 한국어 내장 (02 리포트) |
| D8 | 구동 하드웨어 | 보류. 추천: 1차 노트북/미니PC → 안정화 후 상시기기 (02 리포트) |
| D9 | 친밀도 산식 | 보류(Phase 4). 단조증가 방지(감쇠)·페르소나 전환 히스테리시스 포함. **산식은 공통, 계수는 persona.dynamics가 소유** — 성격마다 같은 신호에 다르게 반응 |
| D10 | 정서 안전장치 정책 | 보류. 베이스라인 권장: 위기발화 감지→자원 안내 + AI 고지 (02 리포트 9장) |
| D11 | 스케줄 "빌려오기" 기술 경로 | (v2 신규) 폰 알람은 외부 API가 사실상 없음. 캘린더 연동 vs 컴패니언 앱 vs 수동 설정 |
| D12 | 턴테이킹 튜닝값 | (v2 신규) endpoint_silence_ms·barge-in 정책. **종이로 결정 불가 — 실구동 튜닝 영역.** interaction_log의 barge_in/false_endpoint로 데이터 수집 |
| D13 | 관심사 피드 소스 | (신규, Phase 3) RSS vs 뉴스 API vs 커뮤니티 크롤링(ToS 주의). 수집 주기·안전 필터 규칙 포함 |
| D15 | VAD 1층 구조 (캐스케이드) | (신규) **구조는 확정: energy(절전 1관문) → silero(voice 판별) → endpointer.** 단 energy 임계는 "침묵만 버리는" 낮은 값 — 판별기 아닌 절전 트리거로. **효과 크기는 D8(하드웨어)에 종속**: 콘센트 미니PC면 절감 미미(단일 파이썬 프로세스라 폰/DSP·서버 사례 안 옴), 라즈베리파이·배터리 기기면 강력. 현재 루프-닫기 단계는 무의존 EnergyVad 단독, silero 승격은 barge-in PR 범위(실시간 voice 판별이 거기선 필수 — STT의 vad_filter 그물이 없음). D14=턴 메타데이터(aliveness.md) |
| D16 | 모드 축 분리 (청취 ⊥ 선톡) | (신규, 2026.06.25) **방향 정리.** 그동안 한 덩어리로 보던 모드를 두 직교 축으로 분리. **청취축**=마이크→STT→LLM 문: 평소 닫힘(STT 비활성), 웨이크워드(D7)로만 열려 endpoint/타임아웃에 닫힘. **선톡축**=SLEEP/ACTIVE/DND/SNOOZE: 나비가 *먼저* 말하는지(`can_speak_now`). 따라서 SLEEP/ACTIVE는 마이크 상태가 아니라 **선톡 상태**를 가리킨다. 연결은 한 방향뿐 — 사용자 발화(웨이크워드)가 선톡축을 ACTIVE로 승격(=강제기상). "자라"(검문①)는 선톡축을 SLEEP으로 내리는 명령. **미결:** 웨이크워드 창 단위(발화 1건 vs 세션+무음 타임아웃)는 D7·D12에 종속 — 실구동 튜닝. 정체성("실시간 대화")상 세션형 유력 |

> 이 결정들은 인터페이스/데이터 모델을 바꾸지 않는다 — 구현 내부에만 영향. 그래서 지금 비워둬도 설계가 무너지지 않는다.
