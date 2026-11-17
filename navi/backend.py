"""턴 뒷단 — 단독 모드와 연결 모드가 갈리는 한 자리 (design/service.md §2.3).

데몬과 텍스트 CLI는 같은 파이프라인을 돈다. 두 모드의 차이는 둘뿐이다:

- **누가 답을 만드는가** — 단독은 로컬 조립 + 자기 키의 두뇌, 연결은 서버.
- **턴이 끝난 뒤 무엇을 하는가** — 단독은 로컬 DB에 대화를 적고, 연결은 서버에 재생 결과를
  알린다(서버가 대화의 원본이다).

그 둘을 여기 모아 호출부가 모드를 묻지 않게 한다.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from navi.brain import create_brain
from navi.brain.base import BrainAdapter
from navi.conductor import Conductor
from navi.config import Config
from navi.memory import MemoryStore
from navi.models import BrainResult
from navi.mouth.base import MouthAdapter
from navi.persona import CharacterCard

log = logging.getLogger(__name__)


@dataclass
class TurnBackend:
    brain: BrainAdapter
    conductor: Any  # Conductor | RemoteConductor — 같은 메서드를 가진다
    link: Any = None  # 연결 모드의 ServerLink. 단독이면 None

    @property
    def connected(self) -> bool:
        return self.link is not None

    async def record(
        self,
        store: MemoryStore,
        session_id: str,
        user_id: int,
        *,
        user_text: str | None,
        result: BrainResult,
        mouth: MouthAdapter | None,
        trigger_type: str = "manual",
    ) -> None:
        """끝난 턴을 남긴다. user_text가 None이면 선제 발화다(사용자 발화가 없다)."""
        if self.connected:
            # 대화는 서버가 이미 갖고 있다. 여기서 할 일은 어디까지 들렸는지 알리는 것뿐이다.
            # 로컬 DB에 따로 적지 않는다 — 두 벌이 되면 어느 쪽이 맞는지 가릴 수 없다.
            await self.brain.report_played(mouth.played_chars if mouth is not None else None)
            return
        if user_text is not None:
            store.append_turn(session_id, user_id, role="user", text=user_text)
        store.append_turn(
            session_id, user_id, role="assistant", text=result.full_text,
            trigger_type=trigger_type,
        )
        store.log_usage("llm", result.usage)

    async def close(self) -> None:
        if self.link is not None:
            await self.link.close()


def build_backend(config: Config, card: CharacterCard, store: MemoryStore) -> TurnBackend:
    """config.server.url이 있으면 연결 모드, 없으면 단독 모드."""
    if not config.server.enabled:
        return TurnBackend(
            brain=create_brain(config),
            conductor=Conductor(card=card, memory=store, config=config),
        )
    # 연결 모드에서만 불러온다 — 단독 모드는 websockets 없이도 떠야 한다.
    from navi.remote import RemoteBrain, RemoteConductor, ServerLink
    from navi.remote.brain import persona_id_of

    link = ServerLink(config.server.url, config.server.token_path)
    log.info("연결 모드 — 서버 %s (조립·두뇌·대화 저장은 서버가 한다)", config.server.url)
    return TurnBackend(
        brain=RemoteBrain(link),
        conductor=RemoteConductor(card, persona_id_of(config.persona_card_path)),
        link=link,
    )
