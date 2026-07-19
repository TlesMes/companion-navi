# companion-navi ("Hey! Listen!")

반려 AI 오케스트레이터 데몬. **두뇌(LLM)는 API로 빌리고, 살아있음·기억·인격은 데몬이 만든다.**
집 안 상시 기기에서 도는 Python 데몬 + 마이크/스피커. 해자는 데몬 + 누적 기억 DB.

## 항상 지킬 것

- 인격·기억·목소리의 연속성은 100% 데몬 소유 — 벤더 종속 설계 금지 (모든 외부 API는 어댑터 뒤로)
- "언제 말할까"는 결정론적 규칙, "무엇을 말할까"만 모델 — 수면/DND 게이트는 절대 LLM에 맡기지 않음
- 사용자 오버라이드("더 잘래")는 항상 자동 판단을 이김
- 전 구간 스트리밍, 첫 오디오 ~1초 목표
- **한국어 퍼스트** — 음성 부품 선정은 한국어 품질이 1순위, 한국어만 지원해도 무방
- 결정 사항은 D번호로 추적, 결정 근거는 문서·커밋에 기록

## 문서 맵 (필요할 때 참조)

| 문서 | 내용 | 언제 읽나 |
| :-- | :-- | :-- |
| [docs/progress.md](./docs/progress.md) | Phase별 진행 기록·구현 중 결정·운영 메모(환경 상태) | **새 세션 시작 시 맥락 복원** |
| [docs/checklist.md](./docs/checklist.md) | 남은 작업만 추린 실행 체크리스트(A 실기 잔여 / B Phase 3 본류 / C D8 동결 / D 백로그) | **다음에 뭘 할지 고를 때** |
| [docs/design/plan.md](./docs/design/plan.md) | 로드맵(Phase 0–5)·원칙 전체·기술 스택과 선정 이유 | **전체 진행 상황·방향을 파악해야 할 때** |
| [docs/design/architecture.md](./docs/design/architecture.md) | 모듈 인터페이스(계약)·데이터 모델·모드 상태머신·보류 결정 D1~D16 | 구현 작업 시 |
| [docs/design/aliveness.md](./docs/design/aliveness.md) | 동적 거동 — 턴 메타데이터(D14)·능동성 3층·상태 진화(친밀도·기억) | 능동성·기억·감정 구현 시 |
| [docs/design/vendor_cost.md](./docs/design/vendor_cost.md) | 벤더 가격 비교·원가 시뮬레이션·안전 규제 조사 | 벤더 결정·원가 검토 시 |
| [docs/design/gui.md](./docs/design/gui.md) | 최소 GUI 설계·구현 계획(PR 3분할)·목업([gui_mockup.html](./docs/design/gui_mockup.html)) | Stage 15 GUI 구현 시 |
| [docs/research/tts_pivot.md](./docs/research/tts_pivot.md) | Supertonic→Zero-shot 보이스 클로닝 전환 및 아키텍처 검토 | D3 배경 파악 시 |

## 커밋 컨벤션 (Conventional Commits)

```
type(scope): 제목 (한국어, 50자 내)

본문: 무엇을 했는지보다 왜 했는지. 결정 사항은 D번호 참조.
```

- **type:** `feat`(기능) `fix`(버그) `docs`(문서) `refactor` `test` `chore`(설정·잡일) `research`(조사·평가·벤치마크)
- **scope(선택):** 모듈명 소문자 — `ear` `turntaking` `stt` `heartbeat` `memory` `conductor` `brain` `mouth` `schedule` `core` / 문서는 `plan` `arch` `cost`
- 예: `docs(arch): 턴테이킹 모듈 계약 추가 (D4 결정 반영)`, `feat(memory): 단기기억 SQLite 적재 구현`

## PR 규칙 (하이브리드 — 2026.06.13 합의, 크기 기준 2026.07.09 개정)

