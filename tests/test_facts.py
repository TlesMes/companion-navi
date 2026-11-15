"""사실 추출 (Phase 4, design/memory.md) — 파서·프롬프트·추출기·저장소."""

from datetime import UTC, date, datetime, timedelta

from navi.brain.base import BrainAdapter
from navi.memory import MemoryStore
from navi.memory.facts import (
    FACT_KINDS,
    FactExtractor,
    ParsedFact,
    build_fact_prompt,
    normalize_fact_text,
    parse_facts,
)
from navi.models import BrainResult, Usage

_NOW = datetime(2026, 11, 15, 3, 0, tzinfo=UTC)


class ScriptedBrain(BrainAdapter):
    """정해 둔 답을 돌려주는 추출기 대역 — 받은 요청을 기록한다."""

    def __init__(self, reply="", *, boom: bool = False):
        super().__init__()
        self._reply = reply
        self._boom = boom
        self.requests = []

    async def generate_stream(self, request):
        self.requests.append(request)
        if self._boom:
            raise RuntimeError("추출기 장애")
        reply = self._reply(request) if callable(self._reply) else self._reply
        self.last_result = BrainResult(full_text=reply, usage=Usage(0, 0))
        yield reply


def _store(tmp_path):
    store = MemoryStore(tmp_path / "t.db")
    return store, store.ensure_user("친구")


def _talk(store, uid, *lines, role="user"):
    return [store.append_turn("s", uid, role, line) for line in lines]


def _extractor(store, uid, brain, **kwargs):
    kwargs.setdefault("min_new_user_turns", 2)
    return FactExtractor(store=store, user_id=uid, extractor=brain, model="m", **kwargs)


# ─── 파서 ─────────────────────────────────────────────────


def test_parse_reads_kind_and_sentence():
    facts = parse_facts("[fact:profile] 사용자의 동생 이름은 민준이다.", 10)

    assert facts == [ParsedFact(kind="profile", text="사용자의 동생 이름은 민준이다.")]


def test_parse_reads_attributes_in_any_order():
    reply = "[fact:plan|replaces:F18|until:2026-12-01|src:T482] 사용자는 부산으로 이사한다."

    (fact,) = parse_facts(reply, 10)

    assert (fact.src, fact.until, fact.replaces) == (482, date(2026, 12, 1), 18)


def test_parse_ignores_prose_around_the_tagged_lines():
    """머리말을 붙여도 그 줄만 안 맞고 나머지는 산다 — JSON이 아니라 줄 태그인 이유."""
    reply = "아래와 같이 정리했습니다:\n[fact:preference] 사용자는 고수를 싫어한다.\n이상입니다."

    assert [f.text for f in parse_facts(reply, 10)] == ["사용자는 고수를 싫어한다."]


def test_parse_drops_unknown_kind_but_keeps_the_rest():
    """종류가 없으면 주입 규칙을 적용할 수 없다 — 그 줄만 버린다."""
    reply = "[fact:mood] 사용자는 오늘 피곤하다.\n[fact:ongoing] 사용자는 이직을 준비 중이다."

    assert [f.kind for f in parse_facts(reply, 10)] == ["ongoing"]


def test_parse_keeps_fact_when_an_attribute_is_unreadable():
    """날짜를 못 읽은 건 '모름'이지 사실을 버릴 이유가 아니다."""
    (fact,) = parse_facts("[fact:plan|until:다음주|src:몰라] 사용자는 면접을 본다.", 10)

    assert (fact.until, fact.src) == (None, None)


def test_parse_unwraps_code_fence_and_strips_markdown():
    reply = "```\n[fact:profile] **사용자**는 개발자다.\n```"

    assert [f.text for f in parse_facts(reply, 10)] == ["사용자는 개발자다."]


def test_parse_dedups_within_one_reply_and_caps():
    reply = "\n".join(
        ["[fact:profile] 사용자는 개발자다.", "[fact:profile] 사용자는 개발자다"]
        + [f"[fact:preference] 사용자는 {i}번 음식을 좋아한다." for i in range(5)]
    )

    assert len(parse_facts(reply, 3)) == 3
    assert [f.text for f in parse_facts(reply, 10)].count("사용자는 개발자다.") == 1


def test_parse_returns_empty_for_empty_or_unparseable_reply():
    assert parse_facts("", 10) == []
    assert parse_facts("뽑을 만한 사실이 없습니다.", 10) == []


