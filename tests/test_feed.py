"""관심사 피드(D13, feed.md) — RSS 소스와 collect 오케스트레이터.

전부 헤드리스: 소스는 주입된 fetcher/fake, 요약기는 EchoBrain이라 네트워크·API 키가 없다.
실 RSS + 실 LLM로 나비가 먼저 말 거는 E2E는 A3(실기) 트랙.
"""

from __future__ import annotations

from datetime import UTC, datetime
from urllib.error import URLError

from navi.feed.base import RawItem
from navi.feed.rss import RssSource

# 소스는 바이트를 받는다(HTTP 응답 그대로) — 한국어라 str로 쓰고 인코딩한다.
_FEED = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0"><channel>
  <title>축구 뉴스</title>
  <item>
    <title>우리 팀 3대1 승리</title>
    <description>후반 교체 투입된 선수가 두 골을 넣었다.</description>
    <link>https://example.test/news/1</link>
    <guid>news-1</guid>
    <pubDate>Wed, 29 Jul 2026 12:00:00 GMT</pubDate>
  </item>
  <item>
    <title>감독 인터뷰</title>
    <description>다음 경기 각오를 밝혔다.</description>
    <link>https://example.test/news/2</link>
  </item>
</channel></rss>
""".encode()

_EMPTY_FEED = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0"><channel><title>조용한 피드</title></channel></rss>
""".encode()

# feedparser는 웬만한 손상을 관대하게 넘기므로(bozo 플래그) "진짜 파싱 불가"여야 한다.
_GARBAGE = b"\x00\x01\x02 not xml at all \xff\xfe"


def _source(payload: bytes, topic_key: str = "축구") -> RssSource:
    return RssSource(
        "https://example.test/rss", topic_key, fetcher=lambda url, timeout: payload
    )


def test_rss_source_parses_entries_into_raw_items():
    """정상 피드 → RawItem. 벤더(feedparser) 타입이 어댑터 밖으로 안 샌다."""
    items = _source(_FEED).fetch()

    assert [i.title for i in items] == ["우리 팀 3대1 승리", "감독 인터뷰"]
    assert items[0].link == "https://example.test/news/1"
    assert items[0].guid == "news-1"
    assert items[0].published == datetime(2026, 7, 29, 12, 0, tzinfo=UTC)
    assert "두 골" in items[0].summary
    assert items[1].published is None  # pubDate 없는 항목도 통과한다


def test_rss_source_returns_empty_for_feed_without_items():
    assert _source(_EMPTY_FEED).fetch() == []


def test_rss_source_swallows_broken_xml():
    """파싱 불가 입력 → 예외 대신 빈 리스트(피드 하나가 배치를 죽이지 않는다)."""
    assert _source(_GARBAGE).fetch() == []


def test_rss_source_swallows_fetch_error():
    """네트워크 실패도 같은 계약 — 삼키고 빈 리스트."""

    def boom(url, timeout):
        raise URLError("연결 실패")

    source = RssSource("https://example.test/rss", "축구", fetcher=boom)
    assert source.fetch() == []


def test_raw_item_identity_prefers_guid_over_link():
    """dedup 정확성의 근거 — 가장 안정적인 식별자를 고른다."""
    both = RawItem(title="제목", summary="", link="https://a.test/1", guid="g1")
    link_only = RawItem(title="제목", summary="", link="https://a.test/1")
    bare = RawItem(title="제목", summary="", link="")

    assert both.identity == "g1"
    assert link_only.identity == "https://a.test/1"
    assert bare.identity.startswith("제목|")