- **PR 1개 = 독립적으로 검증 가능한 작업 한 덩어리** — 모듈 1개 / D번호 결정 1개 / 거동을 바꾸는 튜닝 1건
- **PR 필수:** `feat` `fix` `refactor` `research`(결정 반영) / **main 직커밋 허용:** `docs` `chore` 단독 변경
- 크기: 커밋 1~6개. 줄 수 제한 없음 — 쪼개는 기준은 **검증 단위**. 독립 검증 가능한 덩어리가 2개 이상이면 쪼갠다 (기존 "~300줄" 기준은 13개 PR 전수가 초과·무해해 폐기)
- 머지 조건: 테스트 green + PR 본문에 이 단위의 **검증 방법**과 관련 D번호·완료 기준
- 브랜치명: `type/scope-요지` (예: `feat/ear-vad`, `research/d3-tts`)
- **PR 본문 섹션 헤딩:** `요약` `내용` `검증` `배경` `관련 결정` `다음 작업` 중 필요한 것만 사용. 없는 경우 같은 톤으로 명사형 제목 만들어도 무방. 구어체("무엇을" 등) 금지.

## 현재 상태 (2026.07.19)

**E 묶음(데몬 기동 정상화) — E1·E2·E4·E3 머지 완료, E6·E7·E5만 남음.** 카드가 가리키는 목소리
자산(ckpt·레퍼런스 wav)이 없거나 이 세션 엔진과 안 맞는 페르소나를 **데몬이 422로 막고** GUI가
회색+사유 툴팁으로 미리 보여준다([navi/control/runtime.py](./navi/control/runtime.py) `availability()` —
조회와 실행이 같은 판정, GUI 회색은 안내고 방어는 데몬 소유). 자산 검사는 전부 **상태 변경 이전**에
둬서 "실패하면 아무것도 안 바뀜"이 성립한다([navi/persona/voice.py](./navi/persona/voice.py)
`missing_assets()`가 단일 판정). 상세 → progress.md "E 묶음". **A5(툴팁·회색 실기 확인) 미결.**


