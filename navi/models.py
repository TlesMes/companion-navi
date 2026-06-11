"""모듈 간 계약 타입 (01 문서 4장·6장).

모든 모듈은 이 타입으로만 대화한다 — 벤더 SDK 타입이 모듈 경계를 넘으면 안 된다.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True)
class Turn:
    """conversation_turn 한 행 — 단기기억의 단위."""

    role: str  # "user" | "assistant"
    text: str
    created_at: datetime
    session_id: str
    trigger_type: str = "manual"  # manual(사용자호출) | proactive(능동)


@dataclass(frozen=True)
class Message:
    """LLM 요청 안의 대화 한 줄 (벤더 중립)."""

    role: str  # "user" | "assistant"
    text: str


@dataclass(frozen=True)
class LlmRequest:
    """Conductor가 조립하고 Brain 어댑터가 벤더 형식으로 변환하는 요청.

    캐싱 친화 순서로 조립된다(마스터 플랜 — 입력비 0 수렴):
    system(캐릭터 카드, 고정) → messages(최근 턴 + 이번 트리거, 매번 변함).
    """

    system: str
    messages: list[Message]  # 시간순, 마지막이 이번 트리거
    model: str


@dataclass(frozen=True)
class Usage:
    """원가 모니터링용 — Brain은 매 호출 usage를 반환한다 (계약 4.7)."""

    input_tokens: int
    output_tokens: int


@dataclass(frozen=True)
class BrainResult:
    """스트림 종료 시 확정되는 전문과 사용량."""

    full_text: str
    usage: Usage
