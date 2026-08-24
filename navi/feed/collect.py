"""수집 오케스트레이터 (arch 4.10, D13/feed.md 3.3).

나비가 먼저 말 걸 재료를 하루 1~2회 배치로 모아 topic_candidate에 쌓고, 발화 시점에
미사용·유효 후보를 꺼내 준다. 수집·중복방지·주기는 전부 결정론 — LLM은 요약에만.

이 PR 범위는 ① RSS 경로뿐이다. ② 대화 콜백(source='memory')은 feed.md PR2,
데몬 배선과 config는 PR3.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from navi.brain.base import BrainAdapter
from navi.feed.base import FeedSource, RawItem
from navi.feed.summarize import extract_callbacks, summarize_item
from navi.memory import MemoryStore
from navi.models import TopicCandidate, Turn

log = logging.getLogger(__name__)

# 갓 설치한 데몬이 턴 하나로 LLM을 태우지 않게 하는 하한(사용자 턴 기준).
_MIN_TURNS_FOR_EXTRACT = 3


def _is_safe(*, source: str, topic_key: str, text: str) -> bool:
    """안전 필터 훅 자리 — D13 MVP에선 항상 True(feed.md 5의 씨앗).

    관심사가 이미 큐레이션돼(사용자가 고른 피드) rss엔 지금 실해가 없다. 위험은 콜백
    쪽이다 — 사용자의 무거운 화제(이별·질병·자책)를 선제로 되꺼낼 수 있다.

    시그니처가 RawItem이 아닌 이유: 그러면 콜백 텍스트를 볼 수가 없어, 훅이 지목된
    위험에 정작 심을 수 없는 씨앗이 된다. source를 받는 건 규칙이 채워질 때 둘에 다른
    규칙이 필요해서다 — 뉴스의 "질병"은 정상 소재고 콜백의 "질병"은 사용자 본인 얘기다.
    인자가 전부 str이라 순서 실수가 조용히 통과하지 않게 키워드 전용으로 둔다.

    채울 땐 결정론 규칙만 — 안전 게이트는 LLM에 안 맡긴다(원칙 2). 콜백은 추출 *출력*에
    걸고 있는데, 무거운 대화가 애초에 추출기에 안 닿게 하는 입력측 게이트가 더 안전한
    자리다. 규칙을 채울 때 두 자리를 다 검토할 것.
    """
    return True


def _utc(moment: datetime) -> datetime:
    """들어온 시각을 UTC aware로 맞춘다 — Feed의 모든 시각 진입점이 이걸 통과한다.

    저장소가 타임스탬프를 **문자열로 비교**하고(store.fresh_candidates) 기준 포맷이
    _now_iso()의 UTC aware라, naive가 섞이면 TTL 필터가 조용히 오작동한다. 게다가
    aware와 naive를 빼거나 min()하면 TypeError다.

    naive는 로컬 시각으로 간주한다 — 이 데몬에서 naive의 출처가 DaemonCore의
    `wall_now = datetime.now`(daemon.py)이고 그게 로컬이기 때문이다. astimezone()의
    기본 동작이 정확히 그 해석이고, count_initiations_today가 이미 같은 변환을 쓴다.
    """
    return moment.astimezone(UTC)


def _dedup_key(source: str, topic_key: str, raw: str) -> str:
    digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]
    return f"{source}:{topic_key}:{digest}"


@dataclass(frozen=True)
class _ContributorResult:
    """기여자 하나의 배치 결과 — 건수만으로는 정상과 장애가 안 갈려서 셋을 따로 센다.

    기여자는 RSS 소스이거나 대화 콜백이다. 콜백은 "소스"가 아니라(외부 fetch 없음)
    이름을 중립화했지만, 세 필드의 뜻은 두 경로에서 그대로 성립해 _is_healthy를
    무수정으로 재사용한다.
    """

    responded: bool  # 기여자가 응답했는가 (빈 피드·0턴도 응답이다)
    attempted: int  # dedup을 통과해 **LLM 호출을 시도한** 횟수
    stored: int  # 그중 실제로 적재된 건수


class Feed:
    """수집 배치와 후보 인출을 소유한다. Config를 모른다 — 전부 주입받는다(PR3에서 배선).

    summarizer는 대화용 두뇌와 공유하면 안 된다: 어댑터 하나는 동시 요청 1건이 계약이라
    (brain/base.py) 선제 발화 중에 수집이 돌면 last_result가 경합한다. 같은 이유로
    아래 요약 루프는 순차 처리다 — asyncio.gather 금지.
    """

    def __init__(
        self,
        *,
        store: MemoryStore,
        sources: Sequence[FeedSource],
        summarizer: BrainAdapter,
        model: str,
        recall_turns: Callable[[], list[Turn]] | None = None,
        collect_interval_s: float = 43200.0,  # 12h → 하루 1~2회
        retry_backoff_s: float = 1800.0,  # 배치가 전멸했을 때만 쓰는 짧은 재시도 간격
        fresh_topics_k: int = 3,
        rss_ttl_hours: float = 96.0,
        max_items_per_source: int = 5,
        max_callbacks: int = 2,
        callback_ttl_hours: float = 168.0,  # 7일 — 값은 PR3에서 config로 뺀다
        callback_dedup_window_s: float = 604800.0,  # 7일 — TTL과 별개 손잡이
    ) -> None:
        self._store = store
        self._sources = list(sources)
        self._summarizer = summarizer
        self._model = model
        self._collect_interval_s = collect_interval_s
        self._retry_backoff = timedelta(seconds=retry_backoff_s)
        self._fresh_topics_k = fresh_topics_k
        self._rss_ttl = timedelta(hours=rss_ttl_hours)
        self._max_items_per_source = max_items_per_source
        self._recall_turns = recall_turns
        self._max_callbacks = max_callbacks
        self._callback_ttl = timedelta(hours=callback_ttl_hours)
        self._callback_dedup_window_s = callback_dedup_window_s
        self._collecting = False
        # 장애 배치 뒤 재시도 시각. 영속 안 한다 — 재기동은 대개 사람이 뭔가 고친 뒤라
        # 한 번 더 시도해 보는 게 맞고, 백오프는 원래 일시적 상태다.
        self._retry_after: datetime | None = None

    # ─── 수집 ────────────────────────────────────────────────

    async def maybe_collect(self, now: datetime) -> bool:
        """시각 게이트를 통과할 때만 수집. 돌았으면 True.

        재진입 가드가 있는 건 PR3이 이걸 백그라운드 task로 띄울 것이기 때문이다 —
        collect는 (피드 HTTP + 아이템 LLM)이라 수십 초짜리고, tick에서 인라인으로
        await하면 그동안 디스패치가 멎는다.
        """
        if self._collecting:
            return False
        now = _utc(now)
        if self._retry_after is not None and now < self._retry_after:
            return False  # 직전 배치가 전멸 — 백오프가 끝날 때까지 쉰다
        last = self._store.last_collect_at()
        if last is not None and not self._interval_passed(last, now):
            return False
        self._collecting = True
        try:
            await self.collect(now)
        finally:
            self._collecting = False
        return True

    def _interval_passed(self, last_iso: str, now: datetime) -> bool:
        try:
            last = datetime.fromisoformat(last_iso)
        except ValueError:
            log.warning("last_collect_at을 못 읽어 수집을 허용한다: %r", last_iso)
            return True
        return (now - last).total_seconds() >= self._collect_interval_s

    async def collect(self, now: datetime) -> int:
        """배치 1회 — 적재한 후보 수를 돌려준다.

        collect 자체를 to_thread에 넣지 않는다: 본문이 요약 LLM을 await해야 하고(워커
        스레드에선 두 번째 이벤트 루프가 필요해 깨진다), MemoryStore의 sqlite 커넥션은
        만든 스레드에 묶여 있다. 진짜 블로킹인 HTTP fetch만 스레드로 뺀다.

        끝에 수집 시각을 기록하는 건 **배치가 건강했을 때뿐**이다 — 아래 _is_healthy 참고.
        """
        now = _utc(now)
        # 콜백을 먼저 돌린다 — gemini 무료 티어는 분당 5·하루 20이라(실측 2026.08.02)
        # rss가 쿼터를 먼저 태우면 콜백 1콜이 429에 걸린다. feed.md 1이 "뉴스보다 콜백이
        # 값지다"고 정한 이상, 쿼터가 마르면 살아남아야 하는 건 콜백이다.
        results: list[_ContributorResult] = []
        if self._recall_turns is not None:
            results.append(await self._collect_callbacks(now))
        results.extend([await self._collect_source(source, now) for source in self._sources])
        stored = sum(r.stored for r in results)
        if self._is_healthy(results):
            self._store.set_last_collect_at(now.isoformat())
            self._retry_after = None
        else:
            self._retry_after = now + self._retry_backoff
            log.warning(
                "수집 배치 전멸 — %.0f분 뒤 재시도(정상 간격을 기다리지 않는다)",
                self._retry_backoff.total_seconds() / 60,
            )
        return stored

    def _is_healthy(self, results: list[_ContributorResult]) -> bool:
        """이 배치를 "돌았다"고 볼 수 있는가 — 적재 건수로는 못 가린다.

        stored == 0은 실패가 아니다. 피드에 새 기사가 없으면 dedup으로 전부 걸러져
        정상적으로 0이 된다. 그걸 실패로 보면 조용한 피드를 매 tick 두들긴다.

        가려야 하는 건 두 가지 전멸이다:
          - 소스가 하나도 응답 안 함 → 네트워크·피드 장애
          - 요약을 시도했는데 한 건도 못 건짐 → 요약기 장애(쿼터 소진·키 만료)
        두 번째가 특히 실재한다 — 실측(2026.08.02)에서 gemini 무료 티어 일일 20회를
        소진해 429가 났다. 그때 소스는 멀쩡하고 새 기사도 있으니 "조용한 피드"와
        구분이 안 되고, 안 가리면 고칠 수 있는 장애를 12시간 방치하게 된다.
        """
        if not results:
            return True  # 기여자 미등록 — 할 일이 없는 것이지 장애가 아니다
        if not any(r.responded for r in results):
            return False
        attempted = sum(r.attempted for r in results)
        return attempted == 0 or sum(r.stored for r in results) > 0

    async def _collect_callbacks(self, now: datetime) -> _ContributorResult:
        """② 사용자가 예전에 한 말에서 재료를 만든다 (feed.md 3.4).

        LLM 호출은 배치당 1회. 추출 결과가 비는 건 장애가 아니라 "꺼낼 화제가 없다"는
        정당한 답이라 responded=True·attempted=0으로 보고한다(조용한 피드와 같은 취급).
        """
        assert self._recall_turns is not None
        try:
            # to_thread로 감싸지 않는다 — 이 클로저는 대개 MemoryStore를 닫아 잡고 있고
            # sqlite 커넥션은 만든 스레드에 묶여 있다(워커에서 쓰면 ProgrammingError).
            # rss의 fetch와 정반대 경우다: 저건 진짜 블로킹 HTTP고 DB를 안 건드리지만,
            # 이건 작은 SELECT 하나라 루프 스레드에서 그냥 하는 게 맞다.
            turns = self._recall_turns()
        except Exception:
            log.warning("최근 대화를 못 읽어 콜백을 건너뛴다", exc_info=True)
            return _ContributorResult(responded=False, attempted=0, stored=0)

        # 나비 자기 말에서 화제를 뽑아 되물으면 세계관이 깨진다 — 사용자 턴만 센다.
        # 필터를 주입 클로저에 두면 배선처마다 복제되고 테스트가 안 되므로 여기가 주인.
        user_turns = [t for t in turns if t.role == "user"]
        if len(user_turns) < _MIN_TURNS_FOR_EXTRACT:
            return _ContributorResult(responded=True, attempted=0, stored=0)  # LLM 미호출

        try:
            pairs = await extract_callbacks(
                self._summarizer, self._model, turns, now, self._max_callbacks
            )
        except Exception:
            log.warning("콜백 추출 실패", exc_info=True)
            return _ContributorResult(responded=True, attempted=1, stored=0)  # 장애로 본다

        bucket = int(now.timestamp() // self._callback_dedup_window_s)
        expires_at = (now + self._callback_ttl).isoformat()
        stored = 0
        attempted = 0
        for label, summary in pairs:
            if not _is_safe(source="memory", topic_key=label, text=summary):
                continue
            key = _dedup_key("memory", label, f"win:{bucket}")
            if self._store.candidate_exists(key):
                continue  # 창 안에서 이미 꺼낸 화제 — 정상이지 장애가 아니다
            attempted += 1  # dedup **후에** 센다 — 아래 주석 참고
            inserted = self._store.insert_candidate(
                source="memory",
                topic_key=label,
                summary=summary,
                dedup_key=key,
                fetched_at=now.isoformat(),
                published_at=None,  # 발행 개념이 없다 → 정렬은 fetched_at 폴백(뉴스보다 앞)
                expires_at=expires_at,
            )
            if inserted is not None:
                stored += 1
        # attempted를 dedup 뒤에 세는 이유: 같은 화제가 창 내내 걸리는 건 **정상**인데,
        # 앞에서 세면 attempted>0·stored==0이 돼 _is_healthy가 장애로 오판하고 12h 간격이
        # 30분 백오프로 무너진다. rss가 candidate_exists를 요약 전에 거는 것과 같은 이유다.
        return _ContributorResult(responded=True, attempted=attempted, stored=stored)

    async def _collect_source(self, source: FeedSource, now: datetime) -> _ContributorResult:
        try:
            items = await asyncio.to_thread(source.fetch)
        except Exception:
            # 어댑터가 이미 삼키지만(계약) 이중 방어 — 한 소스가 배치를 죽이지 않는다.
            log.warning("소스 수집 실패, 건너뛴다: %s", source.topic_key, exc_info=True)
            return _ContributorResult(responded=False, attempted=0, stored=0)

        pending: list[tuple[RawItem, str]] = []
        for item in items:
            if not _is_safe(
                source="rss",
                topic_key=source.topic_key,
                text=f"{item.title}\n{item.summary}",
            ):
                continue
            key = _dedup_key("rss", source.topic_key, item.identity)
            if self._store.candidate_exists(key):
                continue  # 요약 *전에* 거른다 — 이미 있는 기사에 LLM을 태우지 않는다
            pending.append((item, key))
            if len(pending) >= self._max_items_per_source:
                break  # 첫 수집에서 큰 피드를 만나도 LLM 호출이 폭주하지 않게

        stored = 0
        for item, key in pending:  # 순차 — 어댑터당 동시 1요청 계약
            try:
                summary = await summarize_item(self._summarizer, self._model, item)
            except Exception:
                log.warning("요약 실패, 이 항목만 건너뛴다: %s", item.title, exc_info=True)
                continue
            if not summary:
                # 빈 요약을 적재하면 나중에 빈 트리거로 선제 발화가 나간다.
                log.warning("요약이 비어 이 항목을 버린다: %s", item.title)
                continue
            published = _utc(item.published) if item.published else None
            inserted = self._store.insert_candidate(
                source="rss",
                topic_key=source.topic_key,
                summary=summary,
                dedup_key=key,
                fetched_at=now.isoformat(),
                published_at=published.isoformat() if published else None,
                expires_at=self._expires_at(published, now),
            )
            if inserted is not None:
                stored += 1
        return _ContributorResult(responded=True, attempted=len(pending), stored=stored)

    def _expires_at(self, published: datetime | None, now: datetime) -> str:
        """TTL 기준은 발행 시각과 수집 시각 중 **이른 쪽**.

        수집 시각만 쓰면 이미 이틀 된 기사가 거기서 또 TTL만큼 살아, 나비가 "어제 소식"인
        양 나흘 전 기사를 꺼낸다(실측 2026.08.02: 한 해외 매체의 피드 맨 앞 항목이 41시간
        경과). 반대로 발행 시각만 쓰면 발행이 뜸한 매체가 통째로 무용지물이 되므로 TTL을
        넉넉히(기본 96h) 잡아 상쇄한다. min을 쓰는 건 발행 시각이 미래인 피드(시계 어긋남·
        예약 발행)에 대한 방어이기도 하다.
        """
        base = min(published, now) if published else now
        return (base + self._rss_ttl).isoformat()

    # ─── 인출 (PR3에서 데몬이 쓴다) ──────────────────────────

    def get_fresh_topics(
        self, k: int | None = None, *, now: datetime | None = None
    ) -> list[TopicCandidate]:
        """미사용·TTL 유효 후보. 객체로 돌려주는 건 호출부가 mark_used에 id가 필요해서다.

        pick_topic에는 .summary만 뽑아 넘긴다 — 3층 계약(list[str])을 안 넓힌다.

        k는 None(미지정)과 0(하나도 필요 없음)을 구분한다 — `k or 기본값`으로 쓰면 0이
        falsy라 기본값으로 뒤바뀌어, 호출부가 억제하려던 선제 발화가 나간다.
        """
        moment = _utc(now) if now else datetime.now(UTC)
        limit = k if k is not None else self._fresh_topics_k
        return self._store.fresh_candidates(limit, moment.isoformat())

    def mark_used(self, candidate_id: int, now: datetime | None = None) -> None:
        """선제 발화에 썼다고 표시 — 같은 이슈로 또 말 걸지 않게.

        후보의 수명(fetch→선택→used)은 데몬이 소유한다. 3층 pick_topic은 DB를 안 건드리는
        순수 힌트 생성기로 남긴다.
        """
        moment = _utc(now) if now else datetime.now(UTC)
        self._store.mark_candidate_used(candidate_id, moment.isoformat())
