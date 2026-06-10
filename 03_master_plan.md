# companion-navi — 마스터 플랜

> 최초 작성: 2026.06.11. 프로젝트의 로드맵·원칙·기술 스택과 그 선정 이유의 단일 출처.
> 상세 설계는 [01_daemon_architecture.md](./01_daemon_architecture.md), 벤더 조사·원가는 [02_tech_research_cost.md](./02_tech_research_cost.md) 참조.

---

## 1. 프로젝트 정체성

- **출발점:** "혼자 있는 아침, 옆에서 자연스럽게 떠들어주는 목소리 — 내가 대답 안 해도 괜찮은." 나아가 반려자처럼 실시간 음성 대화가 가능한 존재.
- **만드는 것:** AI가 아니라 **AI를 살아있게 만드는 오케스트레이터 데몬**. 두뇌(LLM)는 API로 빌리고, 살아있음·기억·인격은 전적으로 데몬이 만든다.
- **해자:** 데몬 + 누적된 기억 DB. 두뇌를 갈아껴도 "그 애"는 유지된다.
- **폼팩터:** 집 안 상시 켜진 기기에서 도는 파이썬 데몬 + USB 마이크/스피커. GPU 불필요.

---

## 2. 로드맵

### Phase 0 — 기획·설계 ✅ 완료 (2026.06)
콘셉트 확정, 설계 문서 v2(직렬 스트리밍 + 자체 턴테이킹), 기술조사·원가 시뮬레이션(월 ~$2~5 예상), git repo 구축.

### Phase 1 — 텍스트 뼈대: "그 애"가 존재하기 시작
음성 없이 CLI 텍스트 대화로 핵심 루프 구축: Conductor(프롬프트 조립) + Brain 어댑터 + 단기기억 + SQLite. 페르소나 프롬프트로 말투 확립.
**완료 기준:** 껐다 켜도 어제 대화를 기억한다. Brain 어댑터에서 LLM 벤더를 바꿔도 같은 애처럼 말한다.

### Phase 2 — 음성화: 듣고 말하는 존재
직렬 스트리밍 파이프라인: 웨이크워드 → VAD → 스트리밍 STT → Brain → 스트리밍 TTS. 턴테이킹 기본형(발화 종료 판정). **D3(TTS 음색) 결정이 이 단계의 관문.**
**완료 기준:** 부르면 ~1.5초 안에 음성으로 답한다. 발화 종료 오판(false_endpoint)이 견딜 만한 수준.

### Phase 3 — 능동성: 먼저 말 거는 존재
Heartbeat 3층(모드게이트→타이밍→주제) + 스케줄 동기화(D11) + 선톡. interaction_log로 응답률/무시율 수집 시작.
**완료 기준:** 자는 시간에 절대 말 안 건다. "더 잘래"가 즉시 먹힌다. 선톡 응답률이 무시율보다 높다.

### Phase 4 — 기억·인격 심화: 같이 산 시간이 쌓이는 존재
장기기억(사실 추출→벡터 검색), 사실 충돌·망각 규칙, 친밀도 산식(D9)·페르소나 전환(히스테리시스 포함).
**완료 기준:** 한 달 전 이야기를 자연스럽게 다시 꺼낸다. 친밀도에 따른 톤 변화가 체감된다.

### Phase 5 — 완성: 같이 살 만한 존재
barge-in 완성, 턴테이킹 튜닝(D12), 정서 안전장치(D10), 고장 모드(API 장애 시 행동), 상시기기 이전(D8).

---

## 3. 원칙

