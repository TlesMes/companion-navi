# companion-navi — "Hey! Listen!"

반려 AI 오케스트레이터 데몬. **두뇌(LLM)는 API로 빌리고, 살아있음·기억·인격은 데몬이 만든다.**
집 안 상시 기기에서 도는 Python 데몬 + 마이크/스피커.

## 설계 원칙

- 인격·기억·목소리의 연속성은 100% 데몬 소유 — 벤더 종속 금지(모든 외부 API는 어댑터 뒤로)
- "언제 말할까"는 결정론적 규칙, "무엇을 말할까"만 모델 — 수면/DND 게이트는 LLM에 맡기지 않음
- 사용자 오버라이드는 항상 자동 판단을 이김
- 전 구간 스트리밍, 첫 오디오 ~1초 목표
- 한국어 퍼스트

## 빠른 시작

```bash
pip install -e .          # 텍스트 뼈대(가벼움)
cp .env.example .env      # API 키 입력 (GEMINI_API_KEY 등)

python -m navi.cli                    # CLI 텍스트 대화 (기본 페르소나 navi)
python -m navi.cli --persona aris     # 페르소나 교체
python -m navi.cli --voice            # 음성 답변 (음성 부품 필요, 아래 참조)
```

음성(STT/TTS)은 무거운 로컬 엔진이라 별도 extra로 분리되어 있다:

```bash
pip install -e ".[voice]"
```

## 문서

| 문서 | 내용 |
| :-- | :-- |
| [docs/progress.md](./docs/progress.md) | Phase별 진행 기록·구현 결정·운영 메모 |
| [docs/design/plan.md](./docs/design/plan.md) | 로드맵(Phase 0–5)·원칙·기술 스택 |
| [docs/design/architecture.md](./docs/design/architecture.md) | 모듈 계약·데이터 모델·모드 상태머신·보류 결정 |
| [docs/design/vendor_cost.md](./docs/design/vendor_cost.md) | 벤더 가격 비교·원가 시뮬레이션·안전 규제 |

## 공개 범위

이 저장소에는 **데몬 코드만** 포함된다. 나비의 해자인 **누적 기억 DB·음색 가중치·레퍼런스 오디오·페르소나 음성 자산**은 의도적으로 제외되어 있어(`.gitignore`), 코드만으로는 특정 나비를 재현할 수 없다.

## 라이선스

[GNU General Public License v3.0 or later](./LICENSE).

이 코드를 가져가 수정·배포하는 경우, 파생물의 소스도 동일하게 GPL로 공개해야 한다.
