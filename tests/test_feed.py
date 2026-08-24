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
from navi.feed.rss import RssSource, strip_html
from navi.feed.summarize import (
    CALLBACK_SYSTEM,
    build_callback_prompt,
    clean_summary,
    normalize_topic_label,
    parse_callbacks,
)
from navi.memory import MemoryStore
from navi.models import BrainResult, Turn, Usage

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


def test_rss_source_strips_markup_from_summary():
    """RSS 명세가 description에 HTML을 허용해 생기는 일 — 모든 피드에 균일 적용.

    실측(2026.08.02): 한 국내 피드의 첫 항목이 인라인 스타일·이미지 태그 포함 2010자였다.
    안 걷어내면 요약 LLM 입력의 대부분이 마크업이 된다.
    """
    feed = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0"><channel><item>
  <title>신작 공개</title>
  <description>&lt;div style="text-align: center;"&gt;&lt;img src="//cdn.test/a.webp"/&gt;
  &lt;p&gt;다음 달   출시된다.&lt;/p&gt;&lt;/div&gt;</description>
  <link>https://a.test/1</link>
</item></channel></rss>
""".encode()

    item = _source(feed).fetch()[0]

    assert item.summary == "다음 달 출시된다."  # 태그 제거 + 공백 정리


def test_rss_source_leaves_plain_summary_untouched():
    """이미 깨끗한 피드(해외 다수)에선 아무 일도 안 일어난다."""
    item = _source(_FEED).fetch()[0]

    assert item.summary == "후반 교체 투입된 선수가 두 골을 넣었다."


def test_entities_decode_the_same_with_or_without_tags():
    """태그 유무가 엔티티 해제를 가르면 안 된다.

    회귀 방지: '<'가 없으면 파서를 건너뛰는 빠른 경로가 unescape까지 건너뛰어,
    같은 내용이 'AT&T'와 'AT&amp;T'로 갈렸다. 남으면 TTS가 'amp'를 읽는다.
    """
    assert strip_html("AT&amp;T가 발표했다") == "AT&T가 발표했다"
    assert strip_html("<p>AT&amp;T가 발표했다</p>") == "AT&T가 발표했다"


def test_entities_are_not_decoded_twice():
    """표시용으로 이스케이프된 태그 표기가 실제 태그로 되살아나면 안 된다.

    회귀 방지: 파서(convert_charrefs)가 이미 푼 결과에 unescape를 또 걸어
    '&amp;lt;b&amp;gt;'가 '<b>'가 됐다.
    """
    assert strip_html("<p>&amp;lt;b&amp;gt; 표기</p>") == "&lt;b&gt; 표기"


def test_rss_source_decodes_entities_end_to_end():
    """어댑터를 통과한 값에도 원본 엔티티 표기가 남지 않는다."""
    feed = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0"><channel><item>
  <title>AT&amp;amp;T 신작</title>
  <description>가격은 5&amp;amp;10 달러</description>
  <link>https://a.test/1</link>
</item></channel></rss>
""".encode()

    item = _source(feed).fetch()[0]

    assert item.title == "AT&T 신작"
    assert item.summary == "가격은 5&10 달러"


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
        self.boom = boom  # 테스트 도중 회복시킬 수 있게 공개
        self.fetch_count = 0
        self.thread_ids: list[int] = []

    def fetch(self):
        self.fetch_count += 1
        self.thread_ids.append(threading.get_ident())
        if self.boom:
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


def _item(title, guid, published=None):
    return RawItem(
        title=title,
        summary="본문",
        link=f"https://a.test/{guid}",
        guid=guid,
        published=published,
    )


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


def test_clean_summary_removes_markdown_that_would_be_spoken():
    """재료는 그대로 발화 트리거가 된다 — 서식이 남으면 나비가 소리 내 읽는다.

    실측 2026.08.02: haiku가 '# 요약'을 머리말로 붙였다.
    """
    assert clean_summary("# 요약\n\n오늘 할인 소식이 있다.") == "오늘 할인 소식이 있다."
    assert clean_summary("```\n어제 경기가 있었다.\n```") == "어제 경기가 있었다."
    assert clean_summary("- 첫 소식\n- 둘째 소식") == "첫 소식 둘째 소식"
    assert clean_summary("**중요한** 발표가 있었다.") == "중요한 발표가 있었다."


