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
| [03_master_plan.md](./03_master_plan.md) | 로드맵(Phase 0–5)·원칙 전체·기술 스택과 선정 이유 | **전체 진행 상황·방향을 파악해야 할 때** |
| [01_daemon_architecture.md](./01_daemon_architecture.md) | 모듈 인터페이스(계약)·데이터 모델·모드 상태머신·보류 결정 D1~D12 | 구현 작업 시 |
| [02_tech_research_cost.md](./02_tech_research_cost.md) | 벤더 가격 비교·원가 시뮬레이션·안전 규제 조사 | 벤더 결정·원가 검토 시 |
| [04_progress.md](./04_progress.md) | Phase별 진행 기록·구현 중 결정·운영 메모(환경 상태) | **새 세션 시작 시 맥락 복원** |

## 커밋 컨벤션 (Conventional Commits)

```
type(scope): 제목 (한국어, 50자 내)

본문: 무엇을 했는지보다 왜 했는지. 결정 사항은 D번호 참조.
```

- **type:** `feat`(기능) `fix`(버그) `docs`(문서) `refactor` `test` `chore`(설정·잡일) `research`(조사·평가·벤치마크)
- **scope(선택):** 모듈명 소문자 — `ear` `turntaking` `stt` `heartbeat` `memory` `conductor` `brain` `mouth` `schedule` `core` / 문서는 `plan` `arch` `cost`
- 예: `docs(arch): 턴테이킹 모듈 계약 추가 (D4 결정 반영)`, `feat(memory): 단기기억 SQLite 적재 구현`

## 현재 상태 (2026.06.13)

Phase 0(기획·설계) 완료 → **Phase 1(텍스트 뼈대) 완료** → Phase 2(음성화) 시작 전.
Phase 1 산출물: Conductor + Brain 어댑터(Gemini 기본·Anthropic·Echo) + 단기기억(SQLite) + 캐릭터 카드([personas/navi.yaml](./personas/navi.yaml)) — CLI 텍스트 대화(`python -m navi.cli`).
완료 기준: ① 껐다 켜도 어제 대화를 기억 — ✅ 실검증(2026.06.13, Gemini + 실사용) ② 벤더 교체에도 같은 말투 — ✅ 구조 검증(동일 카드·메시지 조립을 테스트로 고정). 잔여: ②의 실청취 비교는 Anthropic 키 확보 시.
Phase 2 관문: D3(TTS 음색) 청취 비교 — 수퍼톤 vs Cartesia, D2(STT) 한국어 3사 비교.