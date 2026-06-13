"""STT 어댑터 계약 (01 문서 4.3절 — 스트리밍).

귀(Ear)가 음성 프레임을 흘려보내면 발화 중 변환이 진행되고, 발화 종료(UtteranceEnded) 시
finalize로 텍스트가 확정된다. 벤더(VITO·Clova·Deepgram…)는 이 계약 뒤에 숨는다 — 한국어
품질(CER)로 고르되, 무엇을 고르든 후단 파이프라인은 SttResult로만 대화한다.

사용 규약:
- 발화 1건 = 세션 1개. open_stream으로 열고, feed로 프레임을 밀어넣고, finalize로 닫는다.
- 어댑터 인스턴스는 동시 1발화 전제 (데몬은 단일 대화 스트림).
"""

from __future__ import annotations

from abc import ABC, abstractmethod

from navi.models import AudioChunk, SttResult


class SttSession(ABC):
    """한 발화에 대한 스트리밍 인식 세션."""

    @abstractmethod
    async def feed(self, chunk: AudioChunk) -> None:
        """Ear의 음성 프레임을 흘려보낸다 — 발화 중 변환이 진행된다."""

    @abstractmethod
    async def finalize(self) -> SttResult:
        """UtteranceEnded 시 호출 — 누적 인식을 확정해 반환한다 (거의 즉시)."""


class SttAdapter(ABC):
    @abstractmethod
    async def open_stream(self, lang: str = "ko") -> SttSession:
        """새 발화용 스트리밍 세션을 연다. 한국어 퍼스트라 기본 lang은 'ko'."""