def test_clean_summary_leaves_plain_sentence_untouched():
    """서식이 없으면 아무 일도 안 일어난다(대부분의 경우)."""
    assert clean_summary("  어제 경기에서 3대1로 이겼다.  ") == "어제 경기에서 3대1로 이겼다."


def test_clean_summary_keeps_numbers_that_start_a_sentence():
    """숫자+마침표로 시작하는 정상 문장을 목록기호로 오인하면 안 된다.

    회귀 방지: 번호목록 패턴을 무조건 지워 '2026. 8월 출시 예정이다.'가
    '8월 출시 예정이다.'가 됐다 — 연도가 소리 없이 사라진 채 발화 재료가 된다.
    """
    assert clean_summary("2026. 8월 출시 예정이다.") == "2026. 8월 출시 예정이다."
    assert clean_summary("2026. 8. 5. 유료 DLC가 배포된다.") == "2026. 8. 5. 유료 DLC가 배포된다."
    assert clean_summary("3. 1운동 기념 이벤트가 열린다.") == "3. 1운동 기념 이벤트가 열린다."


def test_clean_summary_still_strips_real_numbered_lists():
    """항목이 여럿이면 진짜 번호목록으로 보고 지운다."""
    assert clean_summary("1. 첫 소식\n2. 둘째 소식") == "첫 소식 둘째 소식"


async def test_expires_at_counts_from_publication_not_collection(tmp_path):
    """오래된 기사가 수집 시각부터 또 TTL만큼 살면 안 된다 — 이른 쪽 기준(ⓒ).

    실측 2026.08.02: 발행이 뜸한 해외 매체는 피드 맨 앞이 이미 41시간 지난 기사였다.
    """
    old = _item("이틀 전 기사", "g1", published=_NOW - timedelta(hours=48))
    store, feed = _feed(tmp_path, [FakeSource(items=[old])], rss_ttl_hours=96)

    await feed.collect(_NOW)

    cand = store.fresh_candidates(10, _NOW.isoformat())[0]
    assert cand.expires_at == _NOW - timedelta(hours=48) + timedelta(hours=96)


async def test_expires_at_falls_back_to_collection_time(tmp_path):
    """발행 시각이 없거나 미래면(시계 어긋남·예약 발행) 수집 시각을 쓴다."""
    undated = _item("발행일 없음", "g1")
    future = _item("미래 발행", "g2", published=_NOW + timedelta(hours=5))
    store, feed = _feed(tmp_path, [FakeSource(items=[undated, future])], rss_ttl_hours=96)

    await feed.collect(_NOW)

    expected = _NOW + timedelta(hours=96)
    assert {c.expires_at for c in store.fresh_candidates(10, _NOW.isoformat())} == {expected}


async def test_batch_candidates_come_out_newest_article_first(tmp_path):
    """한 배치 안에서도 발행 최신순이어야 한다 — pick_topic이 [0]만 쓰기 때문.

    회귀 방지: fetched_at으로 정렬하면 배치 전체가 동률이라 tiebreak가 삽입 순서를
    뒤집어, RSS 최신순으로 넣은 것이 **가장 낡은 기사부터** 나왔다.
    """
    items = [  # RSS 관례 — 최신 기사가 앞
        _item(f"기사 {h}시간전", f"g{h}", published=_NOW - timedelta(hours=h))
        for h in (1, 5, 20)
    ]
    store, feed = _feed(tmp_path, [FakeSource(items=items)])
    await feed.collect(_NOW)

    cands = feed.get_fresh_topics(k=10, now=_NOW)
    assert [c.published_at for c in cands] == [
        _NOW - timedelta(hours=h) for h in (1, 5, 20)
    ]
    assert cands[0].summary.startswith("기사 1시간전")  # pick_topic이 집는 자리


