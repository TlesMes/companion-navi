"""관심사 피드(D13, feed.md) — RSS 소스와 collect 오케스트레이터.

전부 헤드리스: 소스는 주입된 fetcher/fake, 요약기는 EchoBrain이라 네트워크·API 키가 없다.
실 RSS + 실 LLM로 나비가 먼저 말 거는 E2E는 A3(실기) 트랙.
"""

from __future__ import annotations

import sqlite3
import threading
from datetime import UTC, datetime, timedelta
from urllib.error import URLError

from navi.brain.echo import EchoBrain
from navi.feed.base import RawItem
from navi.feed.collect import Feed
from navi.feed.rss import RssSource
from navi.memory import MemoryStore

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


# ─── collect 오케스트레이터 ──────────────────────────────────

_NOW = datetime(2026, 7, 30, 9, 0, tzinfo=UTC)


class FakeSource:
    """호출 횟수와 실행 스레드를 기록하는 소스 — 게이트·to_thread 검증에 쓴다."""

    def __init__(self, topic_key="축구", items=None, boom=False):
        self.topic_key = topic_key
        self._items = items if items is not None else [_item("우리 팀 3대1 승리", "g1")]
        self._boom = boom
        self.fetch_count = 0
        self.thread_ids: list[int] = []

    def fetch(self):
        self.fetch_count += 1
        self.thread_ids.append(threading.get_ident())
        if self._boom:
            raise RuntimeError("소스가 터졌다")
        return list(self._items)


class CountingBrain(EchoBrain):
    """echo에 호출 카운터를 얹은 것 — 요약 LLM이 몇 번 불렸는지 세려고."""

    def __init__(self, fail_from=None):
        super().__init__()
        self.calls = 0
        self._fail_from = fail_from

    async def generate_stream(self, request):
        self.calls += 1
        if self._fail_from is not None and self.calls >= self._fail_from:
            raise RuntimeError("요약기가 터졌다")
        async for token in super().generate_stream(request):
            yield token


def _item(title, guid):
    return RawItem(title=title, summary="본문", link=f"https://a.test/{guid}", guid=guid)


def _feed(tmp_path, sources, brain=None, **kwargs):
    store = MemoryStore(tmp_path / "t.db")
    return store, Feed(
        store=store,
        sources=sources,
        summarizer=brain or EchoBrain(),
        model="test-model",
        **kwargs,
    )


async def test_collect_stores_summarized_rss_candidates(tmp_path):
    """D13 — RSS 항목이 요약을 거쳐 후보로 적재된다."""
    store, feed = _feed(tmp_path, [FakeSource()])

    assert await feed.collect(_NOW) == 1

    cands = store.fresh_candidates(10, _NOW.isoformat())
    assert len(cands) == 1
    assert cands[0].source == "rss"
    assert cands[0].topic_key == "축구"
    assert "우리 팀 3대1 승리" in cands[0].summary  # echo가 요약 요청문을 되돌려준다
    assert cands[0].expires_at > _NOW  # TTL이 미래로 잡혀 아직 유효


async def test_collect_skips_already_stored_items_without_calling_llm(tmp_path):
    """dedup은 요약 *전에* 걸려야 한다 — 이미 있는 기사에 LLM을 또 태우지 않는다."""
    brain = CountingBrain()
    store, feed = _feed(tmp_path, [FakeSource()], brain)

    await feed.collect(_NOW)
    await feed.collect(_NOW + timedelta(hours=1))

    count = sqlite3.connect(tmp_path / "t.db").execute(
        "SELECT COUNT(*) FROM topic_candidate"
    ).fetchone()[0]
    assert count == 1
    assert brain.calls == 1  # 2회차는 요약을 아예 안 불렀다


async def test_collect_skips_item_when_summarizer_fails(tmp_path):
    """요약 실패는 그 항목만 버린다 — 배치는 계속 간다(feed.md 7)."""
    brain = CountingBrain(fail_from=2)
    items = [_item("첫 기사", "g1"), _item("둘째 기사", "g2")]
    store, feed = _feed(tmp_path, [FakeSource(items=items)], brain)

    assert await feed.collect(_NOW) == 1  # 예외가 밖으로 안 샌다
    assert [c.summary.startswith("첫 기사") for c in store.fresh_candidates(10, _NOW.isoformat())] == [True]


async def test_collect_survives_failing_source(tmp_path):
    """소스 하나가 터져도 나머지 소스 것은 적재된다."""
    good = FakeSource("날씨", items=[_item("오늘 34도", "w1")])
    store, feed = _feed(tmp_path, [FakeSource(boom=True), good])

    assert await feed.collect(_NOW) == 1
    assert store.fresh_candidates(10, _NOW.isoformat())[0].topic_key == "날씨"


async def test_collect_records_last_collect_at(tmp_path):
    store, feed = _feed(tmp_path, [FakeSource()])
    assert store.last_collect_at() is None

    await feed.collect(_NOW)
    assert store.last_collect_at() == _NOW.isoformat()


async def test_maybe_collect_respects_interval_gate(tmp_path):
    """하루 1~2회 배치 — 간격 전엔 아예 안 긁는다."""
    source = FakeSource()
    store, feed = _feed(tmp_path, [source], collect_interval_s=3600)

    assert await feed.maybe_collect(_NOW) is True  # 첫 기동 — 게이트 없음
    assert await feed.maybe_collect(_NOW + timedelta(minutes=30)) is False
    assert source.fetch_count == 1  # 게이트에 막혀 fetch 자체가 없었다

    assert await feed.maybe_collect(_NOW + timedelta(hours=2)) is True
    assert source.fetch_count == 2


async def test_collect_fetches_off_the_event_loop(tmp_path):
    """블로킹 HTTP는 워커 스레드에서 — 오디오 핫패스를 막지 않는다.

    이 단정이 to_thread 결정을 회귀로 못 박는다(누가 지우면 여기서 잡힌다).
    """
    source = FakeSource()
    store, feed = _feed(tmp_path, [source])

    await feed.collect(_NOW)

    assert source.thread_ids and source.thread_ids[0] != threading.get_ident()


async def test_collect_caps_items_per_source(tmp_path):
    """첫 수집에서 큰 피드를 만나도 요약 LLM 호출이 폭주하지 않는다."""
    brain = CountingBrain()
    items = [_item(f"기사 {i}", f"g{i}") for i in range(20)]
    store, feed = _feed(tmp_path, [FakeSource(items=items)], brain, max_items_per_source=3)

    assert await feed.collect(_NOW) == 3
    assert brain.calls == 3


async def test_get_fresh_topics_and_mark_used_round_trip(tmp_path):
    """데몬 계약(feed.md 7) — 객체로 받아 candidate_id로 used 처리한다."""
    items = [_item("첫 기사", "g1"), _item("둘째 기사", "g2")]
    store, feed = _feed(tmp_path, [FakeSource(items=items)])
    await feed.collect(_NOW)

    cands = feed.get_fresh_topics(now=_NOW)
    assert len(cands) == 2
    feed.mark_used(cands[0].candidate_id, _NOW)

    remaining = feed.get_fresh_topics(now=_NOW)
    assert [c.candidate_id for c in remaining] == [cands[1].candidate_id]
