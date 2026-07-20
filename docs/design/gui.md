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
  `ref_lang`/`gen_lang`(가중치와 한 몸 — 핫스왑 시 함께 교체, 빈 값이면 현재 유지)
  + `tones:`(첫 항목이 기본 톤). **부팅 시에도 번들 우선** — 카드에 활성 벤더 섹션이 있으면
  mouth options(ckpt류)·초기 VoiceProfile을 카드에서 만든다.
- **`TurnPipeline.set_voice(profile)` / `swap_weights(...)` / `current_voice` / `is_playing()`**
  — 다음 턴부터 적용(턴 중 교체 없음). `swap_weights`는 모델 로드를 `asyncio.to_thread`로
  넘기고 턴 락으로 발화와 상호배제하며, is_playing은 mouth 위임 + 그 락 상태를 함께 표면화한다.
- **`Conductor.set_card(card)`** + 데몬 `run_turn`의 `card.character` 클로저 캡처를 동적 참조로.
- **`SwapRuntime` 파사드**(navi/control/runtime.py 신설) — conductor·pipeline(텍스트 모드면
  None)·personas 디렉토리를 들고 스캔·교체·409 판정 담당, `create_app(swap=...)` 옵셔널
  주입(미주입 503 — DaemonCore 비대화 방지, PR ① 테스트 보존). **`persona_id`(카드 주인)와
  `voice_persona_id`(톤 세트 주인)를 분리 추적** — 가중치가 다르면 새 가중치를 올린 뒤
  (핫스왑) 톤을 걸어 두 id를 다시 합친다. 핫스왑 미지원 엔진에서만 분열이 남는다(카드만
  교체, `voice_swapped: false`).
  - **개정(2026.07.19, E3):** "카드만 교체"는 **금지**로 바뀌었다 — 목소리가 카드를 따라가지
    못하는 조합은 `SwapRuntime.availability()`가 판정해 **422로 막는다**(차단 조건 4개:
    ①벤더 불일치 ②ckpt 부재 ③voice 섹션 없음 ④-a 기본 톤 wav 부재. ④-b 그 외 톤은 그 칩만).
    인격만 바뀌고 목소리는 그대로인 반쪽 정체성이 연속성 원칙 위반이라서다. 남는
    `voice_swapped: false`는 핫스왑 미지원(프리셋 기반) 엔진 하나뿐이다.
    **조회(`GET /personas`의 `available`·`reason`)와 실행이 같은 함수를 쓴다** — 두 벌이면
    회색이 아닌데 막히는 어긋남이 생긴다. 평가 시점은 응답 생성 시(캐시 없음 — 세션 중
    ckpt를 갖추면 다음 요청에 반영). 텍스트 모드는 목소리를 안 건드리므로 전부 허용.
    GUI는 판정을 그리기만 한다(비활성 + 사유 툴팁, 사유엔 `-Persona` 재기동 해법 포함).
- API: 위 표의 /voices·/voice·/personas·/persona. **/persona도 재생 중 409**(voice 교체 내포).
- 톤 칩 최종형 메모: Phase 3-5(감정 태그)에서 STAGE tts payload에 tone을 실어 **자동 점등**,
  클릭은 **핀(고정) 오버라이드**로 승격 — 이번 PR은 수동 선택(=핀만 있는 상태)으로 시작.
- 음색(fine-tune 가중치) 교체 API는 **구현됨** — `change_*_weights` 재호출, 재생 중 409 +
  로딩 중 턴 차단(턴 락). 엔진 핫스왑은 안 함(아래 결정) — 같은 엔진 내 가중치 교체일 뿐.
- **공개 예시 카드([personas/example.yaml](../../personas/example.yaml))**: aris.yaml은
  저작권·gitignore(로컬 전용)라 오프라인 E2E·테스트를 커밋 자산으로 재현할 수 없었음 —
  fine-tune ckpt 없이 **gptsovits base(zero-shot)**로 도는 중립 예시 카드를 공개 자산으로 추가
  (`!personas/example.yaml` 예외). navi↔example 전환이 커밋 자산만으로 재현된다.