### 제품 원칙
1. **두뇌는 빌리고, 살아있음은 데몬이 만든다.** 인격·기억의 연속성은 100% 데몬 소유. 벤더 종속 설계는 거부.
2. **목소리도 데몬의 자산이다.** voice_profile 단일 고정. 두뇌·TTS 벤더를 갈아껴도 같은 목소리.
3. **"언제"는 규칙, "무엇"은 모델.** 안전이 걸린 판단(수면 중 침묵)은 절대 LLM에 맡기지 않는다.
4. **사용자 오버라이드는 항상 이긴다.** "더 잘래" 한마디가 모든 자동 판단을 덮는다.
5. **API는 깔때기 끝에서만.** 평소엔 로컬로 듣기만. 원가는 proactive_daily_cap과 프롬프트 캐싱으로 통제.
6. **전 구간 스트리밍.** 첫 오디오까지 ~1초. 즉답은 포기했지만 2초 침묵은 금지.
7. **정서 안전은 베이스라인.** 위기 발화 감지→자원 안내, AI 고지. 법 적용 여부와 무관하게 탑재.
8. **한국어 퍼스트.** 한국어 지원은 필수이고, 한국어만 지원해도 무방하다. 음성 부품(STT/TTS/웨이크워드)은 글로벌 벤치마크가 아니라 **한국어 품질**로 선정한다. (한국어 STT는 WER이 아닌 CER로 평가)

### 진행 원칙
1. **계약부터 고정.** 인터페이스/데이터 모델을 먼저 정하고 구현은 그 뒤에서 교체.
2. **종이보다 실구동.** 눈치(타이밍)·턴테이킹 임계값은 설계로 못 정한다. interaction_log 데이터로 튜닝.
3. **결정은 D번호로 추적.** 보류 결정 목록(01 문서 8장)을 유지하고, 결정 시 문서·커밋에 근거를 남긴다.
4. **repo가 기억의 원본.** 문서·결정·이력 모두 git에.
5. **단계별 완료 기준을 만족해야 다음 Phase로.** "되는 것 같음"이 아니라 측정 가능한 기준으로.

---

## 4. 기술 스택 — 영역별 설명과 선정 이유

> 2026.06.11 재평가 완료. 기존 추천(02 리포트) 대비 **변경 2건**(VAD, 벡터 저장), 나머지 유지.

| 영역 | 선정 | 상태 |
| :-- | :-- | :-- |
| 데몬 본체 | Python (asyncio) | 확정 |
| LLM 잡담 (D1) | Gemini 2.5 Flash 또는 Claude Haiku 4.5 | 후보 |
| LLM 깊은 대화 (D1) | Claude Sonnet 4.6 또는 Gemini 3.1 Pro | 후보 |
| 실시간 음성 API (D4) | 사용 안 함 — 직렬 스트리밍 | ✅ 확정 |
| 웨이크워드 (D7) | Porcupine | 사실상 확정 |
| VAD | **TEN VAD** (Silero에서 변경) | 사실상 확정 |
| AEC | WebRTC AEC 계열 | 사실상 확정 |
| STT (D2) | **리턴제로(VITO) 또는 Clova Speech** (한국어 1위권) vs Deepgram Nova-3 | 후보 (한국어 퍼스트로 재편) |
| TTS (D3) | **수퍼톤** vs Cartesia Sonic 3.5 (ElevenLabs는 한국어 발음 문제로 강등) | **미정 — 최중요 결정** |
| DB (D5) | SQLite | 사실상 확정 |
| 벡터 저장 (D6) | **sqlite-vec** (Chroma에서 변경) | 사실상 확정 |
| 임베딩 | 미정 (Phase 4에서 결정) | 미정 |
| 스케줄 연동 (D11) | 캘린더 API vs 컴패니언 앱 | 미정 |
| 하드웨어 (D8) | 1차 노트북/미니PC → 상시기기 | 방향 확정 |

### 데몬 본체 — Python + asyncio
상시 이벤트 루프(수 초 단위 tick)와 오디오 스트림·API 스트림의 동시 처리가 본질이라 비동기 단일 프로세스가 적합. 모든 음성 AI SDK(Porcupine, Deepgram, Cartesia 등)가 Python을 1급 지원. 성능 병목은 전부 네트워크 I/O라 언어 속도 무관.

