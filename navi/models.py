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


@dataclass(frozen=True)
class AudioChunk:
    """PCM 오디오 한 조각 — 마이크 입력(STT로 흘림)·스피커 출력(TTS에서 나옴) 공통.

    벤더 SDK의 오디오 타입이 모듈 경계를 넘지 않도록 데몬 내부 표현을 이걸로 통일한다.
    """

    pcm: bytes
    sample_rate: int = 16000


@dataclass(frozen=True)
class SttResult:
    """발화 한 건의 확정 인식 결과 (계약 4.3 finalize 반환)."""

    text: str
    confidence: float  # 0.0~1.0
    lang: str


@dataclass(frozen=True)
class VoiceProfile:
    """나비의 단일 목소리 (설계 원칙 2 — voice_profile 단일 고정).

    벤더 중립 핸들: 정체성은 name이 소유하고, vendor_voice_id만 TTS 벤더 교체 시 갈아끼운다.
    두뇌·TTS 벤더가 바뀌어도 name이 같으면 "같은 목소리"로 취급한다.
    """

    name: str  # 논리적 정체성 (로깅·식별)
    vendor_voice_id: str  # 현재 TTS 벤더의 음색 id
    speed: float = 1.0
