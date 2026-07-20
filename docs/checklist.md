# 남은 작업 체크리스트

> 최초 작성: 2026.07.17. **로드맵의 단일 출처는 [design/plan.md](./design/plan.md)** — 이 문서는 거기서
> "아직 안 된 것"만 추린 **실행 뷰**다. 항목이 끝나면 체크하고, 근거·결정은 커밋 본문과
> [progress.md](./progress.md)에 남긴다(이 파일엔 상세를 쓰지 않는다).
>
> 진행 중 배정: **A = 사용자 직접(실기)** / **B1 = 워크트리 세션**
>
> 섹션 순서는 알파벳이 아니라 **우선순위** — E(데몬 기동 정상화)가 지금 진행 중이라 A 다음에 둔다.
> 기존 A~D의 문자는 progress.md·커밋이 참조하므로 재배열하지 않는다.

---

## A. 미검증 잔여 — 코드는 있고 실물엔 안 붙음

마이크·스피커 앞에서만 되는 작업. 이 묶음을 끝내면 "배선은 됐는데 실기 확인이 없다"는 부채가 사라진다.

- [x] **A1. 음색 가중치 핫스왑 실청취 왕복 1회** — 통과(2026.07.18). set_weights 경로 실측
      **0.96~1.65s**(base ckpt 방향이 느림), example_jp zero-shot 음색 "뚜렷하게 바뀜" 청취 확인.
      `aris_base↔example_jp`가 레퍼런스 교체만 타는 것도 로그로 실측 확증. 상세: progress.md "A 실기 검증 세션".
- [x] **A2. Stage 15 실기 E2E 통합** — ①5노드 점등 ②MODE_CHANGED 라이브 ③GUI kill 무영향 통과.
      ④취침창 런타임 변경은 재기동 후 config로 복귀(영속 안 됨) — gui.md:121 설계대로.
      **미결(백로그 후보): GUI 창 변경 영구화하려면 mode_state 영속/config write-back 필요.** 상세: progress.md.
- [ ] **A3. 음성으로 먼저 말 걸기 E2E** — **보류(2026.07.18).** Gemini 아니라 선제 발화가 2층 타이밍
      게이트까지만 실질 구현이고 3층 `pick_topic`이 `topic_feed`(D13, 빈 더미) 없어 LLM 요청문이
      플레이스홀더라 E2E 실질 없음. **B4(D13 관심사 피드) 구현 후 재개.**
- [x] **A4. GUI 로딩 표시** — **백로그 강등(2026.07.18).** A1 실측 무반응 구간 0.96~1.65s < 2s → 시급성
      낮음. feat PR 불요. 필요 시 아래 D 백로그에서 재소환.
- [x] **A5. 페르소나 게이팅 실기 확인(E3)** — **통과(2026.07.19).** 확인 9항목 전부 기대대로:
      텍스트 모드 전 카드 활성(함정 미발생) / supertonic 세션에서 나비 외 전부 회색 /
      gptsovits 세션에서 aris·aris_base·example_jp 활성 / 임시 카드로 ②④-a 회색·④-b는
      페르소나 정상 + 그 칩만 회색 / 회색 클릭 시 사유 토스트(`disabled` 미사용 근거 확인).
      **게이팅 로직 검증 완료 — 남은 건 표시 결함뿐(→ E8).**
      재현용 임시 카드 `personas/zz_{no_ckpt,no_ref,partial_tone}.yaml` 유지 중(gitignore) —
      E8 재확인에 썼고, **E6-2(preflight) 검증까지 재활용한 뒤** `rm personas/zz_*.yaml`.

## E. 데몬 기동 정상화 — 진행 중 (2026.07.19 시작, A·B보다 우선)

`--voice` 부팅이 어떤 카드로도 죽던 버그에서 출발한 묶음. **E1·E2·E4·E3·E8 머지 완료 + A5 실기 통과.**
남은 순서: **E6 → E7 → E5**. E6는 설계 확정(2026.07.20)으로 PR 4개(E6-1~4)로 쪼개져 있다 —
기존 "스크립트 기본값이 실사용으로 자리잡은 뒤" 유보는 **해소**됐다(머신 전용값 분리가 E6-3에
포함돼 그 대기가 불필요해짐).

