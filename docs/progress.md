# 진행 기록 (Progress Log)

> 새 세션·새 작업자가 맥락을 복원하는 진입점. 최신 Phase가 위.
> 한 줄 요지는 [CLAUDE.md 현재 상태](../CLAUDE.md), 상세 근거는 각 커밋 본문에 있다.

---

## Phase 3 — 능동성 (진행 중)

**Stage 15-③ — pywebview GUI 앱 (2026.07.11, `feat/gui-app`):**
- **GUI 프로세스([navi/gui/__main__.py](../navi/gui/__main__.py)):** `python -m navi.gui` —
  pywebview frameless 창(360×420 고정, Edge WebView2)이 컨트롤 플레인 `GET /`를 로드. 데몬 미기동이면
  대기 화면을 띄우고 1초 폴링으로 자동 진입. 헤더 ✕는 js_api로 네이티브 창 닫기.
  의존성 `pywebview`는 기본 venv(음성 스택 불필요 — GUI는 오디오를 만지지 않는다).
- **단일 파일 프런트([navi/gui/static/index.html](../navi/gui/static/index.html)):** 목업 v3
  구현 — 5노드 파이프라인 점등(STAGE 구독, 대기 중 귀 숨쉬기·청취축 SLEEP 소등), 능동축 필 +
  오버라이드 버튼 4개(DND 중엔 해제로 토글), 취침창 24h 스트립(자정 넘김 양끝 음영 + 현재
  마커 + 클릭 인라인 편집), 페르소나·톤 바텀시트, 로그 다이얼로그. WS 자동 재연결(1.5s) —
  링버퍼 백필은 로그로만 넣고 점등은 최근 3초 이벤트만(재접속 시 과거 재생 방지). **외부 CDN
  0** — 목업의 tabler 웹폰트 대신 인라인 SVG 아이콘(오프라인·pywebview 무의존).
- **컨트롤 플레인 GUI 표면 4건(서버 소폭 확장):** ① `GET /` 프런트 정적 서빙(같은 오리진 —
  CORS 없음) ② `/status`에 `sleep_window`(스트립 초기 렌더 재료 — ModeMachine.window 신설)
  ③ WS 이벤트에 `wall_ts`(ts는 monotonic이라 GUI가 시각으로 못 읽음 — 직렬화 시점 두 시계
  차로 근사) ④ `GET /voices/{name}/audio` 톤 시청취(재생은 GUI `<audio>` — 데몬 스피커 미사용,
  `SwapRuntime.tone_file`이 파일 아닌 voice_id(프리셋명)는 404).
- **결정 — 취침창 변경은 런타임 전용(gui.md 검증 ④):** 데몬이 config.yaml을 쓰지 않는다 —
  영구 변경은 config 수동 편집. GUI 토스트에 "이번 실행 동안 유지"로 명시.
