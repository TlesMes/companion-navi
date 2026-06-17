# 반려 AI 데몬 — 기술/벤더 조사 + 원가 시뮬레이션 (2026.6 기준)

> **[2026.06.11 개정 메모]** D4는 "실시간 API 채택 안 함(직렬 스트리밍)"으로 결정됨 — 설계 문서 v2 참조.
> 이에 따라 본 문서의 **2장 Gemini Live 추천은 폐기**. STT는 스트리밍 필수로(3장 Deepgram 항목 참조),
> TTS는 가장 중요한 결정으로 격상(4장 — 직접 듣고 결정). 가격 조사 수치 자체는 유효.

> 목적: 설계 문서의 [보류된 결정사항] D1~D10을 "돌아오면 결정만 하면 되는" 상태로 만들기.
> 모든 가격은 2026년 6월 웹 조사 기준이며 변동될 수 있음. 출처는 맨 끝.
> 각 항목에 **추천 기본값**을 달아뒀고, 추천 이유와 대안을 함께 적었다.

---

## 0. 한눈에 보는 결론 (추천 스택)

| 결정 | 추천 기본값 | 한 줄 이유 |
| :-- | :-- | :-- |
| D1 두뇌(LLM) | **티어 분리**: 일상 잡담=Gemini 2.5 Flash 또는 Claude Haiku 4.5 / 깊은 대화=Claude Sonnet 4.6 또는 Gemini 3.1 Pro | 능동 잡담은 싼 모델, 정서 교류 순간만 비싼 모델 |
| D4 실시간 대화 | **Gemini Live (3.1 Flash Live)** | STT+LLM+TTS 묶음, 저지연, 한국어 OK, OpenAI Realtime 대비 ~32배 저렴 |
| D2 STT(수동 파이프라인용) | **gpt-4o-mini-transcribe ($0.003/분)** 또는 홈박스에 GPU 있으면 faster-whisper 로컬(무료) | 실시간 대화는 Gemini Live가 STT 흡수하므로 STT는 보조용 |
| D3 TTS(수동 혼잣말용) | **Cartesia Sonic** (최속·저렴·한국어) 또는 OpenAI TTS(저렴·단순). 음색이 제품 핵심이면 ElevenLabs Flash | 혼잣말은 음색 질감이 거의 전부라 품질↔원가 트레이드오프 |
| D5 DB 엔진 | **SQLite 단일 파일** | 인프라 0, 단일 사용자 홈 규모에 충분 |
| D6 벡터 저장 | **Chroma (로컬)** | "벡터DB계의 SQLite", pip 설치 30초, 수백만 벡터까지 OK |
| D7 웨이크워드 | **Porcupine** | 한국어 내장 지원, 온디바이스, 문구 입력→즉시 학습, 무료 티어 |
| D8 하드웨어 | 미니PC 또는 안 쓰는 노트북. (Gemini Live/클라우드 STT 쓰면 **라즈베리파이도 가능** — 무거운 로컬연산 없음) | 두뇌가 API라 GPU 불필요 |
| D9 친밀도 산식 | 대화 빈도/지속/긍정 반응 신호 가중합 (Phase 후반 튜닝) | 초기엔 단순 규칙으로 시작 |
| D10 안전정책 | **필수 구현**: 위기발화 감지→위기자원 안내 + "AI임" 고지 | 2026 미국 NY/CA 법이 의무화. 한국 무관해도 베이스라인으로 채택 |

