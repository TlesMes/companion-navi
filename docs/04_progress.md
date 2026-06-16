# 진행 기록 (Progress Log)

> 새 세션·새 작업자가 맥락을 복원하는 진입점. 최신 Phase가 위.
> 한 줄 요지는 [CLAUDE.md 현재 상태](../CLAUDE.md), 상세 근거는 각 커밋 본문에 있다.

---

## Phase 2 — 음성화 (진행 중)

**현재 상태 — D3 GPT-SoVITS fine-tune 청취 완료, 유력 후보 확정 (2026.06.17):**
CosyVoice2 zero-shot(Stage 1) 후 GPT-SoVITS를 아리스 168클립으로 fine-tune(Colab T4)해 청취. **음색은 가중치가 안정적으로 담당, 톤·억양은 레퍼런스로 제어 가능 → GPT-SoVITS fine-tune을 D3 유력안으로.** 상세는 아래 Stage 2.

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

**남은 일:**
- `try_clone.py` gptsovits 분기 정본화(현 stub: `prompt_language="ko"` 하드코딩 + 구식 API 시그니처) → 받아온 ckpt로 로컬 추론.
- 로컬 WSL GPT-SoVITS 설치 + 받은 ckpt 추론(RTF·TTFA 측정은 GPU 환경 확보 후).
- (보류) 한국어 ref→출력 — 한국어 레퍼런스 음원 확보 시.

**미실행:** Stage 2(TTFA·RTF·VRAM 정량) — GPU 환경 확보 후. 현재 CPU는 속도 측정 무의미.

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
