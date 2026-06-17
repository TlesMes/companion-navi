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
| [docs/design/plan.md](./docs/design/plan.md) | 로드맵(Phase 0–5)·원칙 전체·기술 스택과 선정 이유 | **전체 진행 상황·방향을 파악해야 할 때** |
| [docs/design/architecture.md](./docs/design/architecture.md) | 모듈 인터페이스(계약)·데이터 모델·모드 상태머신·보류 결정 D1~D12 | 구현 작업 시 |
| [docs/design/vendor-cost.md](./docs/design/vendor-cost.md) | 벤더 가격 비교·원가 시뮬레이션·안전 규제 조사 | 벤더 결정·원가 검토 시 |
| [docs/research/tts-pivot.md](./docs/research/tts-pivot.md) | Supertonic→Zero-shot 보이스 클로닝 전환 및 아키텍처 검토 | D3 배경 파악 시 |

## 커밋 컨벤션 (Conventional Commits)

```
type(scope): 제목 (한국어, 50자 내)

본문: 무엇을 했는지보다 왜 했는지. 결정 사항은 D번호 참조.
```

- **type:** `feat`(기능) `fix`(버그) `docs`(문서) `refactor` `test` `chore`(설정·잡일) `research`(조사·평가·벤치마크)
- **scope(선택):** 모듈명 소문자 — `ear` `turntaking` `stt` `heartbeat` `memory` `conductor` `brain` `mouth` `schedule` `core` / 문서는 `plan` `arch` `cost`
- 예: `docs(arch): 턴테이킹 모듈 계약 추가 (D4 결정 반영)`, `feat(memory): 단기기억 SQLite 적재 구현`

## PR 규칙 (하이브리드 — 2026.06.13 합의)

- **PR 1개 = 독립적으로 검증 가능한 작업 한 덩어리** — 모듈 1개 / D번호 결정 1개 / 거동을 바꾸는 튜닝 1건
- **PR 필수:** `feat` `fix` `refactor` `research`(결정 반영) / **main 직커밋 허용:** `docs` `chore` 단독 변경
- 크기: 커밋 1~6개, diff ~300줄 내(리뷰 10분 분량). 넘으면 쪼갠다
- 머지 조건: 테스트 green + PR 본문에 이 단위의 **검증 방법**과 관련 D번호·완료 기준
- 브랜치명: `type/scope-요지` (예: `feat/ear-vad`, `research/d3-tts`)

## 현재 상태 (2026.06.17)

Phase 0(기획·설계) 완료 → **Phase 1(텍스트 뼈대) 완료** → **Phase 2(음성화) D3 진행 중.**
Phase 1 산출물: Conductor + Brain 어댑터(Gemini 기본·Anthropic·Echo) + 단기기억(SQLite) + 캐릭터 카드([personas/navi.yaml](./personas/navi.yaml)) — CLI 텍스트 대화(`python -m navi.cli`).
D3(TTS 음색): GPT-SoVITS fine-tune(아리스 168클립, Colab T4, v2) 청취 완료 → **유력안 확정**. 음색=가중치 안정, 톤=레퍼런스 제어. WSL CPU 로컬 추론 재현 완료(RTF~1.4). 상세 → [docs/progress.md](./docs/progress.md).