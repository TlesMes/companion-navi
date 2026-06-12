# 진행 기록 (Progress Log)

> 새 세션·새 작업자가 맥락을 복원하는 진입점. 최신 Phase가 위.
> 한 줄 요지는 [CLAUDE.md 현재 상태](./CLAUDE.md), 상세 근거는 각 커밋 본문에 있다.

---

## Phase 2 — 음성화 (시작 전)

**진입 관문 (코드보다 결정이 먼저):**
- **D3 — TTS 음색 (최중요)**: 같은 한국어 대사로 수퍼톤 vs Cartesia(+Typecast 등) 청취 비교. 스펙이 아니라 귀로 결정.
- **D2 — STT**: 리턴제로(VITO) vs Clova vs Deepgram을 실제 발화(혼잣말·구어체)로 비교. 기준: 한국어 CER ≫ 스트리밍 레이턴시 > 가격.

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