async def test_candidates_without_publication_fall_back_to_fetch_time(tmp_path):
    """발행 시각 없는 소스(memory 콜백 등)는 fetched_at으로 정렬돼 앞에 선다."""
    items = [_item("발행일 없음", "g0"), _item("어제 기사", "g1", published=_NOW - timedelta(hours=24))]
    store, feed = _feed(tmp_path, [FakeSource(items=items)])
    await feed.collect(_NOW)

    cands = feed.get_fresh_topics(k=10, now=_NOW)
    assert cands[0].published_at is None
    assert cands[0].summary.startswith("발행일 없음")


async def test_naive_now_is_treated_as_local_and_stored_as_utc(tmp_path):
    """데몬의 wall_now(=datetime.now)는 naive 로컬이다 — 그대로 들어와도 깨지면 안 된다.

    회귀 방지: naive가 그대로 흐르면 min()·뺄셈이 TypeError고, 저장소의 문자열 비교가
    오프셋 없는 값과 '+00:00' 값을 섞어 TTL 필터가 조용히 오작동한다.
    """
    naive_now = datetime.now()  # noqa: DTZ005 — 데몬 wall_now 기본값 재현
    published = datetime.now(UTC) - timedelta(hours=2)
    store, feed = _feed(tmp_path, [FakeSource(items=[_item("기사", "g1", published=published)])])

    assert await feed.collect(naive_now) == 1  # TypeError 없이 완주

    cand = feed.get_fresh_topics(now=naive_now)[0]  # 조회도 naive로
    assert cand.fetched_at.utcoffset() is not None  # aware로 저장됐다
    assert cand.expires_at > datetime.now(UTC)  # 만료 판정이 정상 동작
    assert store.last_collect_at().endswith("+00:00")


async def test_naive_now_passes_the_interval_gate(tmp_path):
    """maybe_collect도 같은 경계 — 저장된 aware에서 naive를 빼면 TypeError였다.

    1회차를 aware로 돌려 last_collect_at을 aware로 심어 두고, 2회차를 데몬처럼 naive로
    부른다(그래야 두 표현이 실제로 섞인다).
    """
    source = FakeSource()
    store, feed = _feed(tmp_path, [source], collect_interval_s=3600)

    assert await feed.maybe_collect(datetime.now(UTC)) is True
    assert store.last_collect_at().endswith("+00:00")

    assert await feed.maybe_collect(datetime.now()) is False  # noqa: DTZ005 — 게이트에 막힘
    assert source.fetch_count == 1


async def test_quiet_feed_is_not_treated_as_failure(tmp_path):
    """새 기사가 없어 stored==0인 건 정상이다 — 그걸 장애로 보면 조용한 피드를 매 tick 두들긴다."""
    source = FakeSource()
    store, feed = _feed(tmp_path, [source], collect_interval_s=3600)

    await feed.collect(_NOW)  # 1회차 — 적재됨
    assert await feed.collect(_NOW + timedelta(hours=2)) == 0  # 전부 dedup

    # 정상으로 봤으니 수집 시각이 갱신되고, 정상 간격이 그대로 적용된다
    assert store.last_collect_at() == (_NOW + timedelta(hours=2)).isoformat()
    assert await feed.maybe_collect(_NOW + timedelta(hours=2, minutes=30)) is False


async def test_all_sources_failing_schedules_a_short_retry(tmp_path):
    """네트워크 장애로 전멸한 배치는 12시간을 기다리지 않는다."""
    store, feed = _feed(
        tmp_path, [FakeSource(boom=True)], collect_interval_s=43200, retry_backoff_s=1800
    )

    assert await feed.collect(_NOW) == 0
    assert store.last_collect_at() is None  # 실패를 성공으로 기록하지 않는다

    assert await feed.maybe_collect(_NOW + timedelta(minutes=10)) is False  # 백오프 중
    assert await feed.maybe_collect(_NOW + timedelta(minutes=31)) is True  # 백오프 후 재시도


async def test_summarizer_outage_is_treated_as_failure(tmp_path):
    """소스는 멀쩡한데 요약기가 전멸한 경우 — 실측(gemini 일일 쿼터 429)에서 나온 상황.

    stored만 보면 "조용한 피드"와 구분이 안 돼 고칠 수 있는 장애를 12시간 방치하게 된다.
    """
    brain = CountingBrain(fail_from=1)  # 첫 호출부터 실패
    store, feed = _feed(tmp_path, [FakeSource()], brain, retry_backoff_s=1800)

    assert await feed.collect(_NOW) == 0
    assert brain.calls == 1  # 소스는 응답했고 요약을 시도는 했다
    assert store.last_collect_at() is None  # 장애로 판정

    assert await feed.maybe_collect(_NOW + timedelta(minutes=31)) is True