def test_normalize_absorbs_spacing_and_final_punctuation_only():
    assert normalize_fact_text("사용자는  개발자다.") == normalize_fact_text("사용자는 개발자다")
    assert normalize_fact_text("사용자는 개발자다") != normalize_fact_text("사용자는 디자이너다")


def test_kinds_are_the_four_the_prompt_names():
    """분류를 바꾸면 프롬프트 규칙 ②도 함께 바꿔야 한다 — 어긋나면 추출이 조용히 버려진다."""
    from navi.memory.facts import FACT_SYSTEM

    assert all(kind in FACT_SYSTEM for kind in FACT_KINDS)


# ─── 프롬프트 ─────────────────────────────────────────────


def test_prompt_numbers_turns_and_known_facts_in_separate_spaces(tmp_path):
    """턴 번호와 사실 번호가 한 프롬프트에 같이 있다 — 접두(T·F)로 갈라야 안 헷갈린다."""
    store, uid = _store(tmp_path)
    fact_id = store.insert_fact(uid, "profile", "사용자는 개발자다.", source_turn_id=None,
                                expires_at=None)
    (turn_id,) = _talk(store, uid, "나 이직했어")

    prompt = build_fact_prompt(
        store.turns_after(uid, 0, 10), store.active_facts(uid, _NOW.isoformat()), _NOW
    )

    assert f"F{fact_id} (profile) 사용자는 개발자다." in prompt
    assert f"T{turn_id} (" in prompt and "사용자: 나 이직했어" in prompt
    assert "오늘: " in prompt


def test_prompt_includes_navi_lines_as_context(tmp_path):
    """'민준'만으로는 뜻이 안 통한다 — 질문이 같이 있어야 사용자의 답이 사실이 된다."""
    store, uid = _store(tmp_path)
    _talk(store, uid, "동생 이름이 뭐야?", role="assistant")
    _talk(store, uid, "민준")

    prompt = build_fact_prompt(store.turns_after(uid, 0, 10), [], _NOW)

    assert "나비: 동생 이름이 뭐야?" in prompt
    assert "(없음)" in prompt  # 아는 사실이 없을 때의 표기


# ─── 추출기 ───────────────────────────────────────────────


async def test_extract_stores_facts_with_their_source_turn(tmp_path):
    store, uid = _store(tmp_path)
    first, second = _talk(store, uid, "내 동생 이름은 민준이야", "고수는 못 먹어")
    brain = ScriptedBrain(
        f"[fact:profile|src:T{first}] 사용자의 동생 이름은 민준이다.\n"
        f"[fact:preference|src:T{second}] 사용자는 고수를 못 먹는다."
    )

    assert await _extractor(store, uid, brain).maybe_extract(_NOW) == 2

    facts = store.active_facts(uid, _NOW.isoformat())
    assert [(f.kind, f.source_turn_id) for f in facts] == [
        ("profile", first), ("preference", second)
    ]
    assert facts[0].expires_at is None  # 신상은 만료가 없다


async def test_extract_waits_until_enough_user_turns(tmp_path):
    """턴 하나마다 LLM을 부르지 않는다."""
    store, uid = _store(tmp_path)
    _talk(store, uid, "안녕")
    brain = ScriptedBrain("[fact:profile] 사용자는 개발자다.")

    assert await _extractor(store, uid, brain, min_new_user_turns=2).maybe_extract(_NOW) is None
    assert brain.requests == []


async def test_extract_does_not_resend_turns_it_already_saw(tmp_path):
    store, uid = _store(tmp_path)
    _talk(store, uid, "옛말 하나", "옛말 둘")
    brain = ScriptedBrain("")
    extractor = _extractor(store, uid, brain)
    await extractor.maybe_extract(_NOW)

    _talk(store, uid, "새말 하나", "새말 둘")
    await extractor.maybe_extract(_NOW)

    second_prompt = brain.requests[1].messages[-1].text
    assert "새말 하나" in second_prompt and "옛말 하나" not in second_prompt


async def test_empty_answer_still_advances_the_cursor(tmp_path):
    """'기억할 게 없다'도 답이다 — 안 옮기면 같은 대화를 매번 다시 보낸다."""
    store, uid = _store(tmp_path)
    _talk(store, uid, "ㅋㅋ", "그러게")
    brain = ScriptedBrain("")
    extractor = _extractor(store, uid, brain)

    assert await extractor.maybe_extract(_NOW) == 0
    assert await extractor.maybe_extract(_NOW) is None
    assert len(brain.requests) == 1