Phase 0·1 완료, **Phase 2(음성화) 배선 완결 — 속도만 D8(GPU) 대기로 동결**, **Phase 3(능동성) 진행 중 — 순서 4까지 구현 완료(Stage 13 데몬화 + Stage 14 모드 상태머신 + Stage 15 최소 GUI + Heartbeat 2·3층 배선) + **2층 hazard 교체·A 실기 검증 완료**. 다음 = 순서 5(감정 태그→레퍼런스 전환) + D13 관심사 피드(선제 발화 E2E의 선행).** 데몬: **표준 실행은 [scripts/run_navi.ps1](./scripts/run_navi.ps1)**(인자·venv는 그 스크립트 주석이 권위) — 이벤트 버스([navi/bus.py](./navi/bus.py)) + 데몬 코어([navi/daemon.py](./navi/daemon.py)), 종료는 `stop` 서브커맨드/Ctrl+C/POST /shutdown. 능동축: SLEEP/ACTIVE/DND/SNOOZE 상태머신([navi/heartbeat/mode.py](./navi/heartbeat/mode.py)) — 검문②=`can_speak_now`(취침창 23:00~07:00 config 기본값), 음성 명령은 검문① 확장([navi/gatekeeper.py](./navi/gatekeeper.py)), `mode_state` 영속화. **Heartbeat 2·3층(순서 4, PR #19):** 나비가 먼저 말 건다 — DaemonCore tick이 게이트 순서(ACTIVE→daily_cap→`should_initiate` [navi/heartbeat/timing.py](./navi/heartbeat/timing.py))를 통과하면 `pick_topic`([navi/heartbeat/topic.py](./navi/heartbeat/topic.py))→conductor→brain→mouth 발화 + `interaction_log` 기록(initiated/responded/ignored/overrode). **배선(scaffolding)이지 똑똑한 타이밍 아님** — 값은 config 대충값, 좋은 값은 로그 축적 뒤 튜닝(진행 원칙 2). **2층 산식은 tick 기반 hazard로 교체 완료**(2026.07.18, `refactor/timing-hazard`) — 구 "가중치+jitter"가 반복 샘플링으로 산포 붕괴(±20%→±4%, tick 빈도가 발화 성격을 바꿈)해 Weibull hazard로 교체, tick 빈도 중립·경과할수록 확률↑. 계약(bool)·게이트·로깅 무수정. Stage 15는 PR 3분할([docs/design/gui.md](./docs/design/gui.md)) — **PR ①·②·③ 완료**: ① 컨트롤 플레인(STAGE 계측 + HTTP/WS 서버 [navi/control/server.py](./navi/control/server.py), config `control:` 포트 8765), ② 페르소나·톤 런타임 교체([navi/control/runtime.py](./navi/control/runtime.py) SwapRuntime 파사드 + `/personas`·`/persona`·`/voices`·`/voice`) — **페르소나=카드+음색+톤 번들**(톤은 persona yaml `voice:` 섹션 소유 [navi/persona/voice.py](./navi/persona/voice.py)), **음색 가중치 핫스왑 구현**(2026.07.16 — ckpt 불일치 시 `set_weights`로 런타임 교체, 모델 로드는 to_thread + 턴 락으로 발화와 상호배제. 엔진 핫스왑은 여전히 안 함 — 같은 엔진 내 가중치 교체일 뿐. **실기 청취·전환 시간 검증 완료 2026.07.18 — 교체 0.96~1.65s**), ③ GUI 앱(`python -m navi.gui` — pywebview 창 + 단일 파일 프런트 [navi/gui/static/index.html](./navi/gui/static/index.html), 서빙은 컨트롤 플레인 `GET /`) + 다크/라이트 테마 토글(라이트=Claude 데스크톱 톤, localStorage 저장, PR #18). **A 실기 검증 완료(2026.07.18, 커밋 `a80b719`)** — gui.md PR③ 4항목 중 ①5노드 점등 ②MODE_CHANGED 라이브 ③GUI kill 무영향 통과, ④취침창 런타임 변경은 재기동 시 config 복귀(영속화는 데몬 소유 아님 — gui.md:121 결정의 실증, GUI 창 영구화는 후속). 음색 핫스왑 실측 0.96~1.65s → GUI 로딩 표시는 백로그 강등. **이 마이크는 `--vad-threshold 50` 필요**(기본 150은 발화가 STT로 안 넘어감). 남은 것: **선제 발화 E2E는 보류** — 3층 `pick_topic`이 `topic_feed`(D13) 없이 고정 힌트만 반환해 LLM 요청문이 플레이스홀더라 검증 실질이 없다(D13 구현 후 재개). 상세 → [docs/progress.md](./docs/progress.md) "A 실기 검증 세션".
Phase 1 산출물: Conductor + Brain 어댑터(Gemini 기본·Anthropic·Echo) + 단기기억(SQLite) + 캐릭터 카드([personas/navi.yaml](./personas/navi.yaml)) — CLI 텍스트 대화(`python -m navi.cli`).
D3(TTS 음색): **GPT-SoVITS fine-tune 확정.** 음색=가중치 안정, 톤=레퍼런스 제어. 어댑터: [navi/mouth/gptsovits.py](./navi/mouth/gptsovits.py).
음성 배선: **타이핑/마이크 → 나비 음성 답변 실동.** TurnPipeline([navi/pipeline.py](./navi/pipeline.py)) `--voice` + Ear 마이크 입력([navi/ear/](./navi/ear/)) `--listen`(PR #8). STT는 faster-whisper(`--input` 파일 / `--listen` 마이크).
검문①: **완료** — STT 출력을 LLM 전에 가로채 수면 명령 결정론 처리([navi/gatekeeper.py](./navi/gatekeeper.py), PR #9).
**D7(웨이크워드): 확정.** 엔진=**openWakeWord**(Vosk 폐기 — ASR 스팟팅은 진짜 KWS 아님, 피벗 경위 progress.md Stage 6·7). 한국어 "나비야" 모델은 livekit-wakeword(VoxCPM2 합성)로 학습 → `secrets/navi_ko.onnx`(conv_attention, 다리 없이 기존 어댑터 그대로 로드) → **원어민 마이크 실측 통과**([navi/ear/wakeword.py](./navi/ear/wakeword.py)). 임계값 튜닝은 후순위.
두뇌: **Claude Haiku 4.5 실호출 검증 완료(Stage 12)** — TTFT 0.7~1.3s 안정, `--brain anthropic`으로 런타임 전환. D1은 보류 유지(검증된 대안 확보). E2E 실측 9.8s 중 **TTS(CPU)가 63% — 속도는 D8(GPU) 확보 시 재개.** 스트리밍 STT(D2)·AEC도 속도 트랙과 묶어 보류. 로드맵 현황·Phase 3 착수 순서 상세 → [docs/progress.md](./docs/progress.md) 상단 스냅샷.