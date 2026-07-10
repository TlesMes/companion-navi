# 최소 GUI — 관찰·제어 플레인 (Stage 15 설계·구현 계획)

> 2026.07.10 합의. 목업 확정본: [gui_mockup.html](./gui_mockup.html) (브라우저로 열면 렌더됨).
> 요지 기록은 [progress.md](../progress.md) "Stage 15 준비", 배경 순서는 같은 문서 "다음 갈림길" 3번.

## 원칙 (전 구간 적용)

- **GUI 죽어도 나비는 산다** — GUI는 관찰·제어만, 오디오 핫패스(마이크→STT→TTS)에 절대 불개입.
  버스의 "느린 구독자 격리"(구독자별 유한 큐 + 논블로킹 publish)가 이 보장의 근거.
- **결정론 게이트는 데몬 소유** — GUI 버튼은 음성 명령과 같은 `ModeMachine.command()` /
  `check_gate` 경로를 부를 뿐, 판정 로직을 갖지 않는다.
- **사용자 오버라이드 > 자동 판단** — 톤 칩의 최종형(자동 점등 + 클릭=핀)까지 이 원칙으로 설계.
- 바인딩 `127.0.0.1` 고정 — localhost 밖 노출 없음.

## 아키텍처

```
데몬 프로세스 (python -m navi.daemon)
├─ DaemonCore (기존)
├─ EventBus (기존) ←─ "gui" 구독자
└─ 컨트롤 플레인: FastAPI + uvicorn을 같은 이벤트 루프의 태스크로
     · 버스가 프로세스 내 pub/sub이라 서버는 데몬 안에 있어야 구독 가능
     · 서버 태스크 예외는 삼킨다 — 서버가 죽어도 데몬 본체 생존

GUI 프로세스 (python -m navi.gui) — 별도 프로세스
└─ pywebview 창(Edge WebView2, 약 360×640 고정) ← http://127.0.0.1:{port} 로드
     · 프런트 = navi/gui/static/index.html 단일 파일 (바닐라 JS, 빌드 스텝 0)
     · 추후 Tauri 래핑 경로 보존 (UI가 웹이므로 껍데기만 교체)
```

스택 배제 근거: NiceGUI/Streamlit(자체 루프가 데몬 루프와 충돌), Electron(Node 의존+150MB),
PySide6(웹 UI와 갈라져 Tauri 경로 사망). pywebview는 Python 패키지 하나 + OS 내장 WebView2.

## API 표면

| 메서드·경로 | 내부 호출 | 비고 |
| :-- | :-- | :-- |
| `GET /status` | `DaemonState.snapshot()` | daemon.py 주석에 예고된 그대로 |
| `POST /mode/{cmd}` | `ModeMachine.command()` | wake·snooze·dnd·dnd_clear·sleep — 음성 명령과 동일 경로, `_apply_mode(force_persist=True)` |
| `PUT /mode/window` | `ModeMachine.set_window()` | 취침창 런타임 변경 (Stage 14 예고) |
| `POST /shutdown` | `bus.publish(SHUTDOWN)` | 센티널 파일(`logs/navi.stop`) 방식 대체 (Stage 13 예고) |
| `WS /events` | `bus.subscribe("gui")` | 접속 시 `last_events` 링버퍼(50)로 초기 채움 후 라이브 |
| `GET /voices` · `POST /voice` | `TurnPipeline.set_voice()` | **현재 페르소나의** 톤(레퍼런스) 목록·선택 — 재생 중(`is_playing`)이면 409 |
| `GET /personas` · `POST /persona` | `SwapRuntime.swap_persona()` | 페르소나 **번들** 교체(카드 즉시, 가중치는 후속) — 재생 중 409 |

## PR 분할 (검증 단위 3개)

### PR ① `feat/core-control-plane` — STAGE 계측 + HTTP/WS 서버

- **`EventKind.STAGE` 추가** ([navi/bus.py](../../navi/bus.py)): payload `(stage, phase, detail)` —
  stage ∈ {stt, gate, brain, tts}, phase ∈ {start, done}, detail은 소요 ms·게이트 결과 등.
  발행 지점: 데몬 `transcribe`(stt)·`_handle_utterance`(gate)는 직접, Brain/TTS는
  **TurnPipeline 생성자에 `on_stage: Callable | None` 주입**(파이프라인은 버스를 모름 —
  데몬이 `bus.publish`로 연결). 1차는 pipeline 수준(brain 첫 토큰, tts 진입/종료)만 —
  TTFA(첫 오디오)는 어댑터 확장이 필요해 후속.
  부수 이득: Stage 12에서 수동 측정한 구간별 지연이 상시 기록 → D8(GPU) 재측정 재료.
