"""기억 포트 — 요청 조립이 기억에 요구하는 계약 (design/memory.md §3).

`MemoryStore`는 턴·모드·피드 후보·설정·사용량을 한 클래스에 담고 있다. 그중 **대화 기억**만
이 포트로 떼어 낸다. 이유는 둘이다.

- 조립(Conductor)이 SQLite 구현 전체가 아니라 필요한 것에만 기대게 한다 — 기억 저장소가
  서버로 옮겨 가도(design/service.md §5) 갈아 끼울 경계가 이 포트 하나다.
- 모드·피드·설정·사용량은 넣지 않는다. 그쪽은 기기에 남거나 다른 경계가 된다.

사실(fact)은 **읽는 쪽만** 여기 있다. 쓰는 쪽(추출·대체)은 배치 작업이 `MemoryStore`를
직접 쓴다 — 서버에서는 워커가 다른 저장소에 쓰게 되므로 그 계약을 미리 고정하지 않는다.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import datetime

from navi.models import Fact, Turn


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

    @abstractmethod
    def recall_relevant_facts(self, user_id: int, query: str, k: int) -> list[Fact]:
        """이번 턴에 넣을 사실 최대 k개 — 지금 유효한 것만(대체·만료 제외).

        `query`는 이번 턴의 트리거 문장이다. **지금 구현은 쓰지 않는다** — 사실이 적어
        고를 필요가 없기 때문이다. 사실이 k를 한참 넘어 "관련 있는 것만" 골라야 할 때
        (벡터 검색) 이 인자를 쓰는 구현으로 갈아 끼운다. 그때 호출부를 안 고치려고
        지금부터 받아 둔다(design/memory.md §1).

        돌려주는 순서는 호출마다 같아야 한다(같은 사실 집합이면 같은 순서) — 조립이
        이걸 그대로 프롬프트 앞쪽에 놓으므로, 순서가 흔들리면 프롬프트 캐시가 깨진다.
        """