async def test_recovery_clears_the_backoff(tmp_path):
    """장애가 풀리면 정상 간격으로 돌아간다."""
    source = FakeSource(boom=True)
    store, feed = _feed(tmp_path, [source], collect_interval_s=3600, retry_backoff_s=1800)

    await feed.collect(_NOW)  # 전멸
    source.boom = False  # 회복

    assert await feed.maybe_collect(_NOW + timedelta(minutes=31)) is True
    assert store.last_collect_at() is not None
    # 백오프가 풀렸으니 이제 정상 간격(1h)이 적용된다
    assert await feed.maybe_collect(_NOW + timedelta(minutes=45)) is False


async def test_no_sources_configured_is_not_a_failure(tmp_path):
    """관심사 미등록은 할 일이 없는 것이지 장애가 아니다 — 30분마다 재시도하면 안 된다."""
    store, feed = _feed(tmp_path, [], collect_interval_s=3600, retry_backoff_s=1800)

    assert await feed.collect(_NOW) == 0
    assert store.last_collect_at() == _NOW.isoformat()
    assert await feed.maybe_collect(_NOW + timedelta(minutes=31)) is False


async def test_get_fresh_topics_honours_explicit_zero(tmp_path):
    """k=0(하나도 필요 없음)이 기본값으로 뒤바뀌면 안 된다.

    회귀 방지: `k or 기본값`이 0을 falsy로 봐서 후보 3개를 돌려줬다 — 호출부가
    억제하려던 선제 발화가 그대로 나간다.
    """
    store, feed = _feed(tmp_path, [FakeSource()])
    await feed.collect(_NOW)

    assert feed.get_fresh_topics(k=0, now=_NOW) == []
    assert len(feed.get_fresh_topics(now=_NOW)) == 1  # 미지정은 기본값 그대로


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


# ─── ② 대화 콜백 (D13 PR2) ──────────────────────────────────


class ScriptedBrain(EchoBrain):
    """콜백 추출 요청에만 정해진 문자열을 뱉고 나머지는 echo 그대로.

    EchoBrain은 마지막 user 메시지를 되돌려줄 뿐이라 구조화 출력을 만들 수 없다.
    request.system 동일성으로 갈라서 rss 요약을 기대하는 기존 경로와 섞어 쓸 수 있다.
    """

    def __init__(self, callback_reply="", *, boom=False):
        super().__init__()
        self._reply = callback_reply
        self._boom = boom
        self.requests = []

    async def generate_stream(self, request):
        self.requests.append(request)
        if request.system is not CALLBACK_SYSTEM:
            async for token in super().generate_stream(request):
                yield token
            return
        if self._boom:
            raise RuntimeError("추출기가 터졌다")
        self.last_result = None
        yield self._reply
        self.last_result = BrainResult(full_text=self._reply, usage=Usage(0, 0))


_REPLY = "[topic:이직] 사용자가 지난주에 이직을 고민한다고 말했고 아직 결론은 안 났다."


def _turns(*texts, role="user", at=None):
    moment = at or _NOW
    return [Turn(role=role, text=t, created_at=moment, session_id="s1") for t in texts]


def _user_turns(n=3):
    return _turns(*[f"이직 얘기 {i}" for i in range(n)])


# 파서 — DB·네트워크 없음

def test_parse_callbacks_reads_tagged_lines():
    text = "[topic:이직] 사용자가 이직을 고민한다.\n[topic:러닝] 사용자가 러닝을 시작했다."

    assert parse_callbacks(text, 2) == [
        ("이직", "사용자가 이직을 고민한다."),
        ("러닝", "사용자가 러닝을 시작했다."),
    ]


def test_parse_callbacks_ignores_untagged_prose():
    """부분 실패가 국소적이다 — JSON이면 같은 입력에서 전체 파싱이 터진다."""
    text = "아래와 같이 정리했습니다:\n[topic:이직] 사용자가 이직을 고민한다.\n이상입니다."

    assert parse_callbacks(text, 2) == [("이직", "사용자가 이직을 고민한다.")]


