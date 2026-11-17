"""서버의 배치 워커 — 서버(server/) 작업 큐에서 작업을 가져가 처리한다 (service.md §11 R1).

Java 서버는 온라인 경로만 맡고, LLM을 부르는 배치(사실 추출)는 여기서 한다. 단독 모드와 같은
코드(navi/memory/facts.py)를 써서 배치 로직이 한 벌이다.

    python -m navi.worker --server http://localhost:8080

워커는 DB에 직접 붙지 않는다. 서버의 워커 API로 작업을 pull(claim)하고 결과를 돌려준다.
"""

from navi.worker.client import Job, WorkerClient
from navi.worker.facts import run_fact_job

__all__ = ["Job", "WorkerClient", "run_fact_job"]