- **`navi/control/server.py`**: `create_app(...)` — DaemonCore·ModeMachine·bus를 주입받아
  FastAPI 앱 구성. 데몬 `_run`에서 `uvicorn.Server`를 태스크로 기동(루프 공유,
  `install_signal_handlers` 금지 — 데몬이 시그널 소유). config `control:` 섹션(enabled·port).
- 이 PR 범위: /status·/mode/*·/mode/window·/shutdown·WS /events (음성 관련 API는 ②).
- **검증**: FastAPI TestClient + fake ModeMachine/버스 주입 유닛(기존 데몬 테스트 패턴,
  마이크·키 불필요) · 오프라인 E2E — echo 두뇌 데몬 기동 후 curl로 모드 전이 →
  `mode_state` 영속화·MODE_CHANGED 수신 확인 · POST /shutdown으로 종료.

### PR ② `feat/mouth-voice-swap` — 페르소나·톤 런타임 교체

- **페르소나-음색 번들 (2026.07.10 결정, 원안 개정)**: 페르소나 = 카드(성격·말투) +
  음색(fine-tune 가중치) + 톤 목록(그 음색의 레퍼런스 wav). 톤 레퍼런스는 해당 fine-tune
  화자의 녹음이라 다른 가중치에 교차 적용하면 목소리 연속성 원칙 위반 — 전역 config
  `mouth.tones:` 안은 폐기하고 **persona yaml이 `voice:` 섹션으로 소유**(아래 config 절 스키마).
  런타임 독립 축은 "현재 페르소나 안의 톤 선택" 하나뿐, POST /persona가 번들 교체의 진입점.
- **`VoiceProfile.ref_text: str = ""` 추가** ([navi/models.py](../../navi/models.py)):
  gptsovits `speak_stream`이 `voice.ref_text` 우선, 빈값이면 생성자 `ref_text`(하위호환).
  근거: 레퍼런스 wav(`vendor_voice_id`)는 이미 매 턴 인자인데 전사만 어댑터에 박혀 있어
  톤 교체가 막힘 — 2026.07.10 실사(progress.md 백로그 항목 참조).
- **persona yaml `voice:` 섹션**(옵셔널 — 없으면 config `mouth.voice` 폴백): `name`·`speed` +
  벤더명 하위 섹션(config `_load_mouth` 관례와 동일). gptsovits는 `gpt_ckpt`/`sovits_ckpt`/
  `ref_lang`/`gen_lang`(전방호환 — 이번 PR은 파싱만, 런타임 적용은 가중치 교체 후속 PR과 한 몸)
  + `tones:`(첫 항목이 기본 톤). **부팅 시에도 번들 우선** — 카드에 활성 벤더 섹션이 있으면
  mouth options(ckpt류)·초기 VoiceProfile을 카드에서 만든다.
- **`TurnPipeline.set_voice(profile)` / `current_voice` / `is_playing()`** — 다음 턴부터
  적용(턴 중 교체 없음), is_playing은 mouth 위임.
- **`Conductor.set_card(card)`** + 데몬 `run_turn`의 `card.character` 클로저 캡처를 동적 참조로.
- **`SwapRuntime` 파사드**(navi/control/runtime.py 신설) — conductor·pipeline(텍스트 모드면
  None)·personas 디렉토리를 들고 스캔·교체·409 판정 담당, `create_app(swap=...)` 옵셔널
  주입(미주입 503 — DaemonCore 비대화 방지, PR ① 테스트 보존). **`persona_id`(카드 주인)와
  `voice_persona_id`(톤 세트 주인)를 분리 추적** — 가중치 불일치 페르소나로 교체하면 카드만
  바뀌고 톤 세트·VoiceProfile은 유지(`voice_swapped: false`), 가중치 교체 후속 PR이 이 분열을
  해소하며 두 id를 다시 합친다.
- API: 위 표의 /voices·/voice·/personas·/persona. **/persona도 재생 중 409**(voice 교체 내포).
- 톤 칩 최종형 메모: Phase 3-5(감정 태그)에서 STAGE tts payload에 tone을 실어 **자동 점등**,
  클릭은 **핀(고정) 오버라이드**로 승격 — 이번 PR은 수동 선택(=핀만 있는 상태)으로 시작.
- 음색(fine-tune 가중치) 교체 API는 **후속**(가능 확인됨 — `change_*_weights` 재호출,
  재생 중 금지 + 로딩 중 턴 차단 유예 필요). 엔진 핫스왑은 안 함(아래 결정).
- **검증**: fake mouth 유닛(ref_text 우선순위·재생 중 409) · echo E2E — /voice 교체 후
  다음 턴 VoiceProfile 반영·/persona 교체 후 시스템 프롬프트 교체 확인. 실기 E2E는
  PR ③ 이후 Stage 15 전체를 한 번에(2026.07.10 합의).

