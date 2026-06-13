# 진행 기록 (Progress Log)

> 새 세션·새 작업자가 맥락을 복원하는 진입점. 최신 Phase가 위.
> 한 줄 요지는 [CLAUDE.md 현재 상태](./CLAUDE.md), 상세 근거는 각 커밋 본문에 있다.

---

## Phase 2 — 음성화 (진행 중)

**어댑터 계약·stub 완료 (브랜치 `feat/voice-adapter-contracts`):**
STT/Mouth 계약(01 문서 4.3·4.8) + fake 어댑터 + 팩토리 + 테스트(`navi/stt`·`navi/mouth`·`tests/test_voice.py`).
벤더는 `_PENDING_D2`/`_PENDING_D3`로 보류 표시 — 결정 후 어댑터 한 장 끼우면 됨.

**로컬 우선으로 방향 전환 (GPU 보유 → 비용 0·벤더 비종속).** 데스크톱 = AMD RX 6600 XT(상시기기 호스트).
- **TTS 잠정:** Supertonic(`pip install supertonic`, 한국어 `ko`, CPU RTF 0.35) — 프리셋 10종(M1–5/F1–5) 중 **F1을 나비 잠정 목소리로** 채택(2026.06.13). "마음에 쏙"은 아님 → 추후 voice cloning(CosyVoice2/XTTS-v2/F5-TTS, 6초 레퍼런스) 재검토 여지.
- **STT 잠정:** faster-whisper large-v3-turbo(int8) — 한국어 CER≈0 실검증(합성→받아쓰기 왕복). CPU RTF 1.24(실시간엔 못 미침).
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
