"""서버 워커(navi/worker) — 단독 모드와 같은 추출 규칙을 서버 작업에 거는가."""

from __future__ import annotations

from collections.abc import AsyncIterator

from navi.brain.base import BrainAdapter
from navi.models import BrainResult, Usage
from navi.worker.__main__ import work
from navi.worker.client import Job, StaleJob
from navi.worker.facts import run_fact_job


class ScriptedExtractor(BrainAdapter):
    def __init__(self, reply: str = "", error: Exception | None = None):
        super().__init__()
        self.reply = reply
        self.error = error
        self.prompts: list[str] = []

    async def generate_stream(self, request) -> AsyncIterator[str]:
        self.prompts.append(request.messages[-1].text)
        if self.error:
            raise self.error
        yield self.reply
        self.last_result = BrainResult(self.reply, Usage(1200, 30))


def payload(**overrides):
    base = {
        "user_id": 7,
        "now": "2026-11-18T14:30:00Z",  # 서울 23:30, 수요일
        "utc_offset_minutes": 540,
        "max_facts": 10,
        "turns": [
            {"id": 101, "role": "user", "text": "나 동생 있어. 이름은 민준이야.",
             "created_at": "2026-11-18T14:00:00Z"},
            {"id": 102, "role": "assistant", "text": "민준이구나.", "created_at": "2026-11-18T14:00:05Z"},
            {"id": 103, "role": "user", "text": "다음 주 금요일에 면접 봐.",
             "created_at": "2026-11-18T14:01:00Z"},
            {"id": 104, "role": "assistant", "text": "응원할게.", "created_at": "2026-11-18T14:01:05Z"},
        ],
        "known_facts": [{"id": 9, "kind": "ongoing", "text": "사용자는 회사에 다닌다."}],
    }
    return base | overrides


async def test_fact_job_returns_checked_facts_for_the_server():
    extractor = ScriptedExtractor(
        "[fact:profile|src:T101] 사용자의 동생 이름은 민준이다.\n"
        "[fact:plan|src:T103|until:2026-11-27|replaces:F9] 사용자는 11월 27일에 면접을 본다.\n"
        "[fact:preference|src:T999|replaces:F42] 사용자는 커피를 좋아한다.\n"
        "[fact:ongoing] 사용자는 회사에 다닌다."  # 이미 아는 사실 — 다시 보내지 않는다
    )

    result = await run_fact_job(payload(), extractor=extractor, model="m")

    assert result["cursor"] == 104
    assert result["usage"] == {"input_tokens": 1200, "output_tokens": 30}
    facts = result["facts"]
    assert [f["text"] for f in facts] == [
        "사용자의 동생 이름은 민준이다.", "사용자는 11월 27일에 면접을 본다.", "사용자는 커피를 좋아한다.",
    ]
    assert facts[0]["source_id"] == 101 and facts[0]["expires_at"] is None
    # 끝 날짜는 **사용자 시간대**의 그날 자정까지 — 서울 11/28 00:00 = UTC 11/27 15:00.
    assert facts[1]["expires_at"] == "2026-11-27T15:00:00+00:00"
    assert facts[1]["replaces"] == 9
    # 모델이 지어낸 번호: 출처는 마지막 사용자 말로, 대체는 버린다.
    assert facts[2]["source_id"] == 103 and facts[2]["replaces"] is None


async def test_prompt_uses_the_users_calendar_not_this_machines():
    """UTC로는 18일 14:00이지만 서울로는 18일 23:00 — 말한 날은 사용자 시간대로 센다."""
    late = payload(
        now="2026-11-18T15:30:00Z",  # 서울 19일 00:30
        turns=[{"id": 1, "role": "user", "text": "내일 이사해.", "created_at": "2026-11-18T15:10:00Z"}],
    )
    extractor = ScriptedExtractor("")

    await run_fact_job(late, extractor=extractor, model="m")

    prompt = extractor.prompts[0]
    assert "오늘: 2026-11-19 목" in prompt
    assert "T1 (2026-11-19 목) 사용자: 내일 이사해." in prompt


async def test_batch_without_user_turns_moves_the_cursor_without_calling_the_model():
    extractor = ScriptedExtractor("[fact:profile] 뽑히면 안 된다.")
    only_nabi = payload(turns=[
        {"id": 5, "role": "assistant", "text": "안녕?", "created_at": "2026-11-18T14:00:00Z"},
    ])

    result = await run_fact_job(only_nabi, extractor=extractor, model="m")

    assert extractor.prompts == []
    assert result == {"facts": [], "cursor": 5, "usage": {"input_tokens": 0, "output_tokens": 0}}


class FakeClient:
    def __init__(self, jobs, *, stale_on_complete=False):
        self.jobs = list(jobs)
        self.completed: list[tuple[int, dict]] = []
        self.failed: list[tuple[int, str]] = []
        self.stale = stale_on_complete

    async def claim(self, kinds):
        assert kinds == ["extract_facts"]
        return self.jobs.pop(0) if self.jobs else None

    async def complete(self, job, result):
        if self.stale:
            raise StaleJob("late")
        self.completed.append((job.job_id, result))

    async def fail(self, job, error):
        self.failed.append((job.job_id, error))


async def test_worker_completes_jobs_until_the_queue_is_empty():
    client = FakeClient([Job(1, "extract_facts", 1, payload()), Job(2, "extract_facts", 1, payload())])
    extractor = ScriptedExtractor("[fact:profile|src:T101] 사용자의 동생 이름은 민준이다.")

    done = await work(client, extractor, "m", once=True, idle_s=0)

    assert done == 2
    assert [job_id for job_id, _ in client.completed] == [1, 2]


async def test_model_failure_is_reported_so_the_server_can_retry():
    client = FakeClient([Job(3, "extract_facts", 2, payload())])

    done = await work(client, ScriptedExtractor(error=RuntimeError("벤더 장애")), "m",
                      once=True, idle_s=0)

    assert done == 0
    assert client.failed == [(3, "RuntimeError: 벤더 장애")]


async def test_stale_completion_is_dropped_quietly():
    client = FakeClient([Job(4, "extract_facts", 1, payload())], stale_on_complete=True)

    done = await work(client, ScriptedExtractor(""), "m", once=True, idle_s=0)

    assert done == 0 and client.failed == []