### PR ③ `feat/gui-app` — pywebview 창 + 프런트

- **`navi/gui/__main__.py`**: pywebview 창(360×640 고정, 타이틀 "나비") — config에서 포트
  읽어 접속, 데몬 미기동이면 안내 화면 + 재시도. 의존성 `pywebview`는 기본 venv에 추가
  (음성 스택 불필요 — GUI는 오디오를 만지지 않는다).
- **`navi/gui/static/index.html`** (단일 파일, 목업 gui_mockup.html이 기준):
  - 5노드 파이프라인(귀→받아쓰기→검문→두뇌→목소리) — STAGE 이벤트로 현재 단계만 점멸,
    대기 중엔 귀 노드 숨쉬기, 청취축 SLEEP이면 귀 소등.
  - 헤더: 능동축 필(ACTIVE=초록·SLEEP=회색·DND=주황·SNOOZE=파랑) + 로그 다이얼로그 버튼.
  - 오버라이드 버튼 4개(기상·스누즈·DND·재우기) → POST /mode/*.
  - 취침창 24시간 스트립(자정 넘김 양끝 음영 + 현재 시각 마커) — 클릭 인라인 편집 → PUT.
  - 페르소나 카드 그리드 + 톤 칩(아이콘·미니 파형·시청취) → /persona·/voice.
  - 로그 다이얼로그: WS 이벤트 원문(메인 창은 캡션 한 줄만). WS 끊김 시 자동 재연결.
- **검증(실기)**: 데몬 `--voice --wakeword` + GUI — ① "나비야" 발화로 파이프라인 점등 흐름
  실측 ② GUI 버튼 전이가 MODE_CHANGED로 라이브 반영 ③ GUI 강제 종료 후 음성 대화 무영향
  ④ 취침창 변경이 재기동 후 유지(영속화는 데몬 소유 아님 — config 반영 여부는 이 PR에서 결정).

## config.yaml · persona yaml 추가안

```yaml
# config.yaml — PR ①에서 추가됨
control:
  enabled: true      # 컨트롤 플레인 서버 (데몬 내 태스크)
  port: 8765         # 127.0.0.1 고정 바인딩
```

```yaml
# personas/*.yaml — PR ② 번들 모델: 음색·톤은 페르소나 소유 (옵셔널, 없으면 config mouth.voice 폴백)
voice:
  name: navi          # VoiceProfile.name (논리적 정체성)
  speed: 1.0
  gptsovits:          # 벤더명 하위 섹션 — config _load_mouth 관례와 동일
    gpt_ckpt: secrets/voice_ref/....ckpt    # 루트 기준 상대경로 — 가중치 교체는 후속 PR
    sovits_ckpt: secrets/voice_ref/....pth
    ref_lang: ko      # 전방호환 — 런타임 적용은 후속 PR
    gen_lang: ko
    tones:            # 첫 항목이 기본 톤
      - { name: 기본, icon: mood-smile, voice_id: secrets/voice_ref/base.wav, ref_text: "..." }
      - { name: 신남, icon: mood-happy, voice_id: secrets/voice_ref/happy.wav, ref_text: "..." }
```

## 보류·후순위

- 엔진 핫스왑(Supertonic↔GPT-SoVITS) — **안 함으로 결정(2026.07.10)**. 모델(STT·TTS·KWS)은
  시작 1회 웜업·무교체가 원칙, 런타임 교체 범위는 **가중치(음색)·톤·페르소나까지**.
  근거: 엔진 전환의 가치는 CPU 속도 우회인데 D8(GPU)로 소멸하는 한시적 문제 + 엔진 간
  목소리가 달라 연속성 원칙과 상충 + `VoiceProfile` 의미가 엔진별로 갈라져 API 계약 복잡화.
- 톤 자동 점등(감정 태그 연동) — Phase 3-5에서 이 GUI를 확장.
- 음색 가중치 교체 API — 유예 처리 포함 후속 PR. `change_*_weights`는 CPU에서 수 초~수십 초
  걸리므로 **로딩 상태를 GUI에 노출**한다: `/status`에 `mouth.state(ready|loading)` 필드 추가 +
  전이 시점 WS `/events` 발행 → GUI는 스피너 표시·톤/음색 컨트롤 비활성화. (2026.07.10 합의 —
  톤(레퍼런스) 교체는 매 턴 인자라 로드 시간이 없어 해당 없음. 다중 가중치 프리로드는
  inference_webui가 프로세스당 1세트 싱글턴 + 베이스 모델 중복이라 D8(GPU) 이후 재검토.)
- 트레이 상주(pystray)·Tauri 래핑·라이트 테마 — 써보고 아쉬우면.