### LLM (D1) — 티어 분리, 어댑터 뒤에서 교체 자유
LLM은 "매 호출 새로 고용되는 무상태 배우"이므로 특정 모델에 정붙일 이유가 없다. 잡담·주제도출은 저가 모델(Flash/Haiku), 무거운 감정 대화만 상위 모델로 승격 — 원가 시뮬레이션상 이 분리가 월 원가를 수 배 가른다. 페르소나 시스템 프롬프트는 캐싱 대상으로 설계해 입력비를 0에 수렴시킨다.

### 실시간 음성 API (D4) — 채택 안 함 ✅
Gemini Live 등은 저지연·턴테이킹을 통째로 주지만, ① 목소리가 벤더 내장 음성에 묶여 "그 애"의 연속성이 벤더 소유가 되고 ② 15분 세션 제한·세션 매니저 재설계가 필요하며 ③ 사용자 요구가 즉답(0.3초)이 아닌 ~1초 수준이라 직렬 스트리밍으로 충분. 대가로 턴테이킹을 자체 구현(01 문서 4.2절).

### 웨이크워드 (D7) — Porcupine
완전 온디바이스(프라이버시·무지연·무료 티어), **한국어 내장 지원**, 콘솔에서 문구 입력만으로 커스텀 키워드("야 일어나") 즉시 생성. 대안 openWakeWord는 커스텀 학습에 ML 지식이 필요해 탈락.

### VAD — TEN VAD ⚡ 변경 (기존: Silero VAD)
재평가 결과 교체. TEN VAD가 Silero 대비 ① **발화→침묵 전환 감지가 수백 ms 빠름** — 발화 종료 판정(endpoint)이 체감 지연의 첫 구간이라 이 차이가 그대로 응답 속도가 됨 ② 짧은 침묵 구간 식별 정확도 우수 ③ 연산량·메모리·라이브러리 크기 모두 작음(라즈베리파이 이전에 유리). Silero는 검증된 차선책으로 유지.

### AEC — WebRTC AEC 계열
barge-in(말 끊기)을 위해 데몬이 말하는 중에도 마이크를 들어야 하는데, 스피커 출력이 마이크로 되돌아와 자기 목소리를 사용자로 착각하는 문제를 막는 필수 부품. WebRTC AEC는 업계 표준이고 Python 바인딩이 성숙.

### STT (D2) — 한국어 퍼스트로 후보 재편 (2026.06.11)
한국어 인식 벤치마크(CER 기준)에서 **리턴제로(VITO) 엔진이 1·2위(평균 에러율 8% 미만), Naver Clova Speech가 그 뒤**. 둘 다 실시간 스트리밍 지원. 글로벌 벤더 중에선 Deepgram Nova-3가 한국어 강세(WER 27% 개선, sub-300ms, $0.0077/분). **선정 기준: 한국어 CER ≫ 스트리밍 레이턴시 > 가격.** Phase 2 진입 시 실제 발화(혼잣말·구어체)로 3사 비교 테스트 후 결정.
참고: Deepgram Flux(모델 기반 턴 감지 내장 — D12 부담을 크게 줄여줌)는 한국어 미지원(2026.6 현재 10개 언어)이라 탈락. 한국어 지원 시 재검토.

### TTS (D3) — 미정, 최중요 결정 (한국어 퍼스트로 후보 재편)
목소리 = "그 애"의 정체성이라 스펙이 아니라 귀로 결정해야 함. 한국어 퍼스트 기준으로 후보 재편:
- **수퍼톤(Supertone)** — 한국어 특화(국내, 음성 연기 품질 강점), 스트리밍 + 저지연 모델(supertonic_api_3) 보유. 특히 **온디바이스 TTS(Supertonic, ONNX 오픈소스)** 옵션은 목소리를 로컬에 소유 — "목소리도 데몬의 자산" 원칙에 가장 정합.
- **Cartesia Sonic 3.5** — TTFA ~82ms 최속급, 한국어 포함 42개 언어. 단 한국어 음질은 직접 검증 필요.
- **ElevenLabs — 강등.** 음색 표현력은 최상이나 한국어 발음이 "외국인이 한국어를 읽는 느낌"이라는 평가가 반복됨. 한국어 퍼스트 기준 부적합.
Phase 2 진입 시 같은 한국어 대사로 수퍼톤·Cartesia(+Typecast 등 국내 대안) 청취 비교 후 결정.

