"""검문① — STT 결과를 LLM에 보내기 전에 결정론적으로 가로채는 키워드 게이트.

"언제는 규칙, 무엇은 모델" 원칙의 첫 번째 실체. LLM을 거치지 않고 즉시 처리할 수 있는
모드 명령(수면 진입)을 여기서 판정한다 — LLM 비용·레이턴시 없이, 수면 중에도 작동.

명령어 = 긴 구절 (2026.06.25 실측 반영):
  짧은 단어("자라"·"꺼")는 Whisper STT가 문맥 없이 오인식·환각으로 전멸했다(실측 0/12,
  "자라"→"하라", 짧은 발화→"다음 영상에서 만나요" 환각). 긴 구절("이제 그만 잘게" 등)은
  안정적으로 받아써져(3/4) 채택. STT가 붙이는 양끝 문장부호·공백은 _normalize로 흡수한다
  ("이제 자러 갈게." → 매칭). 검증 도구: scripts/try/verify_gate.py.

범위·한계:
  - 발화 전체(정규화 후)가 명령 구절과 완전 일치할 때만 SLEEP. 부분 일치는 안 쓴다 —
    false positive 방지("나 이제 자라"는 통과). 발화 중단은 VAD barge-in이 담당.
  - 어미 변형("잘래" vs "잘래요")은 집합에 명시한 구절만 잡는다 — 사용자가 외우는 고정 명령.
  - 레이턴시(STT ~8s, large-v3-turbo CPU)는 게이트 밖 문제 → 속도는 D8(GPU)/D2에서 일괄 처리.
  - 전략 대안: 고정 명령을 KWS(D7 웨이크워드)로 옮기면 인식·속도를 동시에 해결한다 —
    D7 구현 시 재검토(D16). 그때 이 텍스트 게이트의 존속 여부를 다시 판단.
"""

from __future__ import annotations

import re
from enum import Enum, auto


class GateResult(Enum):
    PASS = auto()   # LLM으로 통과 — 일반 대화
    SLEEP = auto()  # 수면 진입 — 마이크 루프 종료


# 양끝 문장부호·공백 정규화 — STT가 붙이는 꼬리 부호("…갈게.")·중복 공백을 흡수
_EDGE_PUNCT = re.compile(r"^[\s.!?~,…。·\"'\-]+|[\s.!?~,…。·\"'\-]+$")


def _normalize(text: str) -> str:
    """양끝 문장부호·공백 제거 + 내부 공백 단일화. STT 출력의 표면 변동을 흡수한다."""
    stripped = _EDGE_PUNCT.sub("", text)
    return re.sub(r"\s+", " ", stripped).strip()


# 수면 명령 구절 집합 (정규화된 형태, 발화 전체 일치만)
_SLEEP_COMMANDS: frozenset[str] = frozenset(
    _normalize(phrase)
    for phrase in (
        "이제 그만 잘게",
        "이제 그만 잘래",
        "오늘은 그만 잘래",
        "이제 자러 갈게",
        "나비 이제 잘게",
        "잘 자 나비",
        "그만 자자",
    )
)


def check_gate(text: str) -> GateResult:
    """STT 텍스트가 수면 명령이면 GateResult.SLEEP, 아니면 PASS.

    양끝 문장부호·공백을 정규화한 뒤 명령 구절과 완전 일치를 본다 — 한국어는 대소문자가
    없으니 소문자 변환은 하지 않는다.
    """
    if _normalize(text) in _SLEEP_COMMANDS:
        return GateResult.SLEEP
    return GateResult.PASS