def test_parse_callbacks_returns_empty_for_unparseable_output():
    """파싱 실패는 예외가 아니라 폴백 — ② 스킵으로 이어진다."""
    assert parse_callbacks("뽑을 만한 화제가 없습니다.", 2) == []
    assert parse_callbacks("", 2) == []


def test_parse_callbacks_caps_at_max():
    text = "\n".join(f"[topic:주제{i}] 사용자가 {i}번 얘기를 했다." for i in range(5))

    assert len(parse_callbacks(text, 2)) == 2


def test_parse_callbacks_strips_markdown_per_line():
    """줄 구조는 살고 서식만 벗겨진다 — drain에서 clean_summary를 뺀 결정의 회귀 잠금."""
    text = "```\n[topic:이직] **사용자가** 이직을 고민한다.\n[topic:러닝] 사용자가 뛴다.\n```"

    assert parse_callbacks(text, 2) == [
        ("이직", "사용자가 이직을 고민한다."),
        ("러닝", "사용자가 뛴다."),
    ]


def test_parse_callbacks_drops_pairs_with_empty_parts():
    text = "[topic:  ] 라벨이 비었다.\n[topic:이직] 사용자가 이직을 고민한다."

    assert parse_callbacks(text, 2) == [("이직", "사용자가 이직을 고민한다.")]


def test_parse_callbacks_dedups_repeated_labels_within_one_reply():
    text = "[topic:이직] 첫 문장이다.\n[topic:이직 고민] 같은 화제 두 번째다."

    assert parse_callbacks(text, 2) == [("이직", "첫 문장이다.")]


def test_normalize_topic_label_absorbs_common_drift():
    """LLM이 같은 화제를 다르게 부르면 dedup이 뚫려 같은 얘기를 반복해서 되묻는다."""
    assert normalize_topic_label(" 이직 고민 ") == normalize_topic_label("이직")
    assert normalize_topic_label("이직 이야기") == "이직"
    assert normalize_topic_label("고민") == "고민"  # 통째로 접미면 안 지운다(≥2자 가드)


def test_callback_prompt_carries_only_user_turns_with_relative_dates():
    """나비 자기 말에서 화제를 뽑아 되물으면 세계관이 깨진다."""
    turns = [
        Turn(role="user", text="이직 고민 중", created_at=_NOW, session_id="s1"),
        Turn(role="assistant", text="나비의 대답", created_at=_NOW, session_id="s1"),
        Turn(
            role="user",
            text="어제 면접 봤어",
            created_at=_NOW - timedelta(days=1),
            session_id="s1",
        ),
    ]

    prompt = build_callback_prompt(turns, _NOW)

    assert "나비의 대답" not in prompt
    assert "(오늘) 이직 고민 중" in prompt
    assert "(어제) 어제 면접 봤어" in prompt


# 수집 — DB 있음

def _callback_feed(tmp_path, reply=_REPLY, *, turns=None, sources=None, boom=False, **kwargs):
    brain = ScriptedBrain(reply, boom=boom)
    store, feed = _feed(
        tmp_path,
        sources if sources is not None else [],
        brain,
        recall_turns=lambda: turns if turns is not None else _user_turns(),
        **kwargs,
    )
    return store, feed, brain


async def test_collect_stores_callback_candidate_from_recent_turns(tmp_path):
    """D13 ② — 사용자가 예전에 한 말이 중립 진술 재료가 된다."""
    store, feed, _ = _callback_feed(tmp_path)

    assert await feed.collect(_NOW) == 1

    cand = store.fresh_candidates(10, _NOW.isoformat())[0]
    assert cand.source == "memory"
    assert cand.topic_key == "이직"  # 정규화된 라벨이 저장된다
    assert cand.summary.startswith("사용자가 지난주에")
    assert cand.published_at is None  # 발행 개념이 없다
    assert cand.expires_at == _NOW + timedelta(hours=168)  # 기본 7일


