"""검문① — STT 결과를 LLM에 보내기 전에 결정론적으로 가로채는 키워드 게이트.

"언제는 규칙, 무엇은 모델" 원칙의 첫 번째 실체. LLM을 거치지 않고 즉시 처리할 수 있는
모드 명령(수면 진입)을 여기서 판정한다 — LLM 비용·레이턴시 없이, 수면 중에도 작동.

명령어 = 긴 구절 (2026.06.25 실측 반영):
  짧은 단어("자라"·"꺼")는 Whisper STT가 문맥 없이 오인식·환각으로 전멸했다(실측 0/12,
  "자라"→"하라", 짧은 발화→"다음 영상에서 만나요" 환각). 긴 구절("이제 그만 잘게" 등)은
  안정적으로 받아써져(3/4) 채택. STT의 띄어쓰기 변동·문장부호는 _normalize가 흡수한다
  (공백 전부 제거 — "이제 자러 갈게."도 "이제그만잘게"도 매칭). 검증: scripts/try/verify_gate.py.

범위·한계:
  - 발화 전체(정규화 후)가 명령 구절과 완전 일치할 때만 SLEEP. 부분 일치는 안 쓴다 —
    false positive 방지("나 이제 자라"는 통과). 발화 중단은 VAD barge-in이 담당.
  - 어미 변형("잘래" vs "잘래요")은 집합에 명시한 구절만 잡는다 — 사용자가 외우는 고정 명령.
  - 레이턴시(STT ~8s, large-v3-turbo CPU)는 게이트 밖 문제 → 속도는 D8(GPU)/D2에서 일괄 처리.
  - KWS 재검토 결론(D7 구현 시, 2026.06.25): 수면 명령은 이 텍스트 게이트에 **유지**한다.
    KWS(웨이크워드)는 *깨우기 전용*. 이유 — 두 게이트는 상보적이다(arch 5.1): KWS는 STT가 꺼진
    SLEEP의 입구(파형 spotting)고, 검문①은 STT가 켜진 ACTIVE에서 발화 전체 일치로 변별한다
    ("나 이제 자라"는 통과). spotting은 그 변별을 못 하고, 수면 명령을 KWS로 옮기면 별도 .ppn이
    더 필요해 운영만 무거워진다. 청취축 상태머신은 navi/ear/listening.py(ListenSession).
"""

from __future__ import annotations

import re
from enum import Enum, auto


class GateResult(Enum):
    PASS = auto()   # LLM으로 통과 — 일반 대화
    SLEEP = auto()  # 수면 진입 — 마이크 루프 종료


# 띄어쓰기·문장부호 정규화 — 공백을 전부 제거(한국어 STT의 들쭉날쭉한 단어 경계 흡수)하고
# 문장부호도 제거한다. 의미 없는 표면 변동만 지우므로 "발화 전체 일치"는 그대로 유지된다.
_STRIP = re.compile(r"[\s.!?~,…。·、，．？！\"'\-]+")


def _normalize(text: str) -> str:
    """공백·문장부호를 전부 제거해 표면 변동(띄어쓰기·꼬리 부호)을 흡수한다."""
    return _STRIP.sub("", text)


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