**핵심 통찰 2개:**
1. **실시간 대화의 레이턴시·원가 숙제(남은 숙제 #1·#3)를 Gemini Live가 거의 통째로 해결한다.** STT→LLM→TTS를 직접 잇는 직렬 파이프라인의 지연·복잡도를 우회하고, OpenAI Realtime의 살인적 단가(분당 $0.18~0.46)도 피한다.
2. **DB·벡터·웨이크워드는 전부 무료·로컬로 끝난다.** 돈이 드는 건 오직 두뇌(LLM)·음성(STT/TTS)뿐.

---

## 1. D1 — 두뇌(LLM) 가격 비교

| 모델 | 입력($/1M tok) | 출력($/1M tok) | 포지션 |
| :-- | --: | --: | :-- |
| Claude Opus 4.8 | 5.00 | 25.00 | 최고가·최고지능 (이 용도엔 과함) |
| Claude Sonnet 4.6 | 3.00 | 15.00 | 깊은 정서 대화용 후보 |
| **Claude Haiku 4.5** | **1.00** | **5.00** | 일상 잡담용 가성비 |
| GPT-5.5 | 5.00 | 30.00 | 최고가 |
| GPT-5.4 | 2.50 | 15.00 | Sonnet급 |
| GPT-5.4 Nano | 0.20 | 1.25 | 초저가 잡담용 |
| Gemini 3.1 Pro | 2.00 | 12.00 | 깊은 대화용 후보(최저가 플래그십) |
| **Gemini 2.5 Flash** | **0.30** | **2.50** | 일상 잡담 최저가 후보 |
| Gemini 2.5 Flash-Lite | 0.10 | 0.40 | 극저가 |

캐싱(반복되는 페르소나 시스템프롬프트에 90% 절감), 배치(50% 절감) 옵션 존재 → 페르소나 프롬프트는 캐싱 대상으로 설계하면 입력비가 거의 0에 수렴.

**추천:** 두 모델 티어로 분리. `pick_topic`/잡담은 Gemini 2.5 Flash나 Haiku, 사용자가 무거운 감정을 꺼낼 때만 Sonnet/Gemini Pro로 승격. 어댑터(Brain)가 추상화돼 있으니 런타임 교체 쉬움.

---

## 2. D4 — 실시간 음성 API (능동 대화 모드의 핵심)

| 옵션 | 가격 | 레이턴시/특성 |
| :-- | :-- | :-- |
| **Gemini Live (3.1 Flash Live)** | 오디오 입력 $0.005/분, 출력 $0.018/분 (≈$0.023/분) | STT+LLM+TTS 통합, 30개 HD 음성·24개 언어(한국어 포함), 세션 15분 제한(컨텍스트 재개 지원). **OpenAI Realtime 대비 ~32배 저렴** |
| OpenAI Realtime (gpt-realtime-1.5) | 오디오 입력 $32/1M·출력 $64/1M tok → 실측 **$0.18~0.46/분** (캐싱·툴출력 정리 시 $0.05~0.10/분) | 품질 높으나 단가 부담 큼 |
| 직렬 파이프라인(STT+LLM+TTS 직접 연결) | 부품별 합산(아래) | 가장 저렴하지만 지연(1.5~3초)·턴테이킹 직접 구현 부담 |

**추천: Gemini Live.** "옆에 있는 존재"의 자연스러운 대화에 필요한 저지연·턴테이킹을 통합 제공하면서, 분당 단가가 거의 무시할 수준($0.023/분). 능동 대화 모드는 이걸로, 단순 혼잣말(수동)은 부품 조합으로 가는 하이브리드가 합리적.

---

## 3. D2 — STT (수동 파이프라인 보조용)

> 능동 대화는 Gemini Live가 STT를 흡수하므로, STT 단독 구매는 "웨이크워드 후 단발 명령 인식" 같은 보조 용도.

| 옵션 | 가격 | 비고 |
| :-- | :-- | :-- |
| 자가호스팅 Whisper / faster-whisper | **사실상 무료**(전기·GPU) | 100시간/월에도 $0. 단 홈박스 연산 필요. 한국어는 99개 언어 중 지원, 형태소 복잡 언어라 영어보다 WER↑ |
| **gpt-4o-mini-transcribe** | **$0.003/분** | 클라우드 최저가급, 설정 간단 |
| Deepgram Nova-3 | 배치 $0.0043/분, 스트리밍 $0.0077/분 | 한국어 우수, 실시간 강점 |
| OpenAI Whisper API | $0.006/분 | 표준 |

**추천:** 홈박스에 GPU가 있으면 faster-whisper 로컬(무료·프라이버시), 없으면 gpt-4o-mini-transcribe.

---

## 4. D3 — TTS (수동 혼잣말의 음색)

| 옵션 | 가격 | 레이턴시(TTFA) | 한국어/비고 |
| :-- | :-- | :-- | :-- |
| **Cartesia Sonic** | 저렴 | **~40~150ms (최속급)** | 40개 언어, 보이스 에이전트용 설계 |
| Deepgram Aura-2 | 저렴 | ~90~250ms | 속도 최우선 |
| OpenAI TTS | ≈$15/1M자 (≈$0.015/1k자) | 양호 | 생태계 단순, 가성비 |
| ElevenLabs Flash v2.5 | 0.5크레딧/자, 오버리지 $0.12~0.30/1k자(플랜별) | ~75ms | 음색 품질 최상, 단가는 위 대비 높음 |

**추천:** 혼잣말은 "음색 질감"이 거의 전부라 두 갈래 — 원가 최우선이면 Cartesia/OpenAI TTS, "이 목소리가 제품의 정체성"이면 ElevenLabs. 처음엔 저렴한 걸로 느낌 검증 후 결정 권장.

---

## 5. D5·D6 — DB / 벡터 저장

- **SQLite**: 단일 파일, zero-config, 단일 사용자 홈 규모에 차고 넘침. 설계 문서의 관계형 테이블(user/turn/fact/intimacy/mode_state...) 전부 수용.
- **Chroma**: "벡터DB계의 SQLite". pip 설치 30초, 로컬 영속, 수천~수백만 벡터까지 OK. `memory_embedding` 테이블 역할.
- (대안) 나중에 다중 사용자·서비스화하면 PostgreSQL + pgvector로 통합 이전. 지금은 불필요.

**추천:** SQLite + Chroma. 인프라 비용 0, 백업은 파일 복사.

---

## 6. D7 — 웨이크워드

- **Porcupine (Picovoice)**: 한국어 **내장 지원**, 완전 온디바이스(프라이버시·무지연), 문구를 타이핑하면 수초 내 모델 학습, 무료 티어 존재. 임베디드/데스크톱/웹 배포.
- (대안) openWakeWord: 오픈소스·무료지만 커스텀 학습에 ML 지식 필요.

**추천:** Porcupine. "야 일어나" 같은 강제기상 키워드도 콘솔에서 바로 생성 가능.

---

## 7. D8 — 하드웨어

- 두뇌가 API이므로 **GPU 불필요**가 기본.
- Gemini Live/클라우드 STT를 쓰면 무거운 로컬 연산이 없어 **라즈베리파이급도 가능**(마이크/스피커 + 데몬만 돌리면 됨).
- 로컬 Whisper로 프라이버시를 챙기고 싶으면 GPU 달린 미니PC 또는 안 쓰는 노트북.

**추천:** 1차는 안 쓰는 노트북/미니PC로 시작(개발 편의) → 안정화 후 라즈베리파이 등 상시기기로 이전.

---

## 8. 원가 시뮬레이션 (단일 사용자, 일상 사용 가정)

> 가정: 능동 혼잣말 10회/일(각 ~40자 발화), 능동 대화 2세션/일·총 10분/일, 페르소나 프롬프트 캐싱 적용.
> 추정치이며 사용 패턴에 민감. 월 30일 기준.

**수동(혼잣말) 모드 월 원가**
- LLM(Haiku, 캐싱): 호출당 입력~1.5k·출력~80tok → ≈$0.0019/회 × 300회 ≈ **$0.6/월**
- TTS: 400자/일 × 30 = 12,000자/월
  - OpenAI TTS(~$0.015/1k자): ≈ **$0.18/월**
  - ElevenLabs(~$0.30/1k자): ≈ **$3.6/월**

**능동(실시간 대화) 모드 월 원가 — 10분/일 = 300분/월**
- Gemini Live(≈$0.023/분): ≈ **$6.9/월**
- OpenAI Realtime(캐싱 $0.05~0.10/분): ≈ **$15~30/월**
- 직렬 파이프라인(STT $0.003/분 + LLM + 저가 TTS): 대략 **$2~5/월** (단 지연·구현부담↑)

**합산 추정 (추천 스택: Haiku + Gemini Live + OpenAI TTS)**
> ≈ $0.6 + $0.18 + $6.9 ≈ **월 $7~8 / 사용자** (개인 본인 사용이면 그대로 운영비)

| 시나리오 | 월 추정 |
| :-- | --: |
| 최저가(저가 TTS·직렬·잡담만) | ~$1~3 |
| 추천 균형(Haiku+GeminiLive+OpenAI TTS) | ~$7~8 |
| 고품질(Sonnet+GeminiLive+ElevenLabs) | ~$15~20 |
| 최악(OpenAI Realtime 다용) | ~$30~50 |

**원가 통제 레버:** ① `proactive_daily_cap`(능동 발화 상한) ② 페르소나 프롬프트 캐싱 ③ 잡담은 저가 모델, 깊은 대화만 승격 ④ 능동 대화는 Gemini Live 고정. → 개인용이면 월 ~$10 이하 유지 현실적. **서비스(다중 사용자)화하면 사용자당 원가 × N이라 구독가 설계 필수.**

---

## 9. D10 — 정서 안전장치 (조사 결과, 무시하면 위험)

2026년 들어 **AI 컴패니언 안전법이 실제 시행 중**(미국 NY 2025.11, CA 2026.1, WA·OR 등 후속). 핵심 의무사항이 곧 좋은 설계 가이드라인이기도 함:

1. **위기 발화 감지·대응:** 자해/자살 암시 표현을 감지하고 위기지원 자원으로 연결하는 프로토콜 필수. 사용자가 스스로 "위기"라고 말하길 기대하지 말 것(언어·패턴 마커로 감지).
2. **AI 고지:** 사람이 아니라 AI임을 세션 시작 시 알리고, 장시간 사용 시(법은 3시간마다) 재고지.
3. **범위 한정:** 정서적 지지·심리교육은 하되 치료/진단은 아님을 명확히. 한계 상황에선 사람(전문가)에게 넘기는 핸드오프 설계 — "언제 멈춰야 하는지 아는 것".
4. **한계 인식:** 생성형 AI는 위험을 정확히 평가하지 못함. 임상가 대체 불가.

> 한국 거주·개인 사용이라 위 미국법이 직접 적용되진 않더라도, 감정 교류가 핵심인 제품 특성상 **위기 감지 + 자원 안내 + AI 고지**는 베이스라인으로 반드시 넣는 걸 권장. 설계 문서의 `interaction_log`/안전 모듈에 위기 감지 훅을 추가하면 됨.

---

## 10. 돌아왔을 때 결정할 것 (체크리스트)

- [ ] D1: 잡담 모델 / 깊은대화 모델 각각 1개 확정
- [ ] D3: 혼잣말 음색 — 원가형(Cartesia/OpenAI) vs 품질형(ElevenLabs)
- [ ] D8: 1차 구동 기기 (노트북? 미니PC? 라즈베리파이?)
- [ ] D10: 위기 감지·고지 정책 채택 여부 (권장: 채택)
- 나머지(D2/D4/D5/D6/D7/D9)는 위 추천 기본값으로 진행해도 무방

---

## 출처

- Claude API 가격: [platform.claude.com/docs/pricing](https://platform.claude.com/docs/en/about-claude/pricing), [finout.io](https://www.finout.io/blog/anthropic-api-pricing)
- OpenAI API 가격: [developers.openai.com/api/docs/pricing](https://developers.openai.com/api/docs/pricing), [tokenmix.ai](https://tokenmix.ai/blog/openai-api-pricing)
- Gemini API 가격: [ai.google.dev/gemini-api/docs/pricing](https://ai.google.dev/gemini-api/docs/pricing), [costgoat.com](https://costgoat.com/pricing/gemini-api)
- OpenAI Realtime 원가: [callsphere.ai](https://callsphere.ai/blog/vw2c-openai-realtime-cost-per-minute-math-2026)
- Gemini Live: [byteiota.com](https://byteiota.com/gemini-live-api-production-vertex-ai/), [the-rogue-marketing TTS/Live 가격](https://the-rogue-marketing.github.io/google-gemini-tts-speech-audio-api-pricing-may-2026/)
- STT 비교: [deepgram.com/learn STT 2026](https://deepgram.com/learn/best-speech-to-text-apis-2026), [buildmvpfast.com](https://www.buildmvpfast.com/api-costs/transcription), [openwhispr.com](https://openwhispr.com/blog/local-vs-cloud-transcription)
- TTS 비교/레이턴시: [gradium.ai TTS latency 2026](https://gradium.ai/content/tts-latency-benchmark-2026), [inworld.ai](https://inworld.ai/resources/best-voice-ai-tts-apis-for-real-time-voice-agents-2026-benchmarks), [ElevenLabs 가격](https://elevenlabs.io/pricing)
- 웨이크워드: [picovoice.ai/platform/porcupine](https://picovoice.ai/platform/porcupine/)
- 벡터/DB: [pecollective Chroma vs pgvector](https://pecollective.com/tools/chroma-vs-pgvector/), [groovyweb.co](https://www.groovyweb.co/blog/vector-database-comparison-2026)
- AI 컴패니언 안전법: [mofo.com NY/CA법](https://www.mofo.com/resources/insights/251120-new-york-and-california-enact-landmark-ai), [davispolk.com](https://www.davispolk.com/insights/client-update/california-and-new-york-launch-ai-companion-safety-laws), [jedfoundation.org](https://jedfoundation.org/american-psychological-association-on-generative-ai/)