- **검증**: fake mouth 유닛(ref_text 우선순위·재생 중 409·번들 로더) · echo E2E — /voice 교체 후
  다음 턴 VoiceProfile 반영·/persona 교체(navi↔example) 후 시스템 프롬프트 교체 확인. 실기 E2E는
  PR ③ 이후 Stage 15 전체를 한 번에(2026.07.10 합의) — **gptsovits base zero-shot 실물 발화 +
  부팅 base-선택 로직(카드 ckpt 부재 시 config arisu 폴백 충돌 해소)도 여기 포함**(base s1/s2
  다운로드가 게이트라 이 PR에선 유예, gptsovits.py setup 참조).

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
    gpt_ckpt: secrets/voice_ref/....ckpt    # 루트 기준 상대경로 — 교체 시 런타임 핫스왑
    sovits_ckpt: secrets/voice_ref/....pth
    ref_lang: ko      # 가중치와 함께 교체 (빈 값 = 현재 유지)
    gen_lang: ko
    tones:            # 첫 항목이 기본 톤
      - { name: 기본, icon: mood-smile, voice_id: secrets/voice_ref/base.wav, ref_text: "..." }
      - { name: 신남, icon: mood-happy, voice_id: secrets/voice_ref/happy.wav, ref_text: "..." }