async def test_callback_ttl_is_configurable(tmp_path):
    """수명은 값만 바꾸면 되는 손잡이다 — PR3에서 config로 뺀다."""
    store, feed, _ = _callback_feed(tmp_path, callback_ttl_hours=24)
    await feed.collect(_NOW)

    cand = store.fresh_candidates(10, _NOW.isoformat())[0]
    assert cand.expires_at == _NOW + timedelta(hours=24)


async def test_callback_expires_after_its_ttl(tmp_path):
    """만료가 없으면 3주 전에 끝난 얘기를 어느 날 꺼낸다(feed.md 5의 느린 버전)."""
    store, feed, _ = _callback_feed(tmp_path, callback_ttl_hours=168)
    await feed.collect(_NOW)

    assert feed.get_fresh_topics(now=_NOW + timedelta(days=8)) == []


async def test_callback_prompt_sees_only_user_turns(tmp_path):
    turns = _user_turns() + _turns("나비가 한 말", role="assistant")
    _, feed, brain = _callback_feed(tmp_path, turns=turns)

    await feed.collect(_NOW)

    prompt = brain.requests[0].messages[-1].text
    assert "나비가 한 말" not in prompt
    assert "이직 얘기 0" in prompt


async def test_collect_skips_callback_when_turns_are_too_few(tmp_path):
    """갓 설치한 데몬이 턴 하나로 LLM을 태우지 않는다 — 그리고 그건 장애가 아니다."""
    store, feed, brain = _callback_feed(tmp_path, turns=_user_turns(1))

    assert await feed.collect(_NOW) == 0
    assert brain.requests == []  # LLM 미호출
    assert store.last_collect_at() == _NOW.isoformat()  # 배치는 건강


async def test_collect_skips_callback_when_extraction_is_empty(tmp_path):
    """"꺼낼 화제가 없다"는 정당한 답이지 장애가 아니다(feed.md 3.4)."""
    store, feed, _ = _callback_feed(tmp_path, reply="뽑을 만한 화제가 없습니다.")

    assert await feed.collect(_NOW) == 0
    assert store.last_collect_at() == _NOW.isoformat()
    assert await feed.maybe_collect(_NOW + timedelta(minutes=31)) is False  # 백오프 아님


async def test_callback_dedup_prevents_repeat_within_window(tmp_path):
    """같은 화제를 12시간마다 다시 꺼내지 않는다 — 그리고 그 배치도 건강하다.

    회귀 방지: attempted를 dedup 앞에서 세면 이 정상 상태가 장애로 오판돼
    12시간 간격이 30분 백오프로 무너진다.
    """
    store, feed, _ = _callback_feed(tmp_path, callback_dedup_window_s=604800)

    assert await feed.collect(_NOW) == 1
    later = _NOW + timedelta(hours=12)
    assert await feed.collect(later) == 0

    assert store.last_collect_at() == later.isoformat()  # 건강 판정
    assert len(store.fresh_candidates(10, later.isoformat())) == 1


async def test_callback_recurs_after_dedup_window(tmp_path):
    """영구 봉인 방지 — 창을 넘겨도 사용자가 그 얘길 계속하면 새 후보가 난다."""
    store, feed, _ = _callback_feed(
        tmp_path, callback_dedup_window_s=604800, callback_ttl_hours=24_000
    )
    await feed.collect(_NOW)

    later = _NOW + timedelta(days=8)
    assert await feed.collect(later) == 1
    assert len(store.fresh_candidates(10, later.isoformat())) == 2


async def test_callback_label_drift_dedups_to_the_same_key(tmp_path):
    """LLM이 '이직'과 '이직 고민'을 번갈아 불러도 같은 화제로 본다."""
    store, feed, brain = _callback_feed(tmp_path)
    await feed.collect(_NOW)

    brain._reply = "[topic:이직 고민] 사용자가 여전히 이직을 고민한다."
    assert await feed.collect(_NOW + timedelta(hours=12)) == 0


async def test_extraction_failure_is_treated_as_outage(tmp_path):
    """소스 0 + 콜백만인 구성에서 추출기가 죽으면 장애로 잡혀야 한다.

    회귀 방지: `if not self._sources: return True`가 이 경우를 통째로 삼켰다.
    """
    store, feed, _ = _callback_feed(tmp_path, boom=True, retry_backoff_s=1800)

    assert await feed.collect(_NOW) == 0
    assert store.last_collect_at() is None  # 성공으로 기록하지 않는다
    assert await feed.maybe_collect(_NOW + timedelta(minutes=31)) is True


