"""Feed 소스의 벤더 중립 계약 (arch 4.10, D13/feed.md 3.2).

모든 외부 접점은 어댑터 뒤로(원칙) — RSS든 나중에 뭘 붙이든 collect·저장·pick_topic은
이 프로토콜만 안다. feedparser 같은 벤더 타입은 소스 구현 안에서 끊긴다.

RawItem·FeedSource는 feed 패키지 경계를 안 넘으므로 models.py(모듈 간 계약)에 두지 않는다.
경계를 넘는 건 적재 결과인 TopicCandidate뿐이다.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol


@dataclass(frozen=True)
class RawItem:
    """소스에서 갓 나온 항목 하나 — 아직 요약 전이라 나비 입말이 아니다."""

    title: str
    summary: str
    link: str
    published: datetime | None = None
    guid: str = ""

    @property
    def identity(self) -> str:
        """dedup 원문 — 안정적인 것부터 고른다(guid > link > 제목|게시시각).

        같은 기사가 다음 수집에서도 같은 문자열을 내야 재적재·요약 LLM 낭비를 막는다.
        제목 폴백은 링크가 매번 바뀌는 피드(트래킹 파라미터 등)에 대한 최후 수단.
        """
        return self.guid or self.link or f"{self.title}|{self.published}"


class FeedSource(Protocol):
    """관심사 하나 = 소스 하나. 나중에 소스를 늘려도 collect는 무변경."""

    topic_key: str

    def fetch(self) -> list[RawItem]:
        """블로킹 호출(HTTP) — 호출부가 to_thread로 감싼다.

        실패는 삼켜 빈 리스트로 돌려준다: 피드 하나가 배치 전체를 죽이면 안 된다.
        """
        ...