- [x] **E1. 벤더 해석을 카드 번들로 이관** — **머지 완료(PR #24)**.
      config 기본 supertonic + 카드 gptsovits kwarg 무조건 주입 → `SupertonicMouth(gpt_ckpt=…)`
      TypeError. 기본 카드 navi.yaml도 해당돼 **보편적 실패**였다. `--mouth` 불요.
      `os._exit(0)`의 traceback 은폐도 함께 수정(실패 시 exit 1). 241 tests green.
- [x] **E2. 표준 실행 스크립트 `scripts/run_navi.ps1`** — **머지 완료(PR #25)**. venv 선택·필수
      인자·cwd 고정을 한 곳에. 무인자 = 음성 + 웨이크워드 + Claude brain. GUI 대기 화면 안내도 정정.
      코드리뷰 반영: gui를 API 키 검사에서 제외 / stop은 있는 venv 사용 / 환경변수 키 인정 /
      소수점 로케일 / 자동 변수 섀도잉. **다음은 E4 → E3.**
- [x] **E4. 카드 지정 자산(ckpt·레퍼런스 wav) 존재 검사** — **머지 완료(PR #26)**. 카드가 가리키는
      파일이 없을 때 torch의 날것 에러·첫 발화 폭사 대신 읽을 수 있는 에러로. 검사는 전부
      **상태 변경 이전**(set_weights의 ckpt 대입 전 / _ensure_engine의 chdir·env 오염 전 /
      swap_persona의 set_card 전) — "실패하면 아무것도 안 바뀜"이 성립한다. `persona.missing_assets()`가
      E3의 데이터 소스(파일 시스템만 봄 — 엔진·torch 불요). 빈 ckpt=base 의도라 통과, 부팅 시
      wav 부재는 warning만(E1의 "카드 하나가 데몬을 벽돌로" 재발 방지). 253 tests green.
- [x] **E3. 페르소나 전환 가능 여부 게이팅(GUI 비활성화)** — **머지 완료(PR #27)**. 차단 조건 4개
      (①벤더 불일치 ②ckpt 부재 ③voice 섹션 없음 ④-a 기본 톤 wav 부재 / ④-b는 그 칩만) 확정대로
      구현. **①③이 여태 뚫려 있었다** — 둘 다 `vendor_voice=None`으로 귀결돼 E4 검사를 통과했고,
      `test_persona_swap_without_voice_section_keeps_voice`가 그 반쪽 교체를 **계약으로 박아두고
      있어** 새 결정에 맞게 뒤집었다. `SwapRuntime.availability()` 하나로 조회(`/personas`의
      `available`·`reason`)와 실행(422)이 같은 판정을 쓴다 — GUI 회색은 안내, 방어는 데몬 소유.
      사유엔 `-Persona` 재기동 해법 포함. 259 tests green. 설계 기록 → [gui.md](./design/gui.md) PR② 개정.
      **툴팁·회색 렌더링 실기 미확인 → A5.**
- [ ] **E6. GUI 대기 화면에서 데몬 분리 기동(실행 버튼)** — **설계 확정(2026.07.20), 구현 대기.**
      상세 → [gui.md](./design/gui.md) "E6 — 클론 가능성을 고려한 실행 버튼".
      **코드로 재확인된 사실 3개:** ① 무인자 실행(voice+wakeword+anthropic)은 유지보수자 전용 —
      프레시 클론은 즉사 ② **데몬엔 stdin 입력 경로가 없다** — 마이크·STT는 `if args.wakeword:`
      안에서만 생성([daemon.py:642](../navi/daemon.py:642)), `console()`은 출력 전용 버스 구독자라
      웨이크워드 없는 기동은 대화가 아니라 점검용 골격(→ D17) ③ **웨이크워드 모델이 없으면 데몬은
      죽는다**([daemon.py:647-651](../navi/daemon.py:647) print 후 return) — detached+DEVNULL이면 그
      print가 사라져 GUI 무한 대기. `config.yaml:45` 주석은 stale.
      **확정 설계:** 축은 **엔진 하나**(`gptsovits`/`supertonic`/`목소리 없이(점검용)`) — 부팅에서
      되돌릴 수 없는 유일한 선택이 엔진이라서(톤·페르소나는 런타임 교체 가능). mode 축 소멸.
      **`-Mouth` 금지, `-Persona`로 전달** — `--mouth`는 [config.py:276](../navi/config.py:276)에서
      카드 해석을 건너뛰어 "나비 인격 + 아리스 목소리"(E3가 막은 반쪽 정체성)를 부팅 시점에
      되살린다. preflight가 `엔진 → 부팅 가능한 카드`를 매핑한다.
      **부품 4개(번호 순서 = 구현 순서. 위 최상위 묶음 A~E와 헷갈리지 않게 `E6-n`으로 쓴다):**
      **E6-1** 웨이크워드 번들(`navi_ko.onnx` 164,989 B, 자체 학습이라 라이선스 무관 →
      `assets/wakeword/`로 커밋. 웨이크워드는 제품 정체성 — "하이 빅스비" 위상. 영어 폴백·
      `ready` early-return 수정이 불필요해짐. **gitignore 예외는 불필요** — `assets/`는 무시
      목록에 없다) · **E6-2** `python -m navi.preflight [--json]`
      (순수 판정, `select_vendor`·`missing_assets`·`WakeWordConfig.ready` 재사용 + venv·brain키 추가,
      클론 doctor 겸용) · **E6-3** `config.local.yaml` 오버레이(⚠ 마이크 energy VAD는 **config 경로가
      아예 없음** — 센티널화만으론 값이 사라진다, config 키 신설 + CLI 미지정 시 폴백 필수) ·
      **E6-4** `_Api.launch(engine)` detached+DEVNULL + `wait_for_daemon` 타임아웃 ~90s +
      `logs/navi.log` 안내.
      **클론 경로 완결:** clone → `setup_voice_env.ps1` → supertonic 음성 + "나비야". 남는 장벽은
      `.venv-voice` 하나. **덤:** 기존 `/shutdown` 활용 → 끄기→대기화면→다른 엔진→실행으로
      "재시작 버튼" 없이 엔진 전환.
      **구현 순서: E6-1 → E6-2 → E6-3 → E6-4** (E6-1이 가장 작고 독립적).
      **E6-1 구현 완료(2026.07.20, `feat/wakeword-bundle`)** — 262 tests green + 클론 시뮬
      (`git archive` 트리에서 `wakeword.ready`=True) + **실기 통과**("나비야" 감지).
      다음은 **E6-2(preflight)**.
      **양보 불가:** GUI가 데몬을 소유 금지(`DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP`, DEVNULL,
      핸들 즉시 폐기 — `wait()`·파이프 금지) / **새 폴링 추가 금지**(기존 `wait_for_daemon`이 유일한
      피드백 경로) / venv·인자 지식을 GUI에 두지 않는다 / 중복 클릭은 `acquire_pidfile`이 거부.
      비목표: 정지 버튼·인자 선택 UI.
- [x] **E8. GUI 표시 결함 2건 (360px 창을 못 견딤)** — **머지 완료(PR #28)**. A5 실기에서
      관측(2026.07.19). 게이팅 로직은 정상이고 **보여주는 쪽만** 문제였다. 커밋 `5b00a07`(토스트
      잘림·드롭다운 넘침) + `8c62e6f`(페르소나 선택 시 항상 메뉴 닫기).
      임시 카드 `zz_*.yaml`은 **지우지 않는다** — E6-2(preflight) 검증에 재활용.
      ① **토스트 잘림** — 사유에 해법까지 담기로 한 게 E3 결정인데(`-Persona`로 재기동) 창이
      360px 고정이라 한 줄에 안 들어가 **해법이 잘려 나간다**. 툴팁에 해법을 넣은 의미가
      사라지므로 이건 표시 버그가 아니라 결정의 훼손이다. → 여러 줄 허용 + 폭·지속시간 재조정.
      ② **페르소나 드롭다운 넘침** — 카드 8장에서 목록이 창 밖으로 밀린다. 임시 카드 탓이지만
      실사용에서도 페르소나가 늘면 재발 → `max-height` + 스크롤.
      둘 다 CSS 수준이라 `fix(gui)` 한 덩어리. 재확인은 A5의 임시 카드가 남아 있는 동안이 싸다.
- [ ] **E7. 벤더 지식 분산 정리** — PR #24 코드리뷰(2026.07.19)에서 나온 구조 지적 3건. 셋 다 뿌리가
      같다: **"이 벤더가 무슨 kwarg를 받는가"라는 지식이 여러 층에 흩어져 있다.**
      ① `"gptsovits는 특별하다"`가 세 곳에 각각 인코딩 — [voice.py:40](../navi/persona/voice.py:40)
      `_PATH_VOICE_ID_VENDORS`, [voice.py:43](../navi/persona/voice.py:43) `_CKPT_VENDORS`,
      [config.py:159](../navi/config.py:159) `if vendor == "gptsovits"`. 두 번째 ckpt 벤더를 추가할 때
      하나를 빠뜨리면 **부팅은 되는데 음색만 조용히 틀린** 상태가 된다.
      ② mouth 어댑터 생성자 시그니처 지식이 persona 모듈에 산다 — 그 지식의 자연스러운 주인은
      `navi/mouth/`(벤더별 kwarg 레지스트리)다. 세 번째 벤더가 고유 kwarg를 들고 오면 또 특수 케이스를
      추가해야 하고 일반화되지 않는다.
      ③ `create_mouth` 자체는 여전히 무방비 — `persona.mouth_options`를 안 거치는 호출자(테스트·스크립트·
      향후 SwapRuntime 경로)가 옵션을 직접 조립하면 동일 TypeError가 재발한다. 이 PR의
      `test_supertonic_rejects_gptsovits_kwargs`가 그 무방비함을 **의도된 계약으로 고정**해뒀다.
      → 해법 방향: 벤더별 kwarg 스키마를 mouth 층에 한 곳으로 모으고 persona/config는 그걸 참조만.
      **E3·E4보다 급하지 않다**(현재 벤더 2개에선 실해 없음). 세 번째 TTS 벤더를 붙일 때가 실질 기한.
- [ ] **E5. 언어를 페르소나 속성으로 올릴지 검토** — 지금 `gen_lang`·`ref_lang`이 `voice.<vendor>`
      안에 있어 **voice 섹션 없는 카드는 자기 언어를 선언할 방법이 없다**. example_kr은 한국어
      페르소나인데 그 사실이 카드 어디에도 없어서 코드가 판단할 데이터가 아예 없다(E3 ③을
      "언어 판단 불가"가 아닌 다른 근거로 세운 이유). 언어는 가중치의 속성이라기보다
      **"이 캐릭터가 무슨 말을 쓰는가"**라 페르소나 층에 가깝다. 다만 D3의 "언어는 가중치와
      한 몸(핫스왑 시 함께 교체)" 규칙과 충돌 가능 — **E3·E4 끝난 뒤 별도 검토**, 지금 결정 안 함.

## B. Phase 3 본류 — 순서 4 튜닝 → 순서 5

- [ ] **B1. 조건 산식 재구상(변동확률)** — `should_initiate`를 고정 간격 bool에서 **tick 기반 hazard**
      (시간 경과에 따라 발화 확률↑)로 교체. 조건이 bool 뒤에 격리돼 있어 게이트·대기·로깅은 그대로
      재사용된다. 교체 시 정리할 것: jitter 파라미터 위치(내부 RNG로 옮기면 붕 뜸)·tick 빈도 정규화·
      테스트 결정성(seed 주입). 대상: [navi/heartbeat/timing.py](../navi/heartbeat/timing.py).
- [ ] **B2. interaction_log 축적 → 가중치 튜닝** — B1의 산식에 실데이터를 먹인다. Phase 3 완료 기준
      ("선제 발화 응답률 > 무시율")은 로그 없이 닫을 수 없다. **B1 먼저, 그 위에서 튜닝**(진행 원칙 2).
- [ ] **B3. 순서 5 — 감정 태그 → 레퍼런스 전환** — Brain이 감정 태그를 출력, Mouth가 감정별 레퍼런스
      오디오 선택. D3의 "톤=레퍼런스 제어"를 처음 실제로 쓰는 작업. D9(친밀도) 없이 얹는 1차 감정선.
- [ ] **B4. D13(관심사 피드)** — `pick_topic`의 `topic_feed`가 현재 빈 리스트 더미. Phase 3 완료 기준 포함.
- [ ] **B5. D11(스케줄 동기화)** — 캘린더 API vs 컴패니언 앱. GUI가 이미 있어 후자로 기울 수 있음.

## C. 동결 — 열쇠는 D8 하나

- [ ] **C1. D8(GPU·상시기기) 확보** — E2E 9.8s 중 TTS 합성이 63%(Stage 12 실측). STT·Brain이 0이어도
      "명령 답변 ≤3s" 구조적 불가 → 잔여 속도 작업은 전부 여기로 수렴.
- [ ] **C2. D2(스트리밍 STT 벤더 선정)** — C1 뒤. 한국어 CER 기준 3사 비교.
- [ ] **C3. AEC · barge-in** — C1 뒤.

## D. 백로그 — 건드리지 않음

- **D17(데몬 텍스트 대화 경로)** — 신설 2026.07.20(E6 설계 중 발견). 데몬엔 stdin 입력 경로가 없어
  웨이크워드 없는 기동은 대화가 아니라 점검용 골격이다. **1단계:** `EventKind.RESPONSE`(턴당 1건,
  완성 텍스트 — 토큰 단위는 버스 범람) 발행 + 기존 로그 다이얼로그에 표시, **본 레이아웃 무변경**.
  음성 모드에서도 "실제 발화 문자열 확인" 진단 가치가 있어 텍스트 모드 전용으로 좁히지 않는다.
  **2단계:** `POST /say` — STT만 건너뛰고 `check_gate`→`_run_turn` **동일 경로**(게이트 우회 금지) +
  채팅 입력창. 착륙 시 E6 런처의 "목소리 없이(점검용)" 라벨 개정. 상세 → architecture.md D17.
- 웨이크워드 **영어 폴백·`ready` early-return 잠재 함정** — E6 부품 D(모델 커밋)로 실해가 사라져
  강등. 모델을 다시 gitignore로 되돌리면 재소환.
- TTS **엔진** 핫스왑(gptsovits↔타 어댑터): in-process 로드가 sys.path·CWD·env를 전역 오염시켜
  프로세스 내 교체 불가. 엔진은 D3 확정이라 실익 낮음.
- Phase 4(장기기억·사실 충돌·D9 친밀도·페르소나 전환 히스테리시스)
- Phase 5(D10 정서 안전·고장 모드·D12 턴테이킹 튜닝)