async def test_callback_only_configuration_is_healthy_when_quiet(tmp_path):
    """콜백만 켜고 대화가 없는 상태는 정상이다."""
    store, feed, _ = _callback_feed(tmp_path, turns=[], collect_interval_s=3600)

    assert await feed.collect(_NOW) == 0
    assert store.last_collect_at() == _NOW.isoformat()
    assert await feed.maybe_collect(_NOW + timedelta(minutes=31)) is False


async def test_no_contributors_at_all_is_not_a_failure(tmp_path):
    """소스도 콜백도 없으면 할 일이 없는 것이지 장애가 아니다."""
    store, feed = _feed(tmp_path, [], collect_interval_s=3600, retry_backoff_s=1800)

    assert await feed.collect(_NOW) == 0
    assert store.last_collect_at() == _NOW.isoformat()
    assert await feed.maybe_collect(_NOW + timedelta(minutes=31)) is False


async def test_callback_is_extracted_before_rss_items(tmp_path):
    """쿼터가 마르면 뉴스가 아니라 콜백이 살아남아야 한다(feed.md 1)."""
    _, feed, brain = _callback_feed(tmp_path, sources=[FakeSource()])

    await feed.collect(_NOW)

    assert brain.requests[0].system is CALLBACK_SYSTEM


async def test_callback_candidate_outranks_news_in_fresh_topics(tmp_path):
    """콜백은 published_at이 없어 fetched_at 폴백으로 뉴스보다 앞선다(의도된 정렬)."""
    fresh_news = _item("한 시간 전 기사", "g1", published=_NOW - timedelta(hours=1))
    _, feed, _ = _callback_feed(tmp_path, sources=[FakeSource(items=[fresh_news])])
    await feed.collect(_NOW)

    assert feed.get_fresh_topics(now=_NOW)[0].source == "memory"


async def test_is_safe_hook_can_block_callback_candidates(monkeypatch, tmp_path):
    """훅이 실제로 콜백 경로에 심을 수 있는 씨앗인지 — RawItem 바인딩을 푼 이유."""
    from navi.feed import collect as collect_mod

    monkeypatch.setattr(
        collect_mod, "_is_safe", lambda *, source, topic_key, text: source != "memory"
    )
    store, feed, _ = _callback_feed(tmp_path, sources=[FakeSource()])

    await feed.collect(_NOW)

    sources = {c.source for c in store.fresh_candidates(10, _NOW.isoformat())}
    assert sources == {"rss"}  # 콜백만 막혔다


async def test_callback_wiring_matches_the_daemon_shape(tmp_path):
    """PR3이 실제로 쓸 형태 — store.recall_recent_for_user 클로저를 그대로 주입."""
    store = MemoryStore(tmp_path / "t.db")
    uid = store.ensure_user("친구")
    for i in range(4):
        store.append_turn("s1", uid, "user", f"이직 얘기 {i}")
    feed = Feed(
        store=store,
        sources=[],
        summarizer=ScriptedBrain(_REPLY),
        model="test-model",
        recall_turns=lambda: store.recall_recent_for_user(uid, 30),
    )

    assert await feed.collect(_NOW) == 1
    assert store.fresh_candidates(10, _NOW.isoformat())[0].source == "memory"


async def test_rss_summary_is_still_cleaned_after_drain_change(tmp_path):
    """drain에서 clean_summary를 뺀 뒤에도 rss 요약은 서식이 벗겨져야 한다."""

    class HeadingBrain(EchoBrain):
        async def generate_stream(self, request):
            self.last_result = None
            reply = "# 요약\n\n어제 경기가 있었다."
            yield reply
            self.last_result = BrainResult(full_text=reply, usage=Usage(0, 0))

    store, feed = _feed(tmp_path, [FakeSource()], HeadingBrain())
    await feed.collect(_NOW)

    assert store.fresh_candidates(10, _NOW.isoformat())[0].summary == "어제 경기가 있었다."