- **구현 후 UI 다듬기(실사용 피드백 반영):** ① **frameless 창** — OS 제목줄이 드러나 앱
  헤더를 제목줄로 승격(헤더 전체 드래그 영역, 최소화 버튼, 창 360×420로 축소). 이 과정에서
  창 멈춤 버그 해소: js_api에 창 객체를 공개 속성으로 두면 pywebview가 브리지 직렬화 중
  `Window.native`를 무한 재귀로 훑어 메인 스레드가 멎음 → 비공개 `_window`로 전환.
  ② **취침창 두 핸들 드래그** — 빈 스트립에 범위를 그리는 방식은 인지가 어렵고 오조작에
  취약(어디를 끌어도 창 통째 교체) → 취침·기상 경계에 상시 핸들 2개, 경계만 개별 이동
  (30분 스냅·실시간 미리보기, 빈 영역 드래그 무동작). ③ **페르소나 전환을 헤더 프로필
  드롭다운으로** — 중단 전환 필이 헤더와 중복이라 좌상단 프로필을 인라인 드롭다운 트리거로,
  중단은 톤 칩 + 자세히(시청취) 시트로 축소. ④ **앱 액센트 = Claude 코랄/러스트(#d97757)** —
  블루가 웜 다크 표면과 온도 어긋남, 스누즈 필 파랑은 4모드 색 구분 위해 전용 변수로 보존.
- **결정 — Ctrl+C graceful 종료([navi/daemon.py](../navi/daemon.py)):** 기본 SIGINT는 런너가
  전 태스크를 일괄 취소해 uvicorn WS·lifespan이 CancelledError 소음(ERROR 2건)을 남김 →
  SIGINT·SIGBREAK를 가로채 stop·POST /shutdown과 같은 SHUTDOWN 경로로 합류(두 번째 Ctrl+C는
  기본 핸들러 복원). CTRL_BREAK 실측 — 출력 2줄, traceback 없음, exit 0.
- **검증:** 유닛 192개 green(+5: sleep_window·wall_ts·GET /·시청취). 오프라인 E2E(echo 데몬 +
  브라우저 실측): 모드 버튼 전이가 MODE_CHANGED로 라이브 반영·페르소나 드롭다운 교체
  (navi↔example) 헤더 즉시 반영·취침창 핸들 드래그 즉시 전이·데몬 종료→"연결 끊김"→재기동
  자동 재연결·STAGE 전 단계 점등·frameless 창/최소화/드래그·코랄 테마 computed style 확인.
  **실기 E2E(--voice --wakeword + gptsovits base zero-shot ~250MB 다운로드 포함)는 계획대로
  Stage 15 전체를 한 번에** — gui.md PR ③ 검증 절.

**Stage 15-② — 페르소나·톤 런타임 교체 (2026.07.11, `feat/mouth-voice-swap`):**
- **페르소나-음색 번들 결정(2026.07.10, 원안 개정):** 페르소나 = 카드(성격) + 음색(fine-tune
  가중치) + 톤 목록(그 음색의 레퍼런스 wav). 톤 레퍼런스는 해당 fine-tune 화자의 녹음이라
  다른 가중치에 교차 적용하면 목소리 연속성 원칙 위반 → 전역 config `mouth.tones:` 안을 폐기하고
  **persona yaml `voice:` 섹션**([navi/persona/voice.py](../navi/persona/voice.py))이 소유. 런타임
  독립 축은 "현재 페르소나 안의 톤 선택"뿐, `/persona`가 번들 교체 진입점.
- **`VoiceProfile.ref_text` 신설:** 레퍼런스 wav는 이미 매 턴 인자인데 전사만 gptsovits 생성자에
  박혀 톤 교체가 막혔음 — 전사를 VoiceProfile로 올려 wav와 한 쌍으로(빈값이면 생성자 폴백,
  하위호환). `TurnPipeline.set_voice`는 다음 턴부터 적용, 재생 중 거부(409)는 컨트롤 플레인 몫.
- **SwapRuntime 파사드([navi/control/runtime.py](../navi/control/runtime.py)):** DaemonCore는
  conductor·pipeline을 안 들어(전부 `_run` 지역변수) 교체 명령이 닿을 손잡이가 없음 — 생성자
  비대화 대신 파사드가 손잡이를 모아 들고 `create_app(swap=)` 옵셔널 주입(미주입 503 → PR ①
  테스트 무수정 보존). **persona_id(카드 주인)/voice_persona_id(톤 세트 주인) 분리 추적** —
  가중치 불일치 교체는 카드만 바꾸고 톤 세트 유지(`voice_swapped: false`), 후속 가중치 교체
  PR이 이 분열을 해소. 부팅도 번들 우선(카드 voice 섹션이 mouth options·초기 톤을 덮음).
- **경로 해석 제약:** gptsovits 웜업이 `os.chdir(repo)`를 해 이후 CWD가 리포 루트가 아님 →
  persona yaml 상대경로는 `load(root=)`가 파싱 시점에 절대화. 기준 root를 `Config.root`로 신설.
- **API:** `GET/POST /personas·/persona·/voices·/voice`. /persona도 재생 중 409(voice 교체 내포).
  판정은 파사드, server는 HTTP 번역만(LookupError→404·SwapBusy→409·RuntimeError→503).
- **공개 예시 카드([personas/example.yaml](../personas/example.yaml)):** aris.yaml은 저작권·
  gitignore(로컬 전용)라 오프라인 E2E·테스트를 커밋 자산으로 재현 불가 → fine-tune ckpt 없이
  **gptsovits base(zero-shot)**로 도는 중립 예시 카드를 공개 자산으로(`!personas/example.yaml`).
  navi↔example 전환이 커밋 자산만으로 재현된다. 어댑터는 이미 base zero-shot 지원(ckpt 옵셔널 →
  inference_webui가 base 폴백, 확인함). **위상 못박음: base zero-shot은 GUI 전환 시연 전용 데모
  자산 — 제품 음성 경로가 아니다.** 제품 정체성은 fine-tune 음색(연속성)이라 실사용자 배포엔
  base도 example 카드도 불필요, base 다운로드는 오직 우리가 GUI 전환을 실물로 검증하기 위한 것. **base s1/s2 실물 다운로드(~250MB) + zero-shot 발화 +
  부팅 base-선택 로직(카드 ckpt 부재 시 config arisu 폴백 충돌 해소)은 PR ③ GUI 검증에 포함**
  (실기 자원 공유·GUI 버튼으로 검증이 편함 — 2026.07.11 합의).
- **검증:** 유닛 187개 green(persona·pipeline·control 신규, aris.yaml gitignore라 테스트는
  navi·example 공개 카드 + tmp 인라인만). 오프라인 E2E(echo+fake): curl로 /personas 스캔→
  /persona 교체(navi↔example, character 전환·voice_swapped false)→404→원복→/shutdown, 로그
  "카드 교체" 실측. 실기 E2E는 PR ③ 이후 Stage 15 전체를 한 번에.

**Stage 15-① — 컨트롤 플레인: STAGE 계측 + HTTP/WS 서버 (2026.07.10, `feat/core-control-plane`):**
- **STAGE 계측([navi/bus.py](../navi/bus.py)·[navi/pipeline.py](../navi/pipeline.py)):** `EventKind.STAGE`
  payload=(stage, phase, detail) — stt·gate는 데몬이 직접 발행, brain 첫 토큰(TTFT)·tts
  진입/종료는 TurnPipeline `on_stage` 콜백으로(파이프라인은 버스를 모름 — 데몬이 연결).
  부수 이득: Stage 12 수동 측정하던 구간별 지연이 상시 기록 → D8(GPU) 재측정 재료.
- **컨트롤 플레인([navi/control/server.py](../navi/control/server.py)):** FastAPI+uvicorn을 데몬
  이벤트 루프의 태스크로(gui.md 아키텍처 그대로). `/status`·`/mode/{cmd}`·`/mode/window`·
  `/shutdown`·`WS /events`(링버퍼 50 백필 → 라이브). GUI 버튼은 음성 명령(검문①)과 같은
  `DaemonCore.command_mode()` 단일 진입점 — 결정론 게이트는 데몬 소유. config `control:`
  (enabled·port 8765), 바인딩 127.0.0.1 고정, 서버 태스크 예외는 데몬이 삼킨다.
- **구현 중 결정 2건:** ① 엔드포인트 전부 async def — FastAPI가 동기 def를 스레드풀로 돌려
  SQLite 영속화의 단일 스레드 규약이 깨지는 것(500)을 E2E에서 실측, 루프 실행으로 해소.
  ② uvicorn 시그널 가로채기 무력화(`_DaemonServer`) — Ctrl+C는 데몬 소유,
  install_signal_handlers·capture_signals 양쪽 오버라이드.
- **검증:** 유닛 164개 green(control 10·pipeline 2 신규, TestClient — 서버·마이크·키 불필요).
  오프라인 E2E(echo 두뇌): curl로 snooze→dnd→취침창 변경(즉시 sleep 반영)→`mode_state`
  영속화·MODE_CHANGED 콘솔 수신→POST /shutdown 종료(exit 0·pid 정리) 실측.

**Stage 15 준비 — 최소 GUI 설계 확정 (2026.07.10, 구현 착수 전):**
- **상세 설계·구현 계획·확정 목업: [docs/design/gui.md](./design/gui.md) +
  [gui_mockup.html](./design/gui_mockup.html)** — 아래는 요지.
- **스택:** 컨트롤 플레인 = FastAPI+uvicorn을 **데몬 프로세스 안 asyncio 태스크**로(버스가
  프로세스 내 pub/sub이라 서버는 데몬에 얹는 게 유일한 구독 경로 — "느린 구독자 격리"는 버스
  층이 이미 보장). GUI = **pywebview 별도 프로세스**(`python -m navi.gui`, Edge WebView2 네이티브
  창 약 360폭)가 localhost를 로드. 프런트는 정적 HTML/JS 단일 파일 — Node 툴체인·빌드 스텝 0,
  추후 Tauri 래핑 경로 보존. 바인딩 127.0.0.1 고정.
- **API 표면:** `GET /status`(DaemonState.snapshot) · `POST /mode/{cmd}`(ModeMachine.command —
  wake/snooze/dnd/dnd_clear/sleep) · `PUT /mode/window`(set_window) · `POST /shutdown`(센티널
  파일 방식 대체) · `WS /events`(버스 "gui" 구독, 접속 시 링버퍼 50개 초기 채움) ·
  `POST /persona`(카드 reload — Conductor 카드 교체) · `POST /voice`(톤=레퍼런스 VoiceProfile 교체).
  노드별 점등을 위해 **TurnPipeline에 STAGE 이벤트 계측**(stt/gate/brain/tts 시작·완료 — 부수
  이득: Stage 12 수동 측정하던 구간별 지연이 로그에 상시 기록, D8 재측정 재료).
- **UI 확정(목업 v3):** ① 5노드 파이프라인(귀→받아쓰기→검문→두뇌→목소리) — 현재 단계만
  은은히 점멸, 대기 중엔 귀 노드만 숨쉬기, 청취축 SLEEP이면 귀도 소등(상태 카드 대체).
  ② 능동축은 헤더 필 1개(ACTIVE=초록/SLEEP=회색/DND=주황/SNOOZE=파랑) + 오버라이드 버튼
  4개(기상·스누즈·DND·재우기). ③ 이벤트 원문은 로그 다이얼로그에서만(메인 창은 캡션 한 줄).
  ④ 취침창은 24시간 스트립 시각화(자정 넘김 양끝 음영 + 현재 시각 마커), 클릭 인라인 편집.
  ⑤ 페르소나 = 카드 그리드(personas/*.yaml 아바타 칩), 톤 = 레퍼런스별 아이콘 칩(미니 파형 +
  시청취). **톤 칩의 최종형은 표시 UI** — Phase 3-5(감정 태그) 후 두뇌 선택을 따라 자동 점등,
  클릭은 핀(고정) 오버라이드("사용자 오버라이드 > 자동 판단" 원칙). 이번 PR에선 수동 선택으로 시작.
- **런타임 교체 실사 결과(2026.07.10):** 페르소나 reload(유예 불필요)·톤 교체(매 턴 인자, 즉시)·
  음색 가중치 교체(change_*_weights 재호출 — 재생 중 금지+로딩 중 턴 차단 유예 필요) 모두 가능.
  ref_text가 어댑터 생성자에 박혀 있어 VoiceProfile로 옮기는 소규모 리팩터 동반. 엔진 핫스왑만
  불가(아래 백로그).
- **PR 분할(3개):** ① `feat/core-control-plane`(STAGE 계측 + HTTP/WS 서버) →
  ② `feat/mouth-voice-swap`(페르소나·톤 런타임 교체 — VoiceProfile.ref_text 리팩터 포함) →
  ③ `feat/gui-app`(pywebview 창 + 프런트). 각 PR의 범위·검증 방법은 gui.md에 확정.

**Stage 14 — 모드 상태머신 + 검문②(능동축, arch 5장) (2026.07.09, `feat/heartbeat-mode`):**
- **능동축 상태머신([navi/heartbeat/mode.py](../navi/heartbeat/mode.py)):** SLEEP/ACTIVE/DND/SNOOZE
  — "나비가 먼저 말해도 되는가"를 결정론 규칙으로 판정(LLM 개입 0). `can_speak_now`(ACTIVE만
  True)가 **검문②의 본체** — Heartbeat 선제 발화(4단계)의 유일한 출입문. 청취축과 직교(D16):
  어떤 모드에서도 웨이크워드 호출·응답은 동일 동작, 금지는 능동 발화뿐.
- **수치·전이:** 취침창 23:00~07:00(config `mode:` 기본값, 자정 넘김 지원 — GUI에서 런타임
  변경 예정, `set_window`) · 스누즈 30분(재발화 연장) · DND는 명시 해제만(강제기상 안 통함).
  우선순위: 수동 SLEEP > 취침창 > SNOOZE > DND > ACTIVE. 강제기상은 이번 창에 한해 창을
  이김(사용자 오버라이드 > 자동 판단). 취침창 *진입*이 낮의 DND/SNOOZE를 소거(자고 나면
  리셋), 밤중에 내린 DND는 진입 이후라 아침까지 생존.
- **검문① 확장([navi/gatekeeper.py](../navi/gatekeeper.py)):** GateResult에 WAKE/SNOOZE/
  DND/DND_CLEAR 추가 — 구절 집합을 dict로 일반화(전체 일치·긴 구절 원칙 유지). cli는 신규
  명령을 "데몬 전용" 안내로 차단(LLM 미경유). `ModeMachine.command()`는 호출자 불문 —
  GUI 버튼(3단계)이 같은 API를 쓴다.
- **영속화:** `mode_state` 테이블(arch 6) + `MemoryStore.get/set_mode_state` — 재기동해도
  오버라이드 생존. 저장은 (mode, override_until) 근원만 — 창SLEEP은 시계에서 파생이라 저장
  안 함(겉모드는 ModeMachine이 시계와 합성). 명령 경로는 겉모드 무변화여도 강제 저장
  (창 안 스누즈 코너).
- **데몬 배선:** TICK 구독이 시간 전이(창 진입·만료)를 굴리고, `EventKind.MODE_CHANGED`
  발행 + `DaemonState.proactive_mode`/`can_speak` 스냅샷 노출(3단계 GUI GET /status 재료).
  수면 명령은 두 축을 함께 재움(청취축 세션 종료 + 능동축 SLEEP).
- **검증:** 유닛 152개 green(mode 20·게이트/데몬/메모리 확장). 오프라인 E2E(echo 두뇌):
  취침창을 실시각 ±분으로 잡아 ACTIVE→SLEEP→ACTIVE 창 전이·MODE_CHANGED·영속화 실측.
- **남음(후순위, 2026.07.09 합의):** 음성 명령 구절의 STT 실측은 보류 — 오인식이 잦으면
  구절 튜닝 대신 다른 전이 경로를 검토. 1순위 대안은 GUI 오버라이드 버튼(순서 3):
  `command()`가 호출자 불문이라 STT를 완전히 우회하는 결정론 경로가 이미 설계에 있다.

**Stage 13 — 데몬화(Daemon Core, arch 4.11) (2026.07.08, `feat/core-daemon`):**
- **이벤트 버스([navi/bus.py](../navi/bus.py)):** 프로세스 안 경량 pub/sub — 구독자별 유한
  `asyncio.Queue`, publish는 논블로킹(포화 시 가장 오래된 것 drop, 최신 우선). **발행자는 절대
  대기하지 않는다** — 느린 구독자(향후 GUI WS)가 마이크→STT→TTS 핫패스를 못 막는 핵심 보장.
  `EventKind` = WAKE/UTTERANCE/SLEEP(청취축 유래) + TICK/TURN_STARTED/TURN_ENDED/SHUTDOWN.
- **데몬([navi/daemon.py](../navi/daemon.py)):** `python -m navi.daemon [--voice --wakeword ...]`
  상주 프로세스. 발행자(ear_task=ListenSession 이벤트 감싸기 · tick_task=순수 시계 · stop_watcher)
  + 구독자(dispatcher: STT→검문①→TurnPipeline / console: 상태 안내 — 독립 구독 시연).
  `DaemonState` 스냅샷(listening_mode·turns_count·이벤트 링버퍼)이 3단계 GUI `GET /status`의 원천.
  기능은 기존 `--listen --wakeword --voice`와 동일 — cli.py는 개발 도구로 보존, 배선은 미러
  (공통화 리팩터링은 중복이 아플 때 다음 PR).
- **생명주기(Windows, systemd 없음):** `logs/navi.pid` 단일 인스턴스 가드(중복 기동 거부),
  종료는 Ctrl+C 또는 `python -m navi.daemon stop` — **센티널 파일**(`logs/navi.stop`) 방식
  (Windows 프로세스 간 시그널 불안정 → 3단계 HTTP 컨트롤 플레인 생기면 POST /shutdown으로 대체).
- **구현 중 결정 2건:** ① PID 생존 확인에 `os.kill(pid, 0)` 금지 — Windows에선 시그널 0도
  TerminateProcess를 불러 대상 프로세스를 죽인다 → ctypes `OpenProcess`+`GetExitCodeProcess`로
  구현. ② stdout/stderr `errors="replace"` 재구성 — 출력 리다이렉트 시 cp949 인코딩으로
  em dash(—)에서 데몬이 죽는 것을 실측, 인코딩 불가 문자는 `?`로 대체(콘솔 인코딩은 유지).
- **검증:** 유닛 112개 green(버스 5·데몬 7 신규, 마이크·키 불필요 — FakeWakeWord+프레임 큐 주입으로
  WAKE→턴→수면명령→SLEEP 전 사이클 결정론 재현). 오프라인 E2E(echo 두뇌): 기동→TICK 누적→중복
  기동 거부→stop 종료→pid/stop 파일 정리 확인. **실기 E2E(`--voice --wakeword` 마이크 대화)는
  사용자 확인 대기.**

**로드맵 현황 스냅샷 (2026-07-08):**
전체 6단계 중 Phase 0(기획·설계)·Phase 1(텍스트 뼈대) 완료, **Phase 2(음성화) 배선 완결 — 속도만 하드웨어 대기**, **Phase 3(능동성)으로 전환**. Phase 4(기억·인격)·5(완성) 대기.

- **Phase 2 완료:** D3 음색(GPT-SoVITS) · Brain→Mouth 배선 · STT 파일 입력(`--input`) · 마이크 Ear 입력(`--listen`, PR #8) · **검문①(PR #9)** · **D7 웨이크워드(실측 통과)** · **Claude 브레인 실호출 검증(Stage 12)**
- **Phase 2 동결(하드웨어 대기):** 속도 — E2E 9.8s 중 TTS 합성이 63%(Stage 12 실측). D8(GPU) 없인 "명령 답변 ≤3s" 구조적 불가 → GPU 확보 시 재개. D2(스트리밍 STT)·AEC도 속도 트랙과 묶어 보류.
- **결정 현황(D번호):**
  - ✅ 확정: D3(TTS=GPT-SoVITS) · D4(실시간 음성 API 배제) · **D7(호출어="나비야", 엔진=openWakeWord, 한국어 모델=livekit-wakeword+VoxCPM2, 원어민 실측 통과)** · D15(VAD 1층 캐스케이드) · D16(모드 두 직교 축)
  - ◐ 진행: D5(SQLite) · D6(sqlite-vec) · D8(하드웨어 — 속도 재개의 열쇠)
  - · 보류: D1(LLM — Claude Haiku 검증 완료, `--brain` 전환 가능) · D2(STT 벤더) · D9(친밀도) · D10(안전) · D11(스케줄) · D12(턴테이킹 튠) · D13(피드)
- **다음 갈림길: Phase 3 착수 순서 (2026.07.08 합의)** — 목표 재정의 중 "호출 즉답 ≤0.5s"는 D7로 충족, "명령 답변 ≤3s"는 D8 대기.
  1. ✅ **데몬화(Daemon Core, arch 4.11)** — 완료(Stage 13). 이벤트 버스=경량 pub/sub 자작, 상주=콘솔+명시적 실행/종료, GUI용은 상태 스냅샷 객체까지(HTTP/WS는 3번에서).
  2. ✅ **모드 상태머신 + 결정론 게이트(arch 5장, 검문②)** — 완료(Stage 14). SLEEP/ACTIVE/DND/SNOOZE + `can_speak_now` 게이트, 음성 명령·영속화·MODE_CHANGED 배선까지. GUI 전이(같은 command API)·취침창 런타임 변경은 3번에서 소비.
  3. **최소 GUI(관찰·제어 플레인)** — 데몬과 별도 프로세스, localhost HTTP/WS 구독(모드·로그·오버라이드 버튼). 오디오 경로에 절대 불개입(GUI 죽어도 나비는 산다). FastAPI+웹 우선, 추후 Tauri 래핑 가능. D11(스케줄 빌려오기)의 "컴패니언 앱" 선택지를 겸할 수 있음. 하트비트 튜닝의 관찰 도구라 4번보다 먼저. **← 진행 중 — PR ①(컨트롤 플레인) 완료, ②(음성 교체)·③(GUI 앱) 남음. 설계는 상단 "Stage 15 준비" 참조**
  4. **Heartbeat 2·3층(arch 4.4)** — 타이밍(가중치+jitter) + 주제 도출 → 첫 선제 발화. interaction_log 수집 시작.
  5. **감정 태그→레퍼런스 전환** — Brain이 감정 태그 출력, Mouth가 감정별 레퍼런스 오디오 선택(D3 "톤=레퍼런스 제어" 활용). D9 친밀도 산식 없이 얹는 1차 감정선.
  - Schedule(D11)·Feed(D13)는 위 순서 진행 중 결정 시점 도래 시 D번호로 처리.
  - **백로그(2026.07.10):** TTS *엔진* 핫스왑(gptsovits↔타 어댑터)은 재기동 필요 — in-process
    로드가 sys.path·CWD·env를 전역 오염시켜 프로세스 내 교체 불가(엔진은 D3 확정이라 실익 낮음).
    반면 **페르소나(카드 reload)·톤(레퍼런스 오디오=VoiceProfile)·음색(가중치 change_*_weights)은
    런타임 교체 가능** — 배선만 없음. 가중치 교체는 로드 유예(재생 중 금지 + 로딩 중 턴 차단) 필요.

**Stage 12 — Claude 브레인 실호출 검증 + 병목 이동 확정 (2026.07.08):**
- **Anthropic API 결제·키 적재 → Haiku 4.5 실호출 검증.** brain 격리 TTFT 0.7~1.3s로 안정(429 없음,
  1,000rpm 하드 보장). Stage 11의 진범(무료 티어 스로틀)이 벤더 전환만으로 해소됨을 실측 확인.
  런타임 전환은 기존 `--brain anthropic` 플래그로 충분 — **D1은 계속 보류**(벤더 중립 유지), 단
  "검증된 대안 확보" 상태로 격상. config 기본값은 gemini 유지.
- **깨끗한 E2E 재측정(Claude 브레인, n=3 웜):** STT 1741ms(18%) / Brain TTFT 811ms(8%) /
  첫 문장 대기 301ms(3%) / **TTS 첫 문장 합성 6122ms(63%)** = E2E 9776ms. Stage 9의 결론이
  무료 티어 노이즈 제거 후 재확인 — **병목은 Brain이 아니라 TTS(CPU 합성)**. STT·Brain이 0이어도
  TTS 6s 단독으로 "명령 답변 ≤3s" 초과 → 잔여 속도 작업은 D8(GPU) 하나로 수렴. 속도 트랙은
  하드웨어 확보 전까지 동결하고 Phase 3으로 전환.
- **프롬프트 캐싱 모델 의존성 발견:** 캐시 최소 프리픽스가 Haiku 4.5=4096tok, Sonnet/Opus=1024tok.
  현 캐릭터 카드 ~1.6K tok → Haiku에선 조용히 no-op(어댑터 주석에 명시, Sonnet 이상으로 올리면 자동
  활성). 현 규모에선 비용 영향 미미라 추가 튜닝 불요.

**Stage 11 — 두뇌 워밍업 실측 → 폐기 (2026.07.07, `perf/turn-latency` 폐기):**
- "첫 턴 지연 = 커넥션 콜드스타트, 기동 시 최소 호출로 데우면 즉답" 가설(커밋 ac96d84)을 실측 후 폐기.
- brain 격리 실측(무료 티어): turn1이 warm 턴보다 느리지 않음 → **콜드스타트 신호 없음.** 진범은
  **무료 티어 레이트리밋(gemini-3-flash 분당 5회)** — 초과 시 429+재시도 백오프로 첫 토큰이 수십 초까지
  치솟는다. Stage 9의 TTFT 2~40s 요동도 이 스로틀이 정체(커넥션 아님).
- 워밍업은 5회 쿼터를 1회 갉아먹어 오히려 역효과. **첫 턴 지연의 진짜 해법은 D1(유료 티어/벤더).**

**Stage 10 — D7 한국어 "나비야" 모델 본학습 + 원어민 실측 통과 (2026.07.06):**
- **본학습 실행(Colab T4, livekit-wakeword):** 양성 2000+검증 200 · adversarial negative 2000+200 ·
  background 200+40(VoxCPM2 합성 + free-sound 노이즈) → augment(멜스펙+임베딩 특징추출) →
  train(conv_attention/small, 50000스텝 3-phase) → export(ONNX) → eval. 전 구간 무중단 완주(중간
  Colab 무료 GPU 한도로 여러 차례 끊김 — **Google Drive 마운트로 다운로드·산출물 영속화**해 매
  세션 이어감, 상세 절차는 [notebooks/RESUME_next_session.md](../notebooks/RESUME_next_session.md)).
- **합성 eval(참고용, 과대평가 감안):** `threshold=0.5`에서 recall 69.5%·FPPH 0.06,
  `optimal_threshold=0.25`에서 recall 80%·FPPH 0.18(negative 30324개·16.85시간 기준). VoxCPM2
  합성판 train·검증이 같은 발음 편향(비원어민 톤)을 공유해 낙관 편향 가능성 있음 — 실측 전엔
  참고치.
- **★ 원어민 마이크 실측 — 통과.** `navi_ko.onnx`를 **다리(dnn head·tflite export) 없이** 기존
  `OpenWakeWordWakeWord` 어댑터(`inference_framework="onnx"`)에 그대로 로드해 동작 확인. d7
  문서가 우려했던 "conv_attention은 livekit 자체 런타임 전용, 우리 어댑터엔 dnn+tflite 필요"는
  **기우로 판명** — 특징 shape(16×96, openWakeWord 표준 임베딩과 동일)이 맞아 classifier onnx가
  그대로 호환됨. 신규 어댑터·재학습 불요.
- **실측 조건:** 임계값 기본 0.5(config 그대로, 튜닝은 후순위) · 원어민(개발자 본인) 발화 ·
  `scripts/try/owww_mic.py --model secrets/navi_ko.onnx`. 결과: DETECT 점수 0.51~0.70대로 다회
  반복 감지, 대기 중 최고점수 0.003~0.009대 유지(오탐 없음). CPU 3~7%대(영어 내장 모델
  hey_jarvis 실측치와 동급 — Stage 8 참조).
- **D7 확정.** 호출어="나비야", 엔진=openWakeWord, 한국어 모델=livekit-wakeword(VoxCPM2)+
  conv_attention, 어댑터=기존 `OpenWakeWordWakeWord` 그대로 재사용. 모델 파일은 `secrets/navi_ko.onnx`
  (커밋 금지 — 비밀 취급).
- **남음(후순위):** 임계값 튜닝(현재 0.5, eval optimal 0.25 참고) · 오탐률 장시간 실측(생활 소음·
  타인 발화) · your-voice 주입은 지금 실측 통과로 불필요해짐(보류 해제 없이 폐기 검토 가능).

**Stage 8 — D7 엔진 피벗(Vosk→openWakeWord) + 한국어 학습 경로 확정 (2026.06.28):**
- **엔진 피벗 — Vosk → openWakeWord:** Vosk(ASR 스팟팅)는 SLEEP에서 경량 ASR을 상시 돌리는 2단이라 "진짜 텍스트-없는 KWS"가 아니었다(Stage 7 함의). **openWakeWord = 진짜 음향 KWS**(.onnx, 계정·키·만료 0, 오프라인). 어댑터 `OpenWakeWordWakeWord`([navi/ear/wakeword.py](../navi/ear/wakeword.py)) — 16kHz·80ms(1280)→0~1 점수→임계. 커스텀 `model_path` 우선, 없으면 내장 영어(`hey_jarvis`)로 런타임 검증(모델↔런타임 분리). Vosk·Porcupine 어댑터는 계약 뒤 보존(엔진 교체=어댑터1+팩토리 한 줄). (커밋 17d9c70)
- **Track A — 런타임 sanity check(영어 내장 모델, 통과):**
  - 감지 ✓ — `hey_jarvis` 마이크 E2E 실동(`scripts/try/owww_mic.py`).
  - **CPU 정체 규명(실측):** SLEEP 대기 ~4%/코어(≈시스템 0.4%) = openWakeWord predict. 분해 → **preprocessor(멜스펙+임베딩) 2.6ms가 대부분**, 분류기 ~0ms(공짜), 나머지 ~1% 파이썬 루프. 호출어 여러 개 등록해도 임베딩 공유라 거의 안 비싸짐. 콘센트 PC엔 무시 가능 → 수용.
  - **VAD 정정(커밋 6f3b39f):** `vad_threshold`를 "침묵 추론 스킵→idle CPU↓"로 적은 건 **소스 오독**. openWakeWord는 매 프레임 풀 추론 *뒤* VAD로 결과만 0으로 거름 — 연산 절감 0, 오히려 Silero가 ~1% 더 씀(3.2→3.9ms). 기능은 **오탐 억제**. 기본 0으로 끔. idle CPU 진짜 레버는 EnergyVad 앞단 게이트(D15)뿐 → 콘센트 PC엔 실익 미미, D8(배터리)까지 보류.
- **Track B — 한국어 "나비야" 학습 경로 확정** (상세 → [research/d7_wakeword_ko.md](./research/d7_wakeword_ko.md)):
  - **호출어 = "나비야" 확정**(페르소나 이름, 3음절 — 오탐 잦으면 임계↑로 대응).
  - **호스팅(openwakeword.com) 폐기:** 한국어 체크포인트 부재(영어 Lessac 교차언어 임시방편) + 목소리 모델 하나 23,317크레딧(보유 0)·~7.8h. 영어 뿌리라 한국어 품질도 타협.
  - **채택: livekit-wakeword**(Apache 2.0) — VoxCPM2로 진짜 한국어 다화자 합성. 학습 엔진은 openWakeWord와 동일, **dnn head + tflite export로 우리 어댑터에 직결**(남은 글루: 어댑터 `inference_framework` 인자화 1줄 + config `model_path`). 공식 노트북은 영어 전용(automatic) / export·TTS 미배선(manual)이라 탈락.
  - **함의:** 웨이크워드는 화자-독립 → **개발자가 한 번 구워 배포, 사용자 학습 0**. your-voice는 내 인스턴스 선택 부스트(후순위, livekit 미문서화→수동 노트북 폴백). 한국어 정확도는 영어 임베딩 편향 상속 리스크 → **실측 판정**.
- **남음:** Colab에서 livekit `run`(나비야·voxcpm·dnn)→tflite→`owww_mic` recall 실측. 통과 시 D7 한국어 확정.

**Stage 7 — 웨이크워드 D7: 청취축 상태머신 + 엔진 Vosk 채택 (2026.06.25):**
- **청취축 실체화(D16):** [navi/ear/listening.py](../navi/ear/listening.py) `ListenSession` — SLEEP(STT 끔, 호출어만 청취)↔ACTIVE(발화 끊어 STT). 창=**세션+무음 타임아웃**(반려자식, 기본 30초). `--listen --wakeword`로 켜짐, `--listen` 단독은 기존 상시청취(하위호환).
- **WakeWord 계약(벤더 중립):** [navi/ear/wakeword.py](../navi/ear/wakeword.py) — `detect(chunk)·frame_length`만 노출, 엔진은 계약 뒤(Vad와 동일 규약). `FakeWakeWord`(무의존)로 마이크·키 없이 전 사이클 유닛 검증. 엔진은 `create_wakeword` 팩토리로 교체.
- **엔진 결정 경위 — Porcupine → Vosk:**
  - 1차안 Porcupine(D7 원안, 온디바이스·한국어 내장)으로 어댑터까지 구현. 그러나 **Picovoice 콘솔 가입이 회사 이메일을 요구**해 개인 Gmail 차단 — 무료 AccessKey 발급 불가 판명(블로그/FAQ상 free-forever와 실제 가입 화면이 불일치).
  - openWakeWord 검토: 한국어 커스텀 모델은 **GPU 학습 프로젝트**(WSL2+CUDA, Piper 한국어 합성) + 구글 오디오 임베딩 영어 편향 경고 → ROCm 죽은 CPU-only 환경엔 부담 과다.
  - **채택: Vosk(ASR 기반 스팟팅).** 학습0·CPU·한국어 모델 존재. **주의 — Vosk는 진짜 음향 KWS가 아니라 작은 ASR이다.** 당초 grammar 제한(호출어+[unk])으로 KWS처럼 쓰려 했으나 small-ko가 [unk]를 어휘에 안 가져 무시 → grammar가 호출어 하나로 좁혀져 강제 매칭(오수락 폭주). 그래서 **전체 인식 후 전사에서 호출어 포함 매칭**으로 전환(검문①과 같은 방식, 모델만 경량). 결과적으로 SLEEP=경량 ASR(Vosk) / ACTIVE=무거운 whisper의 2단. 콘센트 PC라 상시 인식 부담은 D15가 수용. **함의:** arch 5.1의 "SLEEP=STT 꺼짐"은 엄밀히는 "무거운 STT 꺼짐+경량 ASR 켜짐"으로 읽어야 한다(진짜 텍스트-없는 KWS는 Porcupine/openWakeWord 회귀 시).
  - **화자 인증 제외(아무나 깨어남):** 빅스비식 "내 음성으로 깨우기"(화자 인증 2층)는 v1 제외. 문구 탐지(1층)만. 주인 한정은 후순위.
  - Porcupine 어댑터는 **벤더중립 증명·향후 회사이메일 확보 시 사용** 위해 보존. `WakeWord` 계약 덕에 엔진 교체는 어댑터 1개 + 팩토리/설정 한 줄.
- **프레임 통일(설계 정리):** 엔진이 요구하는 고정 프레임을 어댑터 내부 재정렬 대신 `WakeWord.frame_length` 선언으로 흡수 — 마이크 blocksize·Endpointer frame_ms를 거기 맞춤(EnergyVad는 프레임 크기 무관). `mic.py`에 raw `frames()` 분리, `utterances()`는 그 위에 재구성.
- **수면 명령 거취(검문① KWS 재검토):** 텍스트 게이트 **유지**, KWS는 깨우기 전용. 두 게이트는 상보(arch 5.1) — KWS=SLEEP 입구(파형), 검문①=ACTIVE 변별("나 이제 자라는 통과").
- **Vosk 어댑터 구현 완료:** `VoskWakeWord`([navi/ear/wakeword.py](../navi/ear/wakeword.py)) — KaldiRecognizer 전체 인식, 전사를 공백 정규화해 호출어 포함 매칭. config는 `ear.wakeword.engine` 스위치로 일반화(vosk/porcupine 공존), `create_wakeword("vosk")` 분기. `ready`는 모델 디렉터리 실존까지 확인, 미설치/미존재면 친절 안내 후 종료.
- **검증:** 유닛(FakeWakeWord+가짜 프레임+가짜 시계)으로 SLEEP→ACTIVE→발화→타임아웃→재기상, 검문①→SLEEP복귀 전 사이클 + Vosk 지연임포트·팩토리(89 테스트 green).
- **남음(사용자 셋업 후):** `.venv-voice`에 `pip install vosk` + 한국어 모델(vosk-model-small-ko) 받아 `secrets/`에 압축해제 → 실마이크 E2E(`--listen --voice --wakeword`).

**현재 상태 — Brain→Mouth 배선 완료 + 실청취 통과 (2026.06.18):**
타이핑 → 나비가 GPT-SoVITS 음성으로 답하는 전 구간이 실동한다(한국어·일본어 모두). 배선은 TurnPipeline(`navi/pipeline.py`)이 담당 — Brain.generate_stream과 Mouth.speak_stream이 둘 다 `AsyncIterator[str]`이라 변환 없이 토큰을 흘리고, barge-in은 interrupt()=mouth.stop()+brain.cancel(). 실청취 중 GPT-SoVITS 실동 픽스 3건(아래 Stage 5). D3 GPT-SoVITS fine-tune은 음색=가중치/톤=레퍼런스로 확정(Stage 2~4).

**Stage 0 — WSL2 + ROCm 실패 → CPU로 음색 검증 선회 (게이트 결과):**
- 데스크톱 = AMD RX 6600 XT(gfx1032). **WSL2에서 ROCm 미인식** — `/dev/dxg`는 있으나 `/dev/kfd`(ROCm 커널 인터페이스) 부재로 `torch.cuda.is_available()==False`.
- 원인: WSL2의 AMD ROCm은 별도 WSL 전용 빌드가 필요하고 지원 카드가 RX 7900·Radeon Pro급뿐. gfx1032는 네이티브에서도 비공식 → WSL은 이중으로 불리.
- **결정: 음색 품질(Stage 1)은 GPU 불필요(속도 무시) → CPU로 먼저 검증.** GPU 환경 싸움(네이티브 Ubuntu 듀얼부팅 등)은 음색 합격 후로 미룸. 이로써 **계획서 가정 A(WSL2+ROCm+gfx1032) 깨짐 확정.**
- 환경: WSL Ubuntu, **Python 3.11**(deadsnakes — CosyVoice 생태계가 3.12 미지원, matcha-tts/piper_phonemize가 `<3.12`), CPU torch. CosyVoice repo는 `~/CosyVoice`(navi repo와 별도 clone, `/dev/kfd` 무관하게 동작).
- CosyVoice 설치 함정: requirements가 NVIDIA 전제(tensorrt·onnxruntime-gpu) → GPU 라인 제외하고 추론 필수만. 최신 setuptools(82)가 pkg_resources 제거 → `setuptools<81` + `--no-build-isolation`. matcha-tts는 pip 대신 PYTHONPATH로. 모델은 HF(`FunAudioLLM/CosyVoice2-0.5B`, ModelScope는 41kB/s로 느림).

**Stage 1 — CosyVoice2 zero-shot 청취 (레퍼런스: 블루아카이브 아리스 일본어 보이스):**
- **CosyVoice2 기준 한국어 출력(cross-lingual) = 불가.** 일본어 레퍼런스→한국어 발화는 깨진 소리. → 이 모델에서는 동일 언어 출력만 유효.
- **언어 방향은 미확정.** 현재 테스트가 일본어 레퍼런스(아리스)를 쓰고 있어 일본어 출력을 먼저 검증한 것. 한국어 레퍼런스 음원 탐색 중이며 일/한 양방 지원도 검토 중. GPT-SoVITS가 교차언어를 더 잘 처리한다는 보고 있음 — fine-tune 비교 시 함께 확인 예정.
- **일본어 출력 = "아주 그럴듯함".** 존댓말 캐릭터 어투가 새 문장에서도 잘 유지됨. zero-shot치고 충분히 좋음.
- **한계 두 가지(실청취):**
  - **캐릭터성 평탄화** — 원본의 활기참·강아지 같은 인상이 다소 눌려 "차분한 아리스" 톤. (단 레퍼런스 톤을 따라가므로 차분한 Lobby 레퍼런스 탓일 여지 있음 — 활기찬 레퍼런스로 재시도 중.)
  - **음질 저하 + 간헐 노이즈** — 출력 24kHz 고정이라 스튜디오 원본(무손실)보다 선명도 손실(neural vocoder 구조적 천장, 모델 무관). 노이즈는 zero-shot hallucination·ref-text 정렬 실패(긴 레퍼런스일수록↑).
- **평가:** CosyVoice2는 일반 음성(뉴스·오디오북) 학습이라 서브컬처 캐릭터 억양을 평탄화하는 경향이 실제로 있음. 단 "캐릭터성 완전 소멸"은 과장 — 일본어·존댓말은 충분히 살아있음.

**Stage 2 — GPT-SoVITS fine-tune 청취 (Colab T4, 2026.06.17):**
- **데이터셋:** 아리스 메이드+기본 스킨 168클립(~14분), 전사 검증 후 ogg→wav→`.list` 패키징(`scripts/prep/build_sovits_dataset.py`). 톤이 같은 캐릭터라 두 스킨 합본.
- **학습:** GPT-SoVITS **v2**(한국어 보류 대비 — 한국어는 v2 전용), SoVITS 8ep / GPT 15ep, batch 4, text_low_lr 0.4. 1A 포맷팅 → 1B 학습 → 1C 추론. 산출물 `arisu_e8_s352.pth`(SoVITS) + `arisu-e15.ckpt`(GPT), 로컬 다운로드 완료.
- **검증 언어:** 일본어 ref(메이드 Lobby_2)→일본어 출력. (한국어 ref→출력은 보류 — 한국어 레퍼런스 음원 미확보. v2로 학습해 재학습 없이 추후 가능.)
- **청취 결과 — 음색과 운율의 역할 분리(핵심 발견):**
  - **음색(timbre) = fine-tune 가중치가 담당 ✅** — 어떤 레퍼런스를 넣어도 "그냥 아리스"라 할 만큼 음색 안정. zero-shot처럼 레퍼런스에 음색이 휘둘리지 않음.
  - **운율(prosody — 톤·억양·에너지) = 레퍼런스가 지배.** 차분→하이톤으로 가는 8초 레퍼런스를 넣으면 출력도 동일하게 차분→하이톤으로 따라감. 4초 하이톤 레퍼런스를 넣으면 출력도 하이톤. → fine-tune이 캐릭터성을 죽인 게 아니라, **레퍼런스 선택으로 톤을 제어**하는 것.
  - (정정) 첫 청취에서 "차분/평탄"하게 들린 건 차분한 레퍼런스(Lobby_2)를 골랐기 때문 — 톤 수렴이 아님.
- **D3 결론(잠정):** GPT-SoVITS fine-tune = **음색 안정(가중치) + 톤 제어 가능(레퍼런스)** → **나비 목소리 유력안.** CosyVoice2 zero-shot 대비 음색 안정성에서 우위, 운율은 레퍼런스로 조절.
- **운영 함의:** 나비 데몬에서 무드별 레퍼런스 클립 풀을 두고 상황(차분한 밤 인사 / 신난 아침 등)에 맞춰 레퍼런스를 골라 끼우면 톤 제어 파이프라인 구성 가능.

**Stage 3 — 로컬 WSL CPU 추론 재현 + RTF 측정 (2026.06.17):**
- **환경:** WSL Ubuntu 24.04, Python 3.12, 12코어/15GB. venv + torch CPU(2.12+cpu/torchaudio 2.11+cpu, **torchcodec 제외**) + `requirements.cpu.txt`. 베이스 모델 cnhubert+roberta는 `lj1995/GPT-SoVITS`에서 다운로드. `arisu` ckpt 2종으로 JA→JA 추론.
- **측정 RTF (CPU, v2):** 첫 문장 ~25s(모델 로드 + numba JIT 웜업, 데몬 상주 시 시작 1회 비용) / **웜 상태 RTF ≈ 1.4**(t01 7.2s÷5.1s, t02 6.9s÷4.8s). → 5s 발화당 ~7s 합성.
- **함의:** 비스트리밍 CPU RTF 1.4는 "첫 오디오 ~1초" 목표엔 빠듯. 배포는 ① GPU 가속(Windows 네이티브 DirectML/onnxruntime-directml은 ONNX export 비용 큼 — 별도 과제) 또는 ② 청크 스트리밍(get_tts_wav가 청크 yield)으로 TTFA 단축 필요. CPU도 데몬 상주 + 짧은 발화 위주면 사용 불가 수준은 아님.
- **`try_clone.py` gptsovits 분기 정본화 완료** — 실 API 반영(sys.path 2개, env 절대경로, change_sovits_weights 인자, torchaudio→soundfile 패치). 환경 복원 절차는 메모리 `gptsovits-wsl-local`.

**Stage 4 — Windows native 어댑터 이식 완료 (2026.06.17~18):**
- **방향 전환 결정: WSL이 아니라 Windows native 단일 프로세스 in-process 통합.** 근거: ROCm 불가 확정
  → CPU torch는 Windows에서 동일 동작 → WSL을 유지할 유일한 이유(GPU)가 사라짐. WSL 브리지(HTTP
  서버)는 **프로세스 2개 + IPC**라 로컬 상시 데몬·배포에 비현실적이라 기각. GPU(CUDA/DirectML)는
  추후 `device` 인자만 교체.
- **의존성 빌드 벽 (핵심 발견):** `pyopenjtalk`(일본어 G2P)는 Windows prebuilt wheel 없음 — PyPI sdist만,
  conda-forge에도 없음. `jieba_fast`도 wheel 없음. **해결:** pyopenjtalk는 VS2019 BuildTools + cmake<4
  + Windows SDK rc.exe 로 1회 소스 빌드. jieba_fast는 어댑터 shim으로 jieba alias 대체.
- **`_ensure_engine` shim 목록 (모두 `navi/mouth/gptsovits.py` 에 흡수됨):**
  - `sys.path` 3개: repo / GPT_SoVITS/ / GPT_SoVITS/eres2net/ (sv.py의 ERes2NetV2 import)
  - `os.chdir(repo)`: GPT-SoVITS 내부가 `os.getcwd()` 기준 상대경로 사용
  - ckpt 경로 절대화: chdir 전에 미리 `os.path.abspath()` — CWD 이동 후 상대경로 깨짐 방지
  - env 변수: `cnhubert_base_path`, `bert_path`, `gpt_path`, `sovits_path` (import 시점에 즉시 로드)
  - `HfFolder` shim: gradio/oauth.py 가 사용, huggingface_hub 0.24+ 에서 제거됨
  - `is_offline_mode` shim: transformers 4.50 hub.py 가 사용, 최신 hf_hub 에서 제거됨
  - `jieba_fast` alias: `sys.modules["jieba_fast"] = jieba` (중국어 전처리 전용)
  - `torchaudio.load` → soundfile 교체: torchcodec/ffmpeg 의존 회피
  - `fast_langdetect` 디렉토리 보장: 없으면 lid.176.bin 다운로드 전에 FileNotFoundError
  - `change_sovits_weights` 소진 + `prompt_language`/`text_language` 인자 필수 전달
- **venv 분리:** `.venv-voice`(Python 3.12) — GPT-SoVITS 런타임. GPT-SoVITS repo: `C:\gptsovits`
  (ASCII 경로). 기존 `.venv`(3.13, 텍스트 뼈대)와 분리.
- **재현 가능 셋업 스크립트:** [`scripts/setup/setup_voice_env.ps1`](../scripts/setup/setup_voice_env.ps1)
  — 1회 실행으로 venv 생성 → pyopenjtalk 빌드 → torch CPU → GPT-SoVITS deps → 모델 다운로드까지.
  의존성 목록: [`requirements.win-cpu.txt`](../requirements.win-cpu.txt).
- **Windows native 합성 검증 완료 (2026.06.18):**
  - 레퍼런스: `Arisu_LogIn_1.wav` (JA) / 합성: `アリスはメイド勇者になります！`
  - RTF: T0=2.13(콜드, numba JIT 웜업 포함) / T1=**1.39**(웜) → WSL 측정치(~1.4)와 일치
  - **D3 최종 확정:** 실청취 "집중 안 하면 동일인으로 착각" → **음색 품질 합격.** fine-tune 가중치로 음색 안정, 레퍼런스로 톤 제어.

**Stage 5 — Brain→Mouth 배선 + 실청취 실동 (2026.06.18, `feat/wire-llm-tts`):**
- **배선:** `navi/pipeline.py` TurnPipeline — run_turn이 요청 조립→Brain 토큰→Mouth 음성을 묶고,
  _tee가 토큰을 Mouth로 흘리며 화면에도 echo. Brain·Mouth 계약이 둘 다 `AsyncIterator[str]`이라
  변환 없이 통과(N/N+1 문장 오버래핑은 Mouth 내부 큐가 담당). config에 mouth 섹션(vendor·voice·
  gptsovits 경로)+MouthConfig, CLI `--voice`. barge-in interrupt()=mouth.stop()+brain.cancel().
- **실청취 중 발견·수정한 GPT-SoVITS 실동 픽스 3건 (배선이 돌려면 필수였음):**
  1. **tqdm `WinError 1`** — 합성이 `asyncio.to_thread` 안에서 돌 때 tqdm이 터미널에 `\r`을 쓰려다
     실패해 둘째 문장부터 합성이 죽었다. `_ensure_engine`에서 `TQDM_DISABLE=1`로 끔.
  2. **pyopenjtalk mecab 한글 경로** — 일본어 G2P 사전이 venv(한글 경로) 안에 있으면 mecab C++가
     경로를 ANSI로 해석해 'Failed to initialize Mecab'. 단축경로(8.3)도 cp949 유효 한글이 남아 무력
     → 사전을 ASCII 경로(`C:\gptsovits\open_jtalk_dic_utf_8-1.11`)로 복사하고 `OPEN_JTALK_DICT_DIR`로
     가리킴. (한국어는 mecab 미사용이라 직전 한국어 청취는 통과했음 → 일본어 전환 시 노출됨)
  3. **종료 프리즈** — 합성 `to_thread`는 외부 라이브러리 추론(1500스텝)이라 중간에 못 끊는데,
     `asyncio.run`이 종료 시 `shutdown_default_executor`로 그 스레드를 join 대기하다 프리즈
     (+PortAudio/torch 잔여). `main()` finally에서 `os._exit(0)`로 executor join을 건너뛰고 즉시 종료.
- **재현성:** `setup_voice_env.ps1` Step 8에 mecab 사전 ASCII 복사 추가(사전은 패키지에 없고 첫 사용 시
  다운로드 → 트리거 후 복사). shim 2개(tqdm·mecab)는 `gptsovits.py` `_ensure_engine`에 흡수.
- **콜드 스타트:** 첫 토큰 ~84s(GPT-SoVITS 가중치 로드 ~71s 동기 + Gemini). speak_stream이 엔진을
  동기 로드 후 LLM을 소비하는 구조라 로드가 첫 토큰을 막음 → 데몬 기동 시 엔진 워밍업이 개선 후보.
- 일본어 응답 페르소나 `personas/navi_ja.yaml`(배선 테스트용 placeholder) 추가, `config.yaml`
  card_path 전환. (페르소나 표준 스키마·언어 속성 필드화는 별도 작업으로 분리)

**Stage 6 — Ear 마이크 입력 파이프라인 (2026.06.25, PR #8 머지):**
- **구현:** 마이크 → VAD → 엔드포인팅 → 발화 단위 방출 → STT → Brain(→Mouth) 실시간 루프. CLI `--listen`.
  - `navi/ear/vad.py` — `Vad` 추상 + `EnergyVad`(RMS 임계, 기본 150). `navi/ear/endpointer.py` — 순수 상태머신(하드웨어 없이 테스트 가능). `navi/ear/mic.py` — `MicListener`(sounddevice 지연 임포트, `--mic`로 실물 장치 지정).
  - CLI 플래그: `--listen` `--mic` `--vad-threshold` `--stt-model`. STT 모델 시작 시 선로드(첫 발화에 묻던 33s 로딩 분리), 단계별 속도 로그(STT·첫토큰·TTS).
  - `scripts/mic_check.py` — 장치 목록 + 실시간 RMS 미터(threshold 튜닝 진단).
- **실측 (실청취):** 첫 발화 모델 선로드로 33s→분리. STT 속도 `large-v3-turbo` 9.5s → `small` 2.5s(RTF~0.69, 4배). 전 구간(mic→발화→STT→음성 답변) 동작 확인. 테스트 48 passed.
- **확인된 한계 → 후속 PR:** 속도 ~1.5s 미달(D2 스트리밍 STT/D8 GPU) · 웨이크워드 없음(D7/검문①) · EnergyVad 오탐(D12 튜닝).
- **설계 메모:** STT→LLM 사이에 데몬 검문소(검문①: 키워드 게이트)가 들어가야 수면/DND 게이트·언령을 LLM에 안 맡길 수 있음 → 통합 실시간 API(Gemini Live) 배제(D4)의 실동 근거. 킬스위치(발화 중단)는 단어가 아닌 VAD(barge-in)가 담당.

**Stage 9 — 전 구간 속도 프로파일링 (2026.07.02, `research/speed-profile`):**
- **목적:** Phase 2 완료 기준 *"부르면 ~1.5초 안에 음성 답변"*(희망 목표) 미달의 병목을 실측으로 규명 — 다음 갈림길(D2 스트리밍 STT vs D8 GPU) 판단 근거. **측정만, 최적화는 범위 밖.**
- **측정 조건:** Windows native CPU(GPU 없음, Stage 0), `.venv-voice`. 입력=`scripts/in/probe_ko.wav`(한국어 2.2초 발화, Supertonic 합성 프로브). STT=faster-whisper `small`(웜) → Brain=gemini-3-flash-preview(무료 티어) → Mouth=GPT-SoVITS(아리스, JA). 동일 입력 웜업 1회 + 3회 반복, 엔진 상주(데몬 전제). 도구: `scripts/bench/profile_turn.py`, 계측 로그는 `gptsovits.py`(문장확정·합성완료·TTFA)와 `cli.py --input`(웜 STT)에 DEBUG/INFO로 흡수. 실행 시 `PYTHONUTF8=1` 필요(GPT-SoVITS가 import 시 중국어 print → cp949 리다이렉트에서 크래시).
- **실측 (웜업 제외 3회):**

  | 회차 | ② STT | ③ 첫 토큰(TTFT) | ④ 첫 청크 합성 (오디오 길이) | TTFA(②이후) |
  | :-- | --: | --: | --: | --: |
  | #1 | 2231ms | 1983ms | 20301ms (16.2s, RTF 1.25) | 22454ms |
  | #2 | 2159ms | 16602ms | 27443ms (23.3s, RTF 1.18) | 44393ms |
  | #3 | 2088ms | 39522ms | 14848ms (14.5s, RTF 1.03) | 54513ms |

  구간별 정리 (E2E = ①800ms 고정 + ② + TTFA. TTFT는 편차가 커서 정상 케이스 #1 기준):

  | 구간 | 지연 | E2E 대비 | 개선 레버 |
  | :-- | --: | --: | :-- |
  | ① 발화 종료→엔드포인터 확정 | 800ms (설계 상수) | 3% | D12 튜닝 (400~500ms 여지) |
  | ② STT (small, 웜, 2.2s 발화) | ~2.2s | 9% | D2 스트리밍 STT(발화 중 처리→체감 0) 또는 D8 GPU(~0.3s) |
  | ③ Brain 첫 토큰 | 2.0s (편차 2.0~39.5s) | 8% | **D1 — 유료 티어/벤더 교체. GPU 무관** |
  | ④ TTS 첫 청크 합성 | ~20s | 79% | 문장 분리 수정(아래) + D8 GPU(RTF 0.1~0.2, ~10배) |
  | **E2E (발화 종료→첫 오디오)** | **~25.5s** | 100% | 목표 1.5s 대비 **17배 초과** |

- **발견 1 — 문장 분리가 일본어에서 사실상 미동작 (④ 비대의 절반):** `_SENTENCE_END` 정규식이 종결부호 뒤 공백(`(?=\s|$)`)을 요구하는데 일본어는 `。` 뒤 공백이 없다 → 토큰 경계가 우연히 `。`에서 끝날 때만 잘리고, 아니면 2~3문장(79~109자, 14~23초 오디오)이 한 청크로 합성됨. 수정하면 첫 청크가 첫 문장(~5초 오디오)으로 줄어 CPU에서도 ④가 ~20s→~6s. **공짜 레버, 최우선.**
- **발견 2 — Gemini 무료 티어 TTFT 편차 통제 불가:** 같은 밤 연속 4콜에서 2.0/16.6/17.0/39.5s. 과거 실측(06.20~21 로그)은 웜 1.1~1.5s — 서버 상태에 따라 수십 배 요동. 로컬 최적화(D2/D8)로 못 잡는 유일한 구간. Phase 2 속도 논의에 **D1(유료 티어 또는 벤더 교체)이 선행 조건**으로 편입되어야 함.
- **발견 3 — 합성 RTF 재확인:** 웜 RTF 1.03~1.25(청크 길수록 유리) — Stage 3~4 실측(~1.4)과 정합. CPU 한계 확정.
- **~1.5초 목표 판정:** 이 목표는 **희망치이며, 현 정의(발화 종료→첫 오디오)로는 GPU를 붙여도 미달.** 레버를 누적 적용한 시나리오(현재 열만 실측, 나머지는 실측 RTF 기반 추정):

  | 구간 | 현재 실측 (CPU) | +문장분리 수정 | +D8 GPU | +D1 유료 티어 | +D12·D2 |
  | :-- | --: | --: | --: | --: | --: |
  | ① 엔드포인터 확정 | 800 | 800 | 800 | 800 | 500 (D12) |
  | ② STT | 2,200 | 2,200 | ~300 | ~300 | ~0 (D2 발화 중 처리) |
  | ③ Brain 첫 토큰 | 2,000 *(편차 2~40s)* | 2,000 | 2,000 | ~1,000 | ~1,000 |
  | ③′ 첫 문장 완성 대기 | 200 | 200 | 200 | 200 | 200 |
  | ④ 첫 문장 합성 | 20,300 *(16s 청크)* | ~6,000 *(5s 청크)* | ~750 *(RTF 0.15)* | ~750 | ~750 |
  | **명령→답변 E2E** | **~25.5s** | **~11.2s** | **~4.1s** | **~3.1s** | **~2.5s** |

  TTFT+엔드포인터만으로 ~1.5s라 전 레버를 넣어도 **~2.5s가 구조적 하한.** 단 GPU가 ②④를 10배 줄이는 건 유효(~7s→~1s).
- **목표 재정의 (2026.07.03 합의):** 경로를 둘로 쪼갠다. 웨이크워드(D7)가 붙으면 호출 경로는 엔드포인터·STT·LLM을 전부 우회하므로 "부르면 1.5초"는 호출 경로에서 충족된다.
  - **호출 단독("나비야"):** 웨이크워드 검출 → 고정 응답("네") **≤0.5s** — 사전 합성 캐시, 결정론 레이어.
  - **명령/질문:** 필러 마스킹 **없이** 실제 답변으로 응답, 목표 **~3s**(위 시트). 캔 맞장구를 질문 뒤에 끼우는 안은 기각 — 부자연스럽고, "무엇을 말할까=모델 소유" 원칙을 결정론 레이어가 침범한다. (참고: 사람 턴 간격 0.2~1s, 상용 스피커 1.5~2.5s — 3s는 용인 하한선)
- **결론 한 줄: 병목은 ④TTS 첫 청크(79%, 문장분리 버그×CPU RTF)와 ③TTFT 편차(무료 티어), 레버는 ①문장 분리 수정(공짜)→②D8 GPU→③D1 유료 티어 순. D2 스트리밍 STT는 2.2s 절감으로 그다음.**

**남은 일:**
- (속도, Stage 9 순서) ① 문장 분리 수정(일본어 무공백 종결) ② D8 GPU 가속 ③ D1 유료 티어/벤더 — 재정의 목표 "호출 즉답 ≤0.5s / 명령 답변 ≤3s".
- 웨이크워드(D7) — Colab 학습 중.
- (개선 후보) 콜드 스타트 — 데몬 기동 시 GPT-SoVITS 엔진 워밍업(더미 합성)으로 첫 토큰 지연 단축.
- (보류) 한국어 ref→출력 — 한국어 레퍼런스 음원 확보 시.
- (보류) 웨이크워드(D7 Porcupine, 음향 KWS) — SLEEP에서 깨우려면 음향 필수.

**이전 상태 — 세 부품 독립 작동 + Mouth 실어댑터 완성:**
- **답변 생성**(Brain + Conductor + 기억) — Phase 1에서 구현, CLI 텍스트 대화로 작동.
- **TTS**(Supertonic, F1) — 한국어 합성 검증(`scripts/try_tts.py`) → **`SupertonicMouth` 실어댑터 구현 완료.**
- **STT**(faster-whisper turbo) — 한국어 받아쓰기 실검증(`scripts/try_stt.py`).

**`SupertonicMouth` 실어댑터 (feat/mouth-supertonic):**
fake를 실엔진으로 교체 — Mouth 계약(4.8) 세 메서드를 Supertonic으로 채움(`navi/mouth/supertonic.py`).
- **문장청크 스트리밍**: Supertonic은 배치 엔진이라 토큰을 문장 경계(`.!?。…\n`)로 끊어 청크별 합성→순차 재생. 첫 문장이 나오는 즉시 말하기 시작(첫 오디오 ~1초 목표). 합성(N+1)이 재생(N)과 겹쳐 끊김 최소화(asyncio.Queue + to_thread 파이프라인).
- **barge-in**: `stop()`이 `sd.stop()`으로 재생 즉시 중단 + 합성 워커는 다음 문장 경계에서 협조 중단.
- 재생은 `sounddevice`, 목소리는 `VoiceProfile.vendor_voice_id`(→Supertonic 음색 F1)·`.speed` 매핑.
- 팩토리 `create_mouth("supertonic")` 연결. 음성 의존성은 `[voice]` extra로 분리(`pip install -e ".[voice]"`) — 텍스트 뼈대는 가볍게 유지.
- 검증: 단위 테스트 5개(가짜 엔진 주입, 실모델·실오디오 없이 청크/꼬리말/음색매핑/barge-in 고정) + 실청취 도구 `scripts/try_mouth.py`(첫 오디오 지연 측정·`--barge-in`). 전체 28 passed.
- **실측 완료 (2026.06.14, 데스크톱 CPU·F1):** 첫 오디오 **0.6초**(웜)/1.4초(콜드 첫 발화 — ONNX 워밍업), 목표 ~1초 충족. barge-in stop() 후 ~0.2초 내 종료. **배치 대기 없음을 직접 증명** — 텍스트를 30배로 늘려도 첫 오디오 0.64s→0.62s로 길이와 무관(배치였다면 ~18초로 밀렸을 것). 합성(N+1)이 재생(N)과 겹쳐 도는 게 실측으로 확인됨.
- 콜드 첫 발화 1.4초는 데몬 기동 시 더미 합성으로 ONNX를 미리 달궈 0.6초대로 낮출 수 있음(향후 튜닝 후보, 미구현).

**중요 — 세 부품은 아직 미연결.** 각각 독립 작동만 하고 파이프라인으로 이어지지 않았다. `try_mouth.py`는 Brain 응답이 아닌 정해진 텍스트를 토큰처럼 흉내 내 먹인 **Mouth 단독 검증**이다.

**다음 작업 후보:**
1. **(추천) 응답생성 → TTS 배선** — Conductor가 Brain 응답 토큰 스트림을 `SupertonicMouth.speak_stream`으로 흘린다. 계약이 이미 양쪽 `AsyncIterator[str]`로 호환돼 배선만 하면 됨. 입력(마이크) 없이도 "타이핑 → 나비가 말로 답함"까지 체감 가능. 입력단보다 작고, 끝내면 처음으로 두뇌가 실제로 말하는 걸 듣게 됨.
2. **입력단(Ear)** — 마이크 → STT(faster-whisper) 연결. 첫 조각으로는 1번보다 큼.

**어댑터 계약·stub 완료 (PR #1 머지):**
STT/Mouth 계약(01 문서 4.3·4.8) + fake 어댑터 + 팩토리 + 테스트(`navi/stt`·`navi/mouth`·`tests/test_voice.py`).
벤더는 `_PENDING_D2`/`_PENDING_D3`로 보류 표시 — 결정 후 어댑터 한 장 끼우면 됨.

**로컬 우선으로 방향 전환 (GPU 보유 → 비용 0·벤더 비종속).** 데스크톱 = AMD RX 6600 XT(상시기기 호스트).
- **TTS 잠정:** Supertonic(`pip install supertonic`, 한국어 `ko`, CPU RTF 0.35) — 프리셋 10종(M1–5/F1–5) 중 **F1을 나비 잠정 목소리로** 채택(2026.06.13). 음색 만족도가 확정적이지 않아 추후 voice cloning(CosyVoice2/XTTS-v2/F5-TTS, 6초 레퍼런스) 재검토 여지.
- **STT 실검증 → 잠정 확정:** faster-whisper large-v3-turbo(int8). 실제 구어체 발화(비속어·반복·외래어 16초)로 검증 — 풀 large-v3와 품질 동급이면서 빠름(CPU RTF 0.61). **동일 발화에서 RTZR(VITO) 웹 데모를 오히려 능가**('아' 누락·'오늘'→'이 우리' 오인식·반복 1회 누락을 turbo는 다 잡음). 단 잡음·전화음질은 미검증 → 폴백 유지. 튜닝: 반복 환각 대비 `vad_filter=True`.
- **GPU 가속은 배포 숙제:** AMD라 CUDA 불가 → onnxruntime-directml 또는 whisper.cpp+Vulkan. 품질은 하드웨어 무관이라 결정엔 CPU로 충분.
- 비교 도구: `scripts/try_tts.py`·`scripts/try_stt.py`. HF 모델 다운로드엔 `.env`의 `HF_TOKEN` 필요(비인증은 속도제한).
- 명칭 통일: 연속 정체성 호칭 "그 애" → **"나비"** (문서·코드 전반).

**원 관문(클라우드 후보, 폴백):** D3 수퍼톤 Play vs Cartesia, D2 VITO vs Clova vs Deepgram — 로컬 품질 미달 시 청취 비교.

**Phase 1에서 넘어온 잔여 항목:**
- 완료 기준 ②의 실청취 비교 — Anthropic 키 확보 시 `python -m navi.cli --brain anthropic`으로 같은 대화 비교.

---

## Phase 1 — 텍스트 뼈대 ✅ (2026.06.11 ~ 06.13)

**산출물:** CLI 텍스트 대화(`python -m navi.cli [-v|-vv] [--brain ...] [--db ...]`)
모듈: 계약 타입(models) / 설정(config) / 단기기억·친밀도·usage_log(memory, SQLite) /
캐릭터 카드(persona + personas/navi.yaml) / 프롬프트 조립(conductor) / 두뇌 어댑터(brain — Gemini·Anthropic·Echo).
테스트 17개(pytest) — 재시작 기억, 벤더 교체 시 인격 동일성, e2e 포함.

**완료 기준 검증:**
1. 껐다 켜도 어제 대화 기억 — ✅ 실검증 (Gemini 실대화 + 사용자 실사용)
2. 벤더 교체에도 같은 말투 — ✅ 구조 검증 (동일 카드·메시지 조립을 테스트로 고정). 실청취만 잔여.

**구현 중 내린 결정 (D번호 외, 근거는 커밋 본문):**
- 캐릭터 카드는 DB가 아닌 **YAML 파일**(personas/) — 창작물은 git이 원본
- few-shot 예시는 messages가 아닌 **시스템 프롬프트 안에** — 가짜 기억 오염 방지 + 캐싱 효율
- 계약 확장: `recall_recent_for_user(user_id, n)` — 세션 경계 없는 인출이어야 재시작 기억이 성립
- 잡담 두뇌 기본값: **gemini-3-flash-preview + thinking 비활성** (실측 TTFT 1.1~1.2초.
  2.5-flash는 품질 부족 피드백, 3.5-flash는 무료 티어에서 TTFT 4~93초라 보류. D1 자체는 계속 보류)

**운영 메모 (새 세션이 알아야 할 환경 상태):**
- `.env`에 GEMINI_API_KEY 설정됨 — **무료 티어** (Flash 계열만 호출 가능, Pro급은 429.
  Google AI Pro 구독은 API와 무관). Anthropic 키 미보유.
- 환경: Windows / `.venv\Scripts\python.exe` / 테스트는 `-m pytest -q`
- **실구동 테스트 규칙: 반드시 `--db 임시파일`로 본 기억(navi.db)과 격리.**
  (06.13 테스트 발화가 본 기억에 영구 적재되는 오염 사고 → 사용자 승인 하에 DB 초기화한 이력)
- 레이턴시 참고치: 첫 토큰 1.1~1.6초 (무료 티어, 프롬프트 ~1,300토큰). Phase 2 예산 산정 시 참고.
- 로그: `-v`(INFO)/`-vv`(DEBUG), 파일 로그 logs/navi.log 상시 기록.