async def test_failure_keeps_the_cursor_and_backs_off(tmp_path):
    """실패한 대화는 아직 추출기에 보이지 않은 것이다 — 백오프 뒤 같은 대화를 다시 본다."""
    store, uid = _store(tmp_path)
    _talk(store, uid, "내 동생 이름은 민준이야", "고수는 못 먹어")
    extractor = _extractor(store, uid, ScriptedBrain(boom=True), retry_backoff_s=600)

    assert await extractor.maybe_extract(_NOW) == 0
    assert store.fact_cursor(uid) == 0
    assert await extractor.maybe_extract(_NOW + timedelta(seconds=599)) is None  # 백오프 중

    extractor._extractor = ScriptedBrain("[fact:profile] 사용자의 동생 이름은 민준이다.")
    assert await extractor.maybe_extract(_NOW + timedelta(seconds=601)) == 1


async def test_replaces_supersedes_the_old_fact_without_deleting_it(tmp_path):
    store, uid = _store(tmp_path)
    old = store.insert_fact(uid, "plan", "사용자는 부산으로 이사할 계획이다.",
                            source_turn_id=None, expires_at=None)
    _talk(store, uid, "이사 취소됐어", "그냥 여기 살기로 했어")
    brain = ScriptedBrain(f"[fact:ongoing|replaces:F{old}] 사용자의 부산 이사 계획은 취소됐다.")

    await _extractor(store, uid, brain).maybe_extract(_NOW)

    assert [f.text for f in store.active_facts(uid, _NOW.isoformat())] == [
        "사용자의 부산 이사 계획은 취소됐다."
    ]
    row = store._conn.execute(
        "SELECT status, superseded_by FROM fact WHERE fact_id = ?", (old,)
    ).fetchone()
    assert row["status"] == "superseded" and row["superseded_by"] is not None


async def test_replaces_with_a_made_up_number_changes_nothing(tmp_path):
    """모델이 준 번호를 그대로 믿지 않는다 — 없는 번호면 새 사실만 들어간다."""
    store, uid = _store(tmp_path)
    keep = store.insert_fact(uid, "profile", "사용자는 개발자다.", source_turn_id=None,
                             expires_at=None)
    _talk(store, uid, "요즘 러닝 시작했어", "매일 아침 뛰어")
    brain = ScriptedBrain("[fact:ongoing|replaces:F9999] 사용자는 아침 러닝을 시작했다.")

    await _extractor(store, uid, brain).maybe_extract(_NOW)

    assert keep in [f.fact_id for f in store.active_facts(uid, _NOW.isoformat())]
    assert len(store.active_facts(uid, _NOW.isoformat())) == 2


async def test_source_outside_the_batch_falls_back_to_last_user_turn(tmp_path):
    store, uid = _store(tmp_path)
    _, last = _talk(store, uid, "나 개발자야", "백엔드 해")
    brain = ScriptedBrain("[fact:profile|src:T9999] 사용자는 백엔드 개발자다.")

    await _extractor(store, uid, brain).maybe_extract(_NOW)

    assert store.active_facts(uid, _NOW.isoformat())[0].source_turn_id == last


async def test_known_fact_is_not_stored_twice(tmp_path):
    store, uid = _store(tmp_path)
    store.insert_fact(uid, "profile", "사용자는 개발자다.", source_turn_id=None, expires_at=None)
    _talk(store, uid, "나 개발자잖아", "알지?")
    brain = ScriptedBrain("[fact:profile] 사용자는 개발자다")

    assert await _extractor(store, uid, brain).maybe_extract(_NOW) == 0


async def test_plan_expires_at_the_end_of_its_day(tmp_path):
    store, uid = _store(tmp_path)
    _talk(store, uid, "20일에 면접 봐", "떨린다")
    brain = ScriptedBrain("[fact:plan|until:2026-11-20] 사용자는 11월 20일에 면접을 본다.")
    await _extractor(store, uid, brain).maybe_extract(_NOW)

    def local(day, hour):  # tz 없는 시각은 로컬 — 끝 날짜도 로컬 기준으로 센다
        return datetime(2026, 11, day, hour).astimezone(UTC).isoformat()

    assert len(store.active_facts(uid, local(20, 23))) == 1  # 그 날은 아직 유효
    assert store.active_facts(uid, local(21, 1)) == []  # 지나면 주입에서 빠진다


