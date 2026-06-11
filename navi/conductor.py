"""오케스트레이터 (01 문서 4.6절) — 페르소나+기억+트리거를 한 요청으로 조립.

조립 순서는 캐싱 친화적으로 고정한다(마스터 플랜 — 입력비 0 수렴):
[고정: 캐릭터 카드 시스템 프롬프트] → [매번 변함: 최근 턴 + 이번 트리거].
relevant_facts(장기기억)는 Phase 4에서 이 사이에 끼어든다.
"""

from __future__ import annotations

import logging

from navi.config import Config
from navi.memory import MemoryStore
from navi.models import LlmRequest, Message
from navi.persona import CharacterCard

log = logging.getLogger(__name__)


class Conductor:
    def __init__(self, card: CharacterCard, memory: MemoryStore, config: Config):
        self._card = card
        self._memory = memory
        self._config = config

    def build_request(
        self, trigger_text: str, user_id: int, session_id: str
    ) -> LlmRequest:
        # session_id는 Phase 1에선 미사용 — 단기기억을 세션 경계 없이 인출해야
        # "껐다 켜도 어제 대화를 기억"이 성립한다. 계약(4.6) 유지 차원에서 받아둔다.
        intimacy = self._memory.get_intimacy(user_id)
        turns = self._memory.recall_recent_for_user(user_id, self._config.recent_turns)
        messages = [Message(role=t.role, text=t.text) for t in turns]
        messages.append(Message(role="user", text=trigger_text))
        request = LlmRequest(
            system=self._card.system_prompt(intimacy),
            messages=messages,
            model=self._config.brain.model,
        )
        log.info(
            "요청 조립 — system %d자, 메시지 %d개(기억 %d턴 + 트리거), 친밀도 %.0f, model=%s",
            len(request.system), len(messages), len(turns), intimacy, request.model,
        )
        log.debug("system 전문:\n%s", request.system)
        log.debug("messages: %s", messages)
        return request
