"""기억 포트 — 요청 조립이 기억에 요구하는 계약 (design/memory.md §3).

`MemoryStore`는 턴·모드·피드 후보·설정·사용량을 한 클래스에 담고 있다. 그중 **대화 기억**만
이 포트로 떼어 낸다. 이유는 둘이다.

- 조립(Conductor)이 SQLite 구현 전체가 아니라 필요한 것에만 기대게 한다 — 기억 저장소가
  서버로 옮겨 가도(design/service.md §5) 갈아 끼울 경계가 이 포트 하나다.
- 모드·피드·설정·사용량은 넣지 않는다. 그쪽은 기기에 남거나 다른 경계가 된다.

지금은 턴만 있다. 사실(fact) 저장·인출은 사실 추출을 구현할 때 여기에 더한다.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import datetime

from navi.models import Turn


class MemoryPort(ABC):
    @abstractmethod
    def append_turn(
        self,
        session_id: str,
        user_id: int,
        role: str,
        text: str,
        trigger_type: str = "manual",
        *,
        ts: datetime | None = None,
    ) -> int:
        """턴 하나를 적재하고 그 `turn_id`를 돌려준다.

        `turn_id`는 사실이 출처 턴을 가리키는 데 쓴다. `ts`는 발화 시각 — 생략하면 지금이다.
        지난 대화를 옮겨 넣거나 측정용 합성 대화에 과거 시각을 줄 때만 넘긴다.
        """

    @abstractmethod
    def recall_recent(self, session_id: str, n: int) -> list[Turn]:
        """해당 세션의 최근 n행, 시간순."""

    @abstractmethod
    def recall_recent_for_user(self, user_id: int, n: int) -> list[Turn]:
        """세션 경계 없이 사용자의 최근 n행, 시간순.

        n은 왕복 수가 아니라 **행 수**다 — 사용자·나비 발화가 각각 한 행이라 20이면 약 10왕복.
        """