async def test_plan_already_past_is_not_stored(tmp_path):
    store, uid = _store(tmp_path)
    _talk(store, uid, "어제 면접 봤어", "망했어")
    brain = ScriptedBrain("[fact:plan|until:2026-11-01] 사용자는 11월 1일에 면접을 본다.")

    assert await _extractor(store, uid, brain).maybe_extract(_NOW) == 0


async def test_undated_plan_and_ongoing_get_a_default_lifetime(tmp_path):
    """낡는 종류는 끝 날짜가 없어도 영원히 남지 않는다."""
    store, uid = _store(tmp_path)
    _talk(store, uid, "언젠가 일본 가려고", "요즘 이직 준비해")
    brain = ScriptedBrain(
        "[fact:plan] 사용자는 일본 여행을 계획하고 있다.\n"
        "[fact:ongoing] 사용자는 이직을 준비 중이다."
    )
    await _extractor(store, uid, brain).maybe_extract(_NOW)

    plan, ongoing = store.active_facts(uid, _NOW.isoformat())
    assert plan.expires_at == _NOW + timedelta(days=30)
    assert ongoing.expires_at == _NOW + timedelta(days=90)


async def test_full_batch_without_user_speech_is_skipped_over(tmp_path):
    """응답 없는 선제 발화가 줄지으면 같은 묶음만 다시 읽어 그 뒤에 못 닿는다 — 넘긴다."""
    store, uid = _store(tmp_path)
    _talk(store, uid, *[f"혼잣말 {i}" for i in range(4)], role="assistant")
    _talk(store, uid, "이제 왔어", "나 개발자야")
    brain = ScriptedBrain("[fact:profile] 사용자는 개발자다.")
    extractor = _extractor(store, uid, brain, max_rows_per_batch=4)

    assert await extractor.maybe_extract(_NOW) is None  # 꽉 찬 묶음에 사용자 말 없음 → 넘김
    assert await extractor.maybe_extract(_NOW) == 1
    assert "혼잣말" not in brain.requests[0].messages[-1].text


async def test_backlog_is_eaten_one_batch_at_a_time(tmp_path):
    store, uid = _store(tmp_path)
    _talk(store, uid, *[f"말 {i}" for i in range(6)])
    brain = ScriptedBrain("")
    extractor = _extractor(store, uid, brain, max_rows_per_batch=3)

    await extractor.maybe_extract(_NOW)
    await extractor.maybe_extract(_NOW)

    assert ["말 0" in r.messages[-1].text for r in brain.requests] == [True, False]
    assert "말 3" in brain.requests[1].messages[-1].text


# ─── 저장소 ───────────────────────────────────────────────


def test_active_facts_limit_keeps_the_newest_in_order(tmp_path):
    store, uid = _store(tmp_path)
    ids = [
        store.insert_fact(uid, "preference", f"사실 {i}", source_turn_id=None, expires_at=None)
        for i in range(5)
    ]

    assert [f.fact_id for f in store.active_facts(uid, _NOW.isoformat(), limit=2)] == ids[-2:]


def test_facts_survive_restart(tmp_path):
    store, uid = _store(tmp_path)
    store.insert_fact(uid, "profile", "사용자는 개발자다.", source_turn_id=None, expires_at=None)
    store.set_fact_cursor(uid, 7)
    store.close()

    reopened = MemoryStore(tmp_path / "t.db")
    assert [f.text for f in reopened.active_facts(uid, _NOW.isoformat())] == ["사용자는 개발자다."]
    assert reopened.fact_cursor(uid) == 7


# ─── 데몬 조립 ────────────────────────────────────────────


def _facts_config(vendor: str, *, enabled: bool = True):
    from types import SimpleNamespace

    from navi.config import FactsConfig

    return SimpleNamespace(
        facts=FactsConfig(
            enabled=enabled,
            extractor_vendor=vendor,
            min_new_user_turns=4,
            max_rows_per_batch=40,
            retry_backoff_s=1800,
        )
    )


def test_echo_extractor_is_never_built(tmp_path):
    """echo는 뽑는 게 없는데 커서만 전진한다 — 기본 config로 띄운 데몬이 대화를 조용히
    '소비'해 버리면 나중에 벤더를 켜도 그 대화는 되돌릴 수 없다."""
    from navi.daemon import _build_facts

    store, uid = _store(tmp_path)

    assert _build_facts(_facts_config("echo"), store, uid) is None
    assert _build_facts(_facts_config("anthropic", enabled=False), store, uid) is None
