"""워커 실행 — `python -m navi.worker [--server URL] [--once]`.

서버 작업 큐에서 사실 추출 작업을 가져가 처리한다. 작업이 없으면 잠깐 쉬었다가 다시 묻는다.
여럿을 띄워도 된다 — 서버가 SKIP LOCKED로 작업을 나눠 준다(D25).

설정:
- 서버 주소: --server, 없으면 config의 server.url
- 워커 토큰: 환경 변수 NAVI_WORKER_TOKEN(.env 가능) — 서버와 같은 값
- 추출기: config의 memory.facts.extractor.vendor(없으면 feed.summarizer.vendor). echo면 시작하지
  않는다 — echo는 아무것도 못 뽑는데 커서만 옮겨 그 대화를 영영 추출하지 못하게 만든다.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import sys

from navi.worker.client import StaleJob, WorkerClient
from navi.worker.facts import run_fact_job

log = logging.getLogger("navi.worker")

FACT_JOB = "extract_facts"


async def work(client: WorkerClient, extractor, model: str, *, once: bool, idle_s: float) -> int:
    """작업을 처리한다. once면 가져갈 게 없을 때 멈춘다. 처리한 작업 수를 돌려준다."""
    done = 0
    while True:
        try:
            job = await client.claim([FACT_JOB])
        except OSError as exc:  # 서버가 안 떠 있다 — 상주 워커는 기다린다
            if once:
                raise
            log.warning("서버에 닿지 못했다 — %.0f초 뒤 다시: %s", idle_s, exc)
            await asyncio.sleep(idle_s)
            continue
        if job is None:
            if once:
                return done
            await asyncio.sleep(idle_s)
            continue
        try:
            result = await run_fact_job(job.payload, extractor=extractor, model=model)
        except Exception as exc:  # LLM 실패 — 서버가 백오프 뒤 다시 내준다
            log.warning("작업 %d 실패: %s", job.job_id, exc, exc_info=True)
            try:
                await client.fail(job, f"{type(exc).__name__}: {exc}")
            except StaleJob:
                pass
            continue
        try:
            await client.complete(job, result)
            done += 1
        except StaleJob:
            log.warning("작업 %d — 임대가 끝나 결과를 버린다(다른 워커가 가져갔다)", job.job_id)


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(prog="navi-worker", description="서버 작업 큐 워커")
    parser.add_argument("--server", metavar="URL", help="서버 주소(없으면 config의 server.url)")
    parser.add_argument("--once", action="store_true", help="가져갈 작업이 없어지면 끝낸다")
    parser.add_argument("--vendor", choices=["anthropic", "gemini"],
                        help="추출기 벤더를 이번 실행만 바꾼다(config의 memory.facts.extractor.vendor)")
    parser.add_argument("--idle", type=float, default=5.0, metavar="SEC", help="빈 큐 대기 간격")
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args()
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    from navi.brain import create_brain
    from navi.config import load_config

    config = load_config()  # .env도 읽는다 — 워커 토큰·추출기 키
    url = args.server or config.server.url
    if not url:
        raise SystemExit("서버 주소가 없습니다 — --server 또는 config의 server.url")
    vendor = args.vendor or config.facts.extractor_vendor
    if vendor == "echo":
        raise SystemExit(
            "추출기 벤더가 echo입니다 — config.local.yaml에 memory.facts.extractor.vendor를 적으세요"
        )
    client = WorkerClient(url, os.getenv("NAVI_WORKER_TOKEN", ""))
    extractor = create_brain(config, vendor=vendor)
    model = config.brain.model_for(vendor)
    log.info("워커 시작 — 서버 %s, 추출기 %s(%s)", url, vendor, model)
    try:
        done = asyncio.run(work(client, extractor, model, once=args.once, idle_s=args.idle))
        log.info("워커 종료 — 작업 %d건 처리", done)
    except KeyboardInterrupt:
        log.info("워커 종료")


if __name__ == "__main__":
    main()
