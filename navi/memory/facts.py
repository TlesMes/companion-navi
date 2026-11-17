"""대화에서 사실을 뽑아 쌓는다 (Phase 4, design/memory.md).

"한 달 전 이야기를 다시 꺼낸다"를 만드는 쪽이다. 최근 대화 창을 넓히는 대신, 창 밖으로
밀려날 대화에서 **사용자에 대해 아는 것**을 문장으로 남긴다.

원칙 안에서의 자리: 언제 추출할지(새 턴 수·백오프·커서)는 전부 결정론이고, LLM은
"무엇이 기억할 만한가"에만 쓴다. 저가 티어 1회 호출, 대화 경로 밖(백그라운드).

피드의 대화 콜백 추출(feed/summarize.py)과 닮았지만 따로 둔다 — 저쪽은 "먼저 말 걸 재료"라
며칠 살다 사라지고, 이쪽은 기억이라 대체되거나 만료될 때까지 남는다. 주기도 다르다:
콜백은 12시간 배치, 사실은 새 대화가 쌓이는 대로.

`navi.memory` 패키지 진입점에서 내보내지 않는다 — 이 모듈이 feed.summarize의 drain을
쓰고 feed는 memory를 쓰기 때문에, 진입점에 올리면 임포트가 맞물린다.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta, tzinfo

from navi.brain.base import BrainAdapter
from navi.feed.summarize import clean_summary, drain
from navi.memory.store import MemoryStore
from navi.models import Fact, LlmRequest, Message, Turn

log = logging.getLogger(__name__)

# 분류는 "무엇을 항상 넣고 무엇을 흘려보낼지"를 가르기 위한 것이다. 최근 순 하나로만
# 고르면 오래됐지만 중요한 것(가족 이름)이 새 잡사실에 밀린다. 점수 대신 종류로 푼다:
#   profile    거의 안 바뀜 — 만료 없음. 개수가 적어 주입 때 항상 넣는다.
#   preference 좋아함·싫어함 — 만료 없음.
#   ongoing    요즘 겪는 일 — 낡는다. 기본 수명을 둔다.
#   plan       날짜가 있는 예정 — 그 날이 지나면 넣을 이유가 없다.
# 넷으로 시작한 건 추출기가 헷갈리지 않을 만큼 경계가 뚜렷해서다. 값은 튜닝 대상이라
# DB CHECK가 아니라 여기서 검증한다(schema.sql 주석).
FACT_KINDS = ("profile", "preference", "ongoing", "plan")

# 추출기가 끝 날짜를 못 준 사실의 기본 수명(일). plan에 날짜가 없으면 "언젠가 할 일"이라
# 한 달이면 낡고, ongoing은 한 철쯤 간다. 근거는 감이다 — 실제로 낡은 사실이 대화에
# 끼어드는지 보고 고친다.
_DEFAULT_TTL_DAYS = {"plan": 30, "ongoing": 90}

FACT_SYSTEM = (
    "너는 대화에서 '사용자에 대해 오래 기억해 둘 사실'을 뽑는 추출기다. 규칙: "
    "①한 줄에 하나씩 `[fact:종류|src:T번호] 문장` 형식으로만 쓴다. 그 밖의 말은 쓰지 않는다. "
    "덧붙이는 표시(until·replaces)는 전부 **대괄호 안**에 넣는다 — "
    "`[fact:plan|src:T12|until:2026-12-01|replaces:F3] 문장`. 문장 뒤에는 아무것도 붙이지 않는다. "
    "②종류는 넷 중 하나다 — profile(이름·가족·직업·사는 곳처럼 잘 안 바뀌는 신상), "
    "preference(좋아하거나 싫어하는 것), ongoing(요즘 겪고 있는 일이나 상태), "
    "plan(앞으로 할 예정인 일). "
    "③문장은 사용자를 '사용자'라고 3인칭으로 부르고, 앞뒤 대화 없이 혼자 읽어도 뜻이 통하게 "
    "쓴다. '그것'·'걔' 같은 말 대신 가리키는 대상을 적는다. "
    "'내일'·'다음 주 금요일'·'요번 주말' 같은 상대적인 날짜도 쓰지 않는다 — '11월 20일'처럼 "
    "실제 날짜로 바꿔 적는다. 날짜는 암산하지 말고 [달력]에서 찾는다: 한 줄이 한 주(월~일)다. "
    "그 말이 나온 대화 줄의 날짜가 든 줄이 '이번 주', 바로 아랫줄이 '다음 주', 그 아랫줄이 "
    "'다다음 주'다. 그 줄에서 해당 요일의 날짜를 읽는다. "
    "④사용자가 직접 말한 것만 적는다. 나비의 말은 맥락으로만 읽고 사실로 뽑지 않는다. "
    "추측하지 않는다. "
    "⑤인사·잡담·지나가는 기분은 뽑지 않는다. 일주일 뒤에도 쓸모 있을 것만 고른다. "
    "⑥src에는 그 사실이 나온 대화 줄의 T번호를 적는다. "
    "⑦plan에 끝나는 날짜가 있으면 대괄호 안에 `|until:2026-12-01`을 넣는다. 문장에 적은 날짜와 같은 "
    "방법으로 [달력]에서 찾는다('오늘'이 아니라 **말한 날** 기준이다). 모르면 붙이지 않는다. "
    "⑧새 사실이 '이미 아는 사실' 중 하나를 바꾸거나 뒤집으면 대괄호 안에 `|replaces:F번호`를 "
    "넣는다. "
    "이미 아는 사실과 같은 내용은 다시 쓰지 않는다. "
    "⑨한국어로 쓴다. "
    "⑩뽑을 게 없으면 아무것도 쓰지 않는다."
)

_FENCE = re.compile(r"^```[^\n]*\n(.*?)\n?```$", re.DOTALL)
_FACT_RE = re.compile(r"^\s*\[fact:\s*([^\]\n]{1,80})\]\s*(\S.*)$", re.MULTILINE)
_NUMBER = re.compile(r"\d+")
# 문장 **뒤**에 붙은 속성 — `…예정이다.|until:2026-11-20`. 규칙은 대괄호 안에 넣으라고 하지만
# 실측(2026.11.15)에서 Haiku가 한 응답 통째로 뒤에 붙였다. 그대로 두면 만료가 안 읽히고
# 문장에 꼬리가 남아 프롬프트로 되먹여진다. 아는 키만 떼어 낸다(문장 속 `|`는 건드리지 않는다).
_TRAILING_ATTRS = re.compile(r"(?:\s*\|\s*(?:src|until|replaces)\s*:\s*[^|\s]+)+\s*$")
_WHITESPACE = re.compile(r"\s+")
_MAX_TURN_CHARS = 300  # 붙여넣기 한 덩어리가 프롬프트를 삼키는 걸 막는다


@dataclass(frozen=True)
class ParsedFact:
    kind: str
    text: str
    src: int | None = None  # 출처 turn_id (모델이 준 값 — 검증 전)
    until: date | None = None
    replaces: int | None = None  # 대체할 fact_id (모델이 준 값 — 검증 전)


def _number(value: str) -> int | None:
    """`T482`·`F18`·`#18`·`482` 어느 모양이든 숫자만 읽는다 — 접두는 모델이 자주 흘린다."""
    hit = _NUMBER.search(value)
    return int(hit.group()) if hit else None


def _date(value: str) -> date | None:
    try:
        return date.fromisoformat(value.strip())
    except ValueError:
        return None  # 못 읽는 날짜는 "모름"으로 — 사실 자체를 버릴 이유는 아니다


def parse_facts(text: str, max_facts: int) -> list[ParsedFact]:
    """`[fact:종류|키:값…] 문장` 줄들을 읽는다. 못 읽는 줄은 그 줄만 버린다.

    줄 태그인 이유는 콜백 추출과 같다(feed/summarize.py parse_callbacks) — 모델이 앞에
    머리말을 붙여도 그 줄만 안 맞고 나머지는 산다. JSON이면 통째로 터진다.

    속성은 순서를 가리지 않고, 모르는 키와 못 읽는 값은 조용히 무시한다. 종류를 못 읽는
    줄만 버린다 — 종류가 없으면 주입 규칙(무엇을 항상 넣는가)을 적용할 수 없다.
    """
    fence = _FENCE.match(text.strip())
    if fence:
        text = fence.group(1)

    facts: list[ParsedFact] = []
    seen: set[str] = set()
    for head, body in _FACT_RE.findall(text):
        parts = [p.strip() for p in head.split("|")]
        kind = parts[0].casefold()
        if kind not in FACT_KINDS:
            continue
        pairs = [p for p in parts[1:] if ":" in p]
        tail = _TRAILING_ATTRS.search(body)
        if tail:
            body = body[: tail.start()]
            # 뒤에 붙은 것을 먼저 넣어 대괄호 안의 값이 이기게 한다(둘 다 있으면 규칙대로 쓴 쪽).
            pairs = [p.strip() for p in tail.group().split("|") if ":" in p] + pairs
        attrs = dict(p.split(":", 1) for p in pairs)
        attrs = {k.strip().casefold(): v for k, v in attrs.items()}
        sentence = clean_summary(body)
        key = normalize_fact_text(sentence)
        if not key or key in seen:
            continue
        seen.add(key)
        facts.append(
            ParsedFact(
                kind=kind,
                text=sentence,
                src=_number(attrs["src"]) if "src" in attrs else None,
                until=_date(attrs["until"]) if "until" in attrs else None,
                replaces=_number(attrs["replaces"]) if "replaces" in attrs else None,
            )
        )
        if len(facts) >= max_facts:
            break
    return facts


def normalize_fact_text(text: str) -> str:
    """같은 문장 판정용 — 공백·대소문자·끝 문장부호만 흡수한다.

    뜻이 같은 다른 문장("동생은 민준" / "동생 이름이 민준")은 못 잡는다. 그건 추출기의
    규칙 ⑧(이미 아는 사실은 다시 쓰지 않는다)에 기대고, 여기는 그 규칙이 샜을 때의
    최소 방어다. 의미 기반 병합은 임베딩을 붙일 때의 일이다.
    """
    return _WHITESPACE.sub("", text).casefold().rstrip(".!。")


def _local_date(moment: datetime, tz: tzinfo | None = None) -> date:
    """말한 날 — tz가 없으면 이 기기의 시간대(단독 모드), 있으면 그 사용자의 시간대(서버 워커)."""
    return moment.astimezone(tz).date()


_WEEKDAYS = "월화수목금토일"


def _dated(moment: datetime, tz: tzinfo | None = None) -> str:
    """`2026-11-12 목` — 요일을 코드가 적어 준다.

    "다음 주 금요일"을 날짜로 바꾸려면 말한 날의 요일이 필요한데, 날짜만 주면 모델이
    요일을 암산한다. 실측(2026.11.15)에서 그 암산이 이틀 틀렸다. 달력 계산은 결정론이
    할 수 있는 일이라 모델에 맡기지 않는다.
    """
    day = _local_date(moment, tz)
    return f"{day.isoformat()} {_WEEKDAYS[day.weekday()]}"


_CALENDAR_WEEKS_AHEAD = 4  # 말한 주 뒤로 몇 주를 더 보여 줄지 — "다다음 주"·"이달 말"까지 덮는다


def _calendar(first: date, last: date) -> list[str]:
    """첫 대화가 든 주부터 마지막 대화 뒤 몇 주까지 — 한 줄이 한 주(월~일).

    요일을 적어 주는 것만으로는 모자랐다(실측 2026.11.15): Haiku가 "목요일에 말한
    '다음 주 금요일'"을 하루씩 틀리게 셌다. 틀린 날짜가 확정된 문장으로 몇 주 남는 건
    상대 날짜가 남는 것보다 나쁘다. 그래서 세는 일을 없앤다 — 모델은 표에서 읽기만 한다.
    한 줄을 한 주로 묶은 건 "다음 주"가 "아랫줄"이 되게 하려는 것이다.
    """
    monday = first - timedelta(days=first.weekday())
    end = last + timedelta(weeks=_CALENDAR_WEEKS_AHEAD)
    lines = []
    while monday <= end:
        week = [monday + timedelta(days=i) for i in range(7)]
        lines.append("  ".join(f"{d.isoformat()}({_WEEKDAYS[d.weekday()]})" for d in week))
        monday += timedelta(weeks=1)
    return lines


def build_fact_prompt(
    turns: list[Turn], known: list[Fact], now: datetime, tz: tzinfo | None = None
) -> str:
    """추출기에 보일 한 덩어리 — 오늘 날짜, 이미 아는 사실, 새 대화.

    나비의 말도 넣는다(콜백 추출과 다른 점). "동생 이름이 뭐야?" → "민준"처럼 사용자의
    답이 질문 없이는 뜻이 안 통하기 때문이다. 사실로 뽑지 말라는 건 규칙 ④가 맡는다.

    번호에 T·F를 붙이는 건 두 번호 공간이 한 프롬프트에 같이 있어서다 — `#18`만 쓰면
    턴인지 사실인지 모델이 헷갈린다.
    """
    lines = [f"오늘: {_dated(now, tz)}", "", "[이미 아는 사실]"]
    if known:
        lines += [f"F{f.fact_id} ({f.kind}) {f.text}" for f in known]
    else:
        lines.append("(없음)")
    if turns:
        days = [_local_date(t.created_at, tz) for t in turns]
        lines += ["", "[달력]", *_calendar(min(days), max(days))]
    lines += ["", "[새 대화]"]
    for turn in turns:
        speaker = "사용자" if turn.role == "user" else "나비"
        day = _dated(turn.created_at, tz)
        lines.append(f"T{turn.turn_id} ({day}) {speaker}: {turn.text[:_MAX_TURN_CHARS]}")
    return "\n".join(lines)


def _expires_at(fact: ParsedFact, now: datetime, tz: tzinfo | None = None) -> datetime | None:
    """만료 시각 — 추출기가 준 끝 날짜가 우선, 없으면 종류별 기본 수명, 그것도 없으면 None."""
    if fact.until is not None:
        # 그 날이 **끝날 때까지** 유효하다. 자정은 사용자 시간대(tz), 없으면 이 기기 시간대.
        end_of_day = datetime.combine(fact.until + timedelta(days=1), time.min)
        end_of_day = end_of_day.replace(tzinfo=tz) if tz is not None else end_of_day.astimezone()
        return end_of_day.astimezone(UTC)
    days = _DEFAULT_TTL_DAYS.get(fact.kind)
    return now + timedelta(days=days) if days is not None else None


@dataclass(frozen=True)
class NewFact:
    """검증을 마친, 적재할 사실 하나."""

    kind: str
    text: str
    source_turn_id: int
    expires_at: datetime | None
    replaces: int | None  # 이미 아는 사실 중 대체할 것. 모델이 지어낸 번호는 여기 오지 않는다


def select_facts(
    parsed: list[ParsedFact],
    *,
    batch_ids: set[int],
    fallback_src: int,
    known: list[Fact],
    now: datetime,
    tz: tzinfo | None = None,
) -> list[NewFact]:
    """추출기의 답을 검증해 적재할 것만 고른다 — 모델이 준 번호는 그대로 믿지 않는다.

    단독 모드(FactExtractor)와 서버 워커(navi/worker)가 같이 쓴다. 규칙이 한 벌이어야
    두 모드의 기억이 같은 모양으로 쌓인다.
    """
    known_ids = {f.fact_id for f in known}
    known_texts = {normalize_fact_text(f.text) for f in known}
    selected: list[NewFact] = []
    for fact in parsed:
        key = normalize_fact_text(fact.text)
        if key in known_texts:
            continue  # 이미 아는 사실 — 규칙 ⑧이 샌 경우
        expires_at = _expires_at(fact, now, tz)
        if expires_at is not None and expires_at <= now:
            continue  # 이미 지난 예정 — 넣자마자 주입에서 빠질 사실이다
        selected.append(
            NewFact(
                kind=fact.kind,
                text=fact.text,
                source_turn_id=fact.src if fact.src in batch_ids else fallback_src,
                expires_at=expires_at,
                replaces=fact.replaces if fact.replaces in known_ids else None,
            )
        )
        known_texts.add(key)
    return selected


class FactExtractor:
    """새로 쌓인 대화를 차례로 먹어 사실을 적재한다. Config를 모른다 — 전부 주입받는다.

    extractor는 대화용 두뇌·피드 요약기와 **다른 인스턴스**여야 한다. 어댑터 하나는 동시
    요청 1건이 계약이라(brain/base.py) 같은 걸 쓰면 last_result가 경합한다.
    """

    def __init__(
        self,
        *,
        store: MemoryStore,
        user_id: int,
        extractor: BrainAdapter,
        model: str,
        min_new_user_turns: int = 4,
        max_rows_per_batch: int = 40,
        max_known_facts: int = 80,
        max_facts_per_batch: int = 10,
        retry_backoff_s: float = 1800.0,
    ) -> None:
        self._store = store
        self._user_id = user_id
        self._extractor = extractor
        self._model = model
        self._min_new_user_turns = min_new_user_turns
        self._max_rows = max_rows_per_batch
        self._max_known = max_known_facts
        self._max_facts = max_facts_per_batch
        self._retry_backoff = timedelta(seconds=retry_backoff_s)
        self._running = False
        self._retry_after: datetime | None = None  # 영속 안 함 — Feed와 같은 이유

    async def maybe_extract(self, now: datetime) -> int | None:
        """조건이 맞으면 한 묶음 추출. 돌았으면 적재 건수, 안 돌았으면 None.

        조건은 전부 결정론이다: 재진입 아님 → 백오프 아님 → 새 사용자 턴이 충분함.
        한 번에 한 묶음만 먹는다 — 밀린 대화가 많아도 호출 한 번이 길어지지 않고,
        남은 건 다음 tick이 이어 먹는다.
        """
        if self._running:
            return None
        now = now.astimezone(UTC)
        if self._retry_after is not None and now < self._retry_after:
            return None
        cursor = self._store.fact_cursor(self._user_id)
        turns = self._store.turns_after(self._user_id, cursor, self._max_rows)
        if not turns:
            return None
        user_turns = [t for t in turns if t.role == "user"]
        if len(user_turns) < self._min_new_user_turns:
            if len(turns) >= self._max_rows:
                # 묶음이 꽉 찼는데 사용자 말이 거의 없다(응답 없는 선제 발화가 줄지은 경우).
                # 넘기지 않으면 같은 묶음만 영원히 다시 읽어 그 뒤의 대화에 못 닿는다.
                self._store.set_fact_cursor(self._user_id, turns[-1].turn_id)
            return None  # LLM 미호출 — 턴 몇 개마다 호출하지 않는다

        self._running = True
        try:
            return await self._extract(turns, user_turns, now)
        finally:
            self._running = False

    async def _extract(self, turns: list[Turn], user_turns: list[Turn], now: datetime) -> int:
        known = self._store.active_facts(self._user_id, now.isoformat(), self._max_known)
        request = LlmRequest(
            system=FACT_SYSTEM,
            messages=[Message(role="user", text=build_fact_prompt(turns, known, now))],
            model=self._model,
        )
        try:
            reply = await drain(self._extractor, request)
        except Exception:
            # 커서를 안 옮긴다 — 이 대화는 아직 추출기에 보이지 않은 것이다.
            self._retry_after = now + self._retry_backoff
            log.warning(
                "사실 추출 실패 — %.0f분 뒤 재시도",
                self._retry_backoff.total_seconds() / 60,
                exc_info=True,
            )
            return 0
        self._retry_after = None

        stored = self._apply(parse_facts(reply, self._max_facts), turns, user_turns, known, now)
        # 뽑은 게 없어도 옮긴다 — "기억할 게 없다"도 정당한 답이고, 안 옮기면 같은 대화를
        # 매번 다시 보낸다(fact_cursor가 따로 있는 이유).
        self._store.set_fact_cursor(self._user_id, turns[-1].turn_id)
        log.info("사실 추출 — 대화 %d행에서 %d건 적재", len(turns), stored)
        return stored

    def _apply(
        self,
        parsed: list[ParsedFact],
        turns: list[Turn],
        user_turns: list[Turn],
        known: list[Fact],
        now: datetime,
    ) -> int:
        """추출기의 답을 검증해 적재한다 — 모델이 준 번호는 그대로 믿지 않는다."""
        selected = select_facts(
            parsed,
            batch_ids={t.turn_id for t in turns},
            # 출처를 못 읽으면 이 묶음의 마지막 사용자 말
            fallback_src=user_turns[-1].turn_id,
            known=known,
            now=now,
        )
        for fact in selected:
            new_id = self._store.insert_fact(
                self._user_id,
                fact.kind,
                fact.text,
                source_turn_id=fact.source_turn_id,
                expires_at=fact.expires_at.isoformat() if fact.expires_at else None,
            )
            if fact.replaces is not None:
                self._store.supersede_fact(self._user_id, fact.replaces, new_id)
        return len(selected)
