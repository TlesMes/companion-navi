"""워커 API 클라이언트 (design/protocol.md §7) — 가져가기·완료·실패.

표준 라이브러리 HTTP만 쓴다. 호출은 블로킹이라 비동기 루프에서는 스레드로 넘긴다.
"""

from __future__ import annotations

import asyncio
import json
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any

_TIMEOUT_S = 30


class StaleJob(RuntimeError):
    """그 시도는 이미 무효다 — 임대가 끝나 다른 워커가 가져갔다(409). 결과를 버린다."""


@dataclass(frozen=True)
class Job:
    job_id: int
    kind: str
    attempt: int  # 펜싱 토큰 — 완료·실패 보고에 그대로 돌려준다
    payload: dict[str, Any]


class WorkerClient:
    def __init__(self, url: str, token: str):
        if not token:
            raise ValueError("워커 토큰이 없습니다 — 환경 변수 NAVI_WORKER_TOKEN")
        self._base = url.rstrip("/")
        self._token = token

    async def claim(self, kinds: list[str]) -> Job | None:
        body = await asyncio.to_thread(self._post, "/v1/worker/jobs/claim", {"kinds": kinds})
        if body is None:
            return None
        return Job(body["job_id"], body["kind"], body["attempt"], body["payload"])

    async def complete(self, job: Job, result: dict[str, Any]) -> None:
        await asyncio.to_thread(
            self._post, f"/v1/worker/jobs/{job.job_id}/complete",
            {"attempt": job.attempt, **result},
        )

    async def fail(self, job: Job, error: str) -> None:
        await asyncio.to_thread(
            self._post, f"/v1/worker/jobs/{job.job_id}/fail",
            {"attempt": job.attempt, "error": error[:500]},
        )

    def _post(self, path: str, body: dict[str, Any]) -> dict[str, Any] | None:
        request = urllib.request.Request(
            self._base + path,
            method="POST",
            data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self._token}",
            },
        )
        try:
            with urllib.request.urlopen(request, timeout=_TIMEOUT_S) as response:
                raw = response.read()
        except urllib.error.HTTPError as exc:
            if exc.code == 409:
                raise StaleJob(path) from exc
            raise
        return json.loads(raw) if raw else None  # 204 = 가져갈 작업 없음
