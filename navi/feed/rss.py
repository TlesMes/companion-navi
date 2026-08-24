"""RSS 소스 어댑터 (D13/feed.md 3.2).

소스를 RSS로 좁힌 근거(D13): 무료·ToS 무해(RSS는 소비 목적 배포)·한국어 피드 풍부.
뉴스 API는 무료 티어 제한과 캐싱 ToS 제약, 커뮤니티 크롤링은 ToS 위반 리스크로
로컬 1인용 데몬엔 과하다.
"""

from __future__ import annotations

import logging
import re
import urllib.request
from collections.abc import Callable
from datetime import UTC, datetime
from html import unescape
from html.parser import HTMLParser

import feedparser

from navi.feed.base import RawItem

log = logging.getLogger(__name__)

_USER_AGENT = "companion-navi/0.1 (+personal RSS reader)"
_WHITESPACE = re.compile(r"\s+")


class _TextOnly(HTMLParser):
    """태그를 버리고 텍스트만 모은다."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.chunks: list[str] = []

    def handle_data(self, data: str) -> None:
        self.chunks.append(data)


def strip_html(value: str) -> str:
    """RSS 본문에서 마크업을 걷어낸다 — 특정 사이트가 아니라 *포맷* 대응이다.

    RSS 명세가 description에 escaped HTML을 허용해서 생기는 일이라 모든 피드에 균일하게
    적용한다(이미 깨끗한 피드에선 아무 일도 안 일어난다). 이걸 안 하면 인라인 스타일과
    CDN 이미지 URL이 요약 LLM 입력의 대부분을 차지한다 — 실측에서 한 국내 피드의 첫
    항목이 2010자였고 그중 사람이 읽을 문장은 일부였다.

    소스별 분기는 여기 두지 않는다. 특정 피드가 정말 고유 처리를 요구하면 답은 if가
    아니라 FeedSource 구현체를 하나 더 만드는 것이다.

    태그가 없어도 파서를 태운다. 예전엔 '<'가 없으면 건너뛰는 빠른 경로가 있었는데, 그
    경로가 엔티티를 안 풀어 같은 내용이 태그 유무에 따라 갈렸다('AT&amp;T' vs 'AT&T').
    엔티티 해제는 파서의 convert_charrefs가 이미 하므로 바깥에서 unescape를 또 부르지
    않는다 — 이중 해제는 표시용으로 이스케이프된 '&lt;b&gt;'를 실제 태그 문자열로
    되살린다. 문자열이 짧고 배치당 몇 건이라 파서 비용은 문제가 안 된다.
    """
    parser = _TextOnly()
    try:
        parser.feed(value)
        parser.close()
    except Exception:
        # 파서가 죽었으면 charref 변환도 안 됐다 — 이 경로에서만 직접 푼다.
        log.warning("HTML 정리 실패, 원문을 그대로 쓴다", exc_info=True)
        return _WHITESPACE.sub(" ", unescape(value)).strip()
    return _WHITESPACE.sub(" ", "".join(parser.chunks)).strip()


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
        title=strip_html(entry.get("title") or ""),
        summary=strip_html(entry.get("summary") or ""),
        link=(entry.get("link") or "").strip(),
        published=_published(entry),
        guid=(entry.get("id") or "").strip(),
    )


def _published(entry) -> datetime | None:
    parsed = entry.get("published_parsed") or entry.get("updated_parsed")
    if not parsed:
        return None
    return datetime(*parsed[:6], tzinfo=UTC)  # feedparser의 struct_time은 UTC