```

## E6 — 클론 가능성을 고려한 실행 버튼 (2026.07.20 설계 확정)

대기 화면([navi/gui/__main__.py](../../navi/gui/__main__.py) `_WAITING_HTML`)은 지금 기동 명령을
텍스트로 **안내만** 한다 — 사용자가 터미널에서 직접 쳐야 한다. E6는 여기에 실행 버튼을 붙여
GUI가 데몬을 (소유하지 않고) 띄운다. repo가 public이므로 **클론 가능성**을 같은 설계로 푼다.

### 코드로 재확인된 사실 3개

1. **무인자 실행은 유지보수자 전용.** `run_navi.ps1` 무인자 = voice + wakeword + anthropic인데
   프레시 클론엔 `.venv-voice`도 `secrets/`도 없어 즉사한다. 버튼이 무인자를 그대로 실행하면
   클론한 사람은 100% 실패한다.
2. **데몬엔 stdin 입력 경로가 없다.** 마이크·STT는 `if args.wakeword:` 안에서만 생성되고
   ([daemon.py:642](../../navi/daemon.py:642)), `console()`은 버스 구독자라 **출력 전용**이다
   ([daemon.py:727](../../navi/daemon.py:727)). 즉 웨이크워드 없는 기동은 대화가 아니라
   **tick만 도는 점검용 골격**이다(→ D17).
3. **웨이크워드 모델이 없으면 데몬은 뜨는 게 아니라 죽는다.** `config.wakeword.ready`가 거짓이면
   print 후 `return`([daemon.py:647-651](../../navi/daemon.py:647)). detached+DEVNULL이면 그 print가
   사라져 **GUI가 무한 대기**한다. `config.yaml:45`의 "모델 없이도 데몬은 뜬다" 주석은 stale.

### 확정 설계

**축은 엔진 하나** — `[gptsovits] [supertonic] [목소리 없이(점검용)]`. 부팅에서 **되돌릴 수 없는
유일한 선택이 엔진**이기 때문이다(톤·페르소나는 런타임 교체 가능, 엔진 핫스왑은 안 함 —
아래 보류 결정). mode 축은 소멸한다.

**`-Mouth` 금지, `-Persona`로 전달.** `--mouth`를 주면 [config.py:276](../../navi/config.py:276)이
카드 해석(`_vendor_from_card`)을 **건너뛰어** config의 벤더 섹션이 이기고, "나비 인격 + 아리스
목소리"라는 **E3가 막은 반쪽 정체성이 부팅 시점에 되살아난다.** 그래서 런처는 엔진을 직접
넘기지 않고 **preflight가 `엔진 → 부팅 가능한 카드`를 매핑**해 `-Persona`로 전달한다.

> 부품 번호는 **E6-n** — checklist.md의 최상위 묶음 A~E와 헷갈리지 않게. 번호 순서 = 구현 순서.

**E6-1. 웨이크워드 번들** — `secrets/navi_ko.onnx`(**164,989 B ≈ 161 KB**, 자체 학습이라 라이선스
무관)를 `assets/wakeword/`로 옮겨 **커밋**한다. 웨이크워드는 부속이 아니라 **제품 정체성**이다
("하이 빅스비" 위상) — 클론한 사람이 "나비야"를 못 쓰면 이 제품이 아니다. 부수 효과로 영어
폴백·`ready` early-return 수정이 **불필요해진다**(잠재 함정은 백로그로). `.venv-voice`는 여전히 필요.

**E6-2. preflight** — `python -m navi.preflight [--json]`. 파일 시스템·환경변수만 읽는
판정(torch·포트·pid 불요)이라 데몬 없이 혼자 돈다.
데몬이 실제 부팅에 쓰는 함수를 재사용해 판정 이중화(E3)를 안 깬다:
[`select_vendor`](../../navi/persona/voice.py:176)·[`missing_assets`](../../navi/persona/voice.py:147)·
[`WakeWordConfig.ready`](../../navi/config.py:69) + **venv·brain 키 판정 추가**.
**환경·모델 doctor 겸용** — 설치 환경(venv·API 키)과 모델 자산(웨이크워드·TTS 가중치)이
갖춰졌는지 보는 진단(`flutter doctor`류). 클론한 사람만이 아니라 유지보수자도 쓴다.

**E6-3. `config.local.yaml` 오버레이**(gitignore) — `-VadThreshold 50` 같은 머신 전용값을 커밋
파일에서 분리. `run_navi.ps1`의 `-VadThreshold` 기본값은 센티널(미전달)로.
⚠ **선행 조건:** 마이크 energy VAD 임계는 **지금 config 경로가 아예 없다** — `--vad-threshold`
CLI만 읽고([daemon.py:657](../../navi/daemon.py:657)), `config.wakeword.vad_threshold`는
openWakeWord 내부 Silero VAD라 **다른 손잡이**다([wakeword.py:209](../../navi/ear/wakeword.py:209)).
따라서 E6-3은 "기본값을 센티널로" 만으로 부족하고 **config 키 신설 + CLI 미지정 시 config 폴백**을
함께 해야 한다. 안 그러면 값이 옮겨지는 게 아니라 사라진다(VAD 꺼짐).

**E6-4. 대기 화면(제시) + 실행 버튼** — preflight 결과를 **그리고**, 고른 것을 **띄운다**.
제시와 실행을 쪼개지 않는 이유: "화면은 그려지는데 눌러도 아무 일 없음"은 독립 검증 가치가 없다.

- **제시** — 엔진 칩 3개(`gptsovits`/`supertonic`/`목소리 없이(점검용)`). 각 칩의 활성 여부는
  preflight의 `bootable`, 회색 칩을 누르면 `reason` 토스트(E3·E8과 같은 규약 — `disabled`를
  쓰지 않는다: 클릭이 죽으면 사유를 볼 기회가 사라진다). 카드별 `warnings`도 함께 보인다
  ("뜨긴 뜨는데 그 톤으로 말하면 실패"를 미리 알린다).
- **카드는 고르지 않는다(2026.07.20 결정)** — 엔진만 고르고, 카드는 preflight가 그 엔진에
  지정한 것(config 기본 카드가 그 엔진이면 그것, 아니면 부팅 가능한 첫 카드)을 쓴다.
  근거: **부팅에서 되돌릴 수 없는 선택만 노출한다**는 원칙. 카드는 뜬 뒤 기존 페르소나
  그리드로 교체되므로 되돌릴 수 있고, 대기 화면에 카드까지 늘어놓으면 "인자 선택 UI"가 된다.
- **실행** — `_Api.launch(engine)`:
```python
DETACHED_PROCESS, CREATE_NEW_PROCESS_GROUP = 0x00000008, 0x00000200
subprocess.Popen(
    # 카드는 preflight가 그 엔진에 지정한 것 — GUI가 고르지 않는다.
    # -Mouth는 쓰지 않는다(위 "확정 설계" 참조 — 부팅 시점 반쪽 정체성 방지).
    ["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass",
     "-File", str(script), "-Persona", persona],
    creationflags=DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP,
    stdin=DEVNULL, stdout=DEVNULL, stderr=DEVNULL, cwd=root, close_fds=True,
)  # Popen 즉시 폐기 — .wait()·.communicate() 없음(파이프가 차면 데몬이 막힌다)
```
**새 폴러 금지**(기존 `wait_for_daemon` 인계) + **타임아웃 ~90s** + 초과 시 `logs/navi.log` 안내.
타임아웃이 사실 3의 무한 대기를 메우는 안전망이다.

**클론 경로 완결:** `clone` → `setup_voice_env.ps1` → **supertonic 음성 + "나비야"**가 동작한다.
남는 장벽은 `.venv-voice` 하나로 줄어든다.

**덤 — 엔진 전환:** 기존 `POST /shutdown`을 쓰면 *끄기 → 대기화면 → 다른 엔진 → 실행*이 되어
**"재시작 버튼"을 새로 만들지 않고** 엔진을 바꿀 수 있다.

### E6 양보 불가 제약 대조

| 제약 | 지키는 법 |
| :-- | :-- |
| detached·DEVNULL·핸들 폐기 | E6-4 Popen 그대로 |
| 새 폴링 금지 | 기존 `wait_for_daemon` 인계, preflight는 버튼당 1회 in-process |
| GUI는 스크립트 파일명만 | venv·인자는 `run_navi.ps1` 소유. GUI는 엔진 라벨만 알고, 카드 매핑은 preflight |
| 판정=데몬 소유(E3) | preflight가 `select_vendor`·`missing_assets`·`ready` 재사용 + `-Mouth` 금지로 부팅 시점 반쪽 정체성 차단 |
| 비목표: 인자 선택 UI | **엔진 축만** 노출한다(칩 3개) — 카드·mode·brain·vad는 대기 화면에 없다. 되돌릴 수 없는 선택만 묻는다는 뜻이지 "버튼이 물리적으로 1개"라는 뜻이 아니다(v1 표현 폐기) |

### 구현 순서 (PR 4개, E8 뒤)

**E6-1 → E6-2 → E6-3 → E6-4** — E6-1이 가장 작고 독립적이라 먼저.

1. **E6-1 웨이크워드 번들**: `assets/wakeword/`로 이동 + config 경로 갱신 + 출처 README.
   **gitignore 예외는 불필요**(`assets/`는 무시 목록에 없음 — 2026.07.20 확인). `secrets/`
   규칙은 유지한다: vosk·porcupine 경로가 여전히 거기를 가리킨다.
   검증: 깨끗한 트리(`git archive`)에서 `wakeword.ready`가 True + 실기 "나비야" 감지.
2. **E6-2 preflight**: 읽기 전용 판정 + `--json`. 검증: 오프라인 유닛(임시 카드 `zz_*` 재활용).
3. **E6-3 config 오버레이**: 위 ⚠ 선행 조건 포함. 검증: 오버레이 없으면 기존 거동 동일 + 오버레이가 base를 이김.
4. **E6-4 실행 버튼**: E6-1~3 전제. 검증: 위 PR ③ 검증 ③"GUI kill 무영향" 유지 + 자산부족 시 부팅 전 사유 표시 + 타임아웃 동작.

## 보류·후순위

- 엔진 핫스왑(Supertonic↔GPT-SoVITS) — **안 함으로 결정(2026.07.10)**. 모델(STT·TTS·KWS)은
  시작 1회 웜업·무교체가 원칙, 런타임 교체 범위는 **가중치(음색)·톤·페르소나까지**.
  근거: 엔진 전환의 가치는 CPU 속도 우회인데 D8(GPU)로 소멸하는 한시적 문제 + 엔진 간
  목소리가 달라 연속성 원칙과 상충 + `VoiceProfile` 의미가 엔진별로 갈라져 API 계약 복잡화.
- 톤 자동 점등(감정 태그 연동) — Phase 3-5에서 이 GUI를 확장.
- 음색 가중치 교체의 **GUI 로딩 표시** — API 자체는 구현됐으나(핫스왑 PR) 표시는 아직.
  `change_*_weights`가 CPU에서 수 초~수십 초 걸리므로 **로딩 상태를 GUI에 노출**한다:
  `/status`에 `mouth.state(ready|loading)` 필드 추가 + 전이 시점 WS `/events` 발행 → GUI는
  스피너 표시·톤/음색 컨트롤 비활성화. 지금은 교체 중 요청이 409로 튕길 뿐이라 사용자에겐
  무반응처럼 보인다. (2026.07.10 합의 — 톤(레퍼런스) 교체는 매 턴 인자라 로드 시간이 없어
  해당 없음. 다중 가중치 프리로드는 inference_webui가 프로세스당 1세트 싱글턴 + 베이스 모델
  중복이라 D8(GPU) 이후 재검토.)
- 트레이 상주(pystray)·Tauri 래핑·라이트 테마 — 써보고 아쉬우면.