### TTS (D3) — 미정, 최중요 결정
목소리 = "그 애"의 정체성이라 스펙이 아니라 귀로 결정해야 함. 후보: **Cartesia Sonic 3.5**(TTFA ~82ms 최속, 42개 언어, 저렴) vs **ElevenLabs Flash v2.5**(TTFA ~150ms, 음색 표현력 최상, 고가). 저가 대안으로 MiniMax(<250ms, 40+ 언어)도 등장. Phase 2 진입 시 한국어 샘플을 직접 듣고 결정.

### DB (D5) — SQLite
단일 사용자 홈 규모에 서버형 DB는 과잉. 단일 파일 = 백업이 파일 복사 한 번, 인프라 비용 0. 설계 문서의 모든 테이블(turn/fact/intimacy/mode_state/usage_log...) 수용.

### 벡터 저장 (D6) — sqlite-vec ⚡ 변경 (기존: Chroma)
재평가 결과 교체. sqlite-vec은 SQLite 확장이라 **장기기억 벡터가 같은 SQLite 파일 안에 들어감** → "그 애의 기억 전체 = 파일 하나"가 되어 백업·이식·해자 관리가 극단적으로 단순해짐. 순수 C·무의존성이라 라즈베리파이에서도 동작. brute-force 검색이지만 개인 기억 규모(수천~수만 벡터)에선 ms 단위라 무관. 수백만 벡터 규모가 되면 그때 Chroma/pgvector로 이전.

### 하드웨어 (D8) — 1차 노트북/미니PC
두뇌가 API라 GPU 불필요. TEN VAD·sqlite-vec 채택으로 로컬 부하가 더 줄어 최종적으로 라즈베리파이급 상시기기 이전이 현실적. 개발 단계는 편의상 노트북/미니PC.

---

## 5. 재평가 출처 (2026.06.11)

- TEN VAD: [GitHub — TEN-framework/ten-vad](https://github.com/TEN-framework/ten-vad), [Hugging Face](https://huggingface.co/TEN-framework/ten-vad), [Picovoice VAD 비교](https://picovoice.ai/blog/best-voice-activity-detection-vad/)
- STT: [Deepgram Nova-3 리뷰·벤치마크](https://transcriber.talkflowai.com/blog/deepgram-nova-3-review-benchmarks-pricing), [Flux Multilingual 출시(한국어 미포함)](https://deepgram.com/learn/deepgram-launches-flux-multilingual-press-release), [2026 STT 비교](https://futureagi.com/blog/speech-to-text-apis-in-2026-benchmarks-pricing-developer-s-decision-guide/)
- TTS: [TTS 레이턴시 벤치마크 2026](https://gradium.ai/content/tts-latency-benchmark-2026), [ElevenLabs vs Cartesia](https://futureagi.com/blog/elevenlabs-vs-cartesia-tts-2026/), [Cartesia/ElevenLabs/MiniMax 비교](https://www.famulor.io/blog/cartesia-sonic-elevenlabs-and-minimax-the-ultimate-comparison-for-ai-voice-agents-and-famulors-strategic-advantage)
- 벡터: [sqlite-vec GitHub](https://github.com/asg017/sqlite-vec), [임베디드 벡터DB 비교 2026](https://shaharia.com/blog/choosing-embeddable-vector-database-go-application/)
- 한국어 STT: [한국어 음성인식 벤치마크(rtzr)](https://blog.rtzr.ai/korean-speechai-benchmark/), [Awesome-Korean-Speech-Recognition](https://github.com/rtzr/Awesome-Korean-Speech-Recognition), [Clova Speech](https://www.ncloud.com/product/aiService/clovaSpeech)
- 한국어 TTS: [Supertone API](https://www.supertone.ai/en/api), [Supertonic 온디바이스 TTS](https://github.com/supertone-inc/supertonic), [한국어 TTS 서비스 비교(Typecast)](https://typecast.ai/kr/learn/tts-recommendation-korea/)
