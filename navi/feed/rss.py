"""RSS 소스 어댑터 (D13/feed.md 3.2).

소스를 RSS로 좁힌 근거(D13): 무료·ToS 무해(RSS는 소비 목적 배포)·한국어 피드 풍부.
뉴스 API는 무료 티어 제한과 캐싱 ToS 제약, 커뮤니티 크롤링은 ToS 위반 리스크로
로컬 1인용 데몬엔 과하다.
"""

from __future__ import annotations

import logging
import urllib.request
from collections.abc import Callable
from datetime import UTC, datetime

import feedparser

from navi.feed.base import RawItem

log = logging.getLogger(__name__)

_USER_AGENT = "companion-navi/0.1 (+personal RSS reader)"


def _default_fetcher(url: str, timeout_s: float) -> bytes:
    request = urllib.request.Request(url, headers={"User-Agent": _USER_AGENT})
    with urllib.request.urlopen(request, timeout=timeout_s) as response:  # noqa: S310
        return response.read()


class RssSource:
    """RSS 한 피드 = 관심사 하나(topic_key).

    feedparser.parse(url)로 URL을 직접 넘기지 않는 이유: feedparser의 자체 HTTP엔
    타임아웃 손잡이가 없어, 응답 없는 서버 하나가 to_thread 워커를 영구 점유한다.
    바이트를 직접 받아서 넘긴다. fetcher 주입은 그 김에 테스트를 네트워크 없이 만든다.
    """

    def __init__(
        self,
        feed_url: str,
        topic_key: str,
        *,
        timeout_s: float = 10.0,
        fetcher: Callable[[str, float], bytes] | None = None,
    ) -> None:
        self.topic_key = topic_key
        self._url = feed_url
        self._timeout_s = timeout_s
        self._fetch_bytes = fetcher or _default_fetcher

    def fetch(self) -> list[RawItem]:
        """계약대로 실패는 삼키고 빈 리스트 — 한 피드가 배치를 죽이지 않는다."""
        try:
            raw = self._fetch_bytes(self._url, self._timeout_s)
            parsed = feedparser.parse(raw)
        except Exception:
            log.warning("RSS 수집 실패, 이 피드는 건너뛴다: %s", self._url, exc_info=True)
            return []

        if parsed.bozo:
            # feedparser는 깨진 XML에 예외 대신 bozo 플래그를 세운다. 관대하게 —
            # 잘린 피드에서 멀쩡한 앞쪽 기사를 버릴 이유가 없다. 진짜 쓰레기면
            # entries가 자연히 비어 빈 리스트가 된다.
            log.warning("RSS가 온전하지 않다(파싱된 항목만 사용): %s", self._url)

        items = [_to_item(entry) for entry in parsed.entries]
        return [item for item in items if item.title]  # 제목 없으면 요약 재료가 없다


def _to_item(entry) -> RawItem:
    return RawItem(
        title=(entry.get("title") or "").strip(),
        summary=(entry.get("summary") or "").strip(),
        link=(entry.get("link") or "").strip(),
        published=_published(entry),
        guid=(entry.get("id") or "").strip(),
    )


def _published(entry) -> datetime | None:
    parsed = entry.get("published_parsed") or entry.get("updated_parsed")
    if not parsed:
        return None
    return datetime(*parsed[:6], tzinfo=UTC)  # feedparser의 struct_time은 UTC
