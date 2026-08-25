"""오케스트레이터 (01 문서 4.6절) — 페르소나+기억+트리거를 한 요청으로 조립.

조립 순서는 캐싱 친화적으로 고정한다(마스터 플랜 — 입력비 0 수렴):
[고정: 캐릭터 카드 시스템 프롬프트] → [매번 변함: 최근 턴 + 이번 트리거].
relevant_facts(장기기억)는 Phase 4에서 이 사이에 끼어든다.
"""

from __future__ import annotations

import logging

from navi.config import Config
from navi.memory import MemoryStore
from navi.models import LlmRequest, Message, TurnKind
from navi.persona import CharacterCard

log = logging.getLogger(__name__)

# 선제 발화 프레이밍 — 서술형 소재가 "사용자가 한 말"로 오해되지 않게, 트리거를 감싼다
# (turn_assembly.md §2). 태그가 아니라 자기설명형 자연어라 시스템 프롬프트가 규약을 몰라도
# 성립한다. 문구는 튜닝 대상(값은 배선용, 실 재료 후 A/B).
#
# "출처를 지어내지 마"가 들어간 건 실측 때문이다(turn_assembly.md §4.1 ①) — 프레이밍이
# 출처를 안 주니 모델이 빈칸을 "어제 뉴스에서 봤는데"로 메웠고, 나비는 카드상 집 밖에
# 못 나가는 정령이라 세계관 위반이다. 카드 background가 "바깥 이야기가 흘러들어오되
# 어디서 왔는지는 모른다"로 **능력**을 세우고, 이 줄이 **이번 건**을 통제한다. 층이 달라
# 서로를 대체하지 못한다(2026.08.25 결정 ⓒ).
_PROACTIVE_FRAME = (
    "(사용자는 지금 아무 말도 안 했어. 아래 소재로 네가 먼저 말을 꺼내줘 — "
    "사용자가 알려준 게 아니라 집 안에 흘러든 이야기로 알게 된 거야. "
    "어디서 봤다거나 들었다고 출처를 지어내지 마. 짧게, 되묻지 말고: {trigger})"
)

# 대화 콜백은 소재의 출처가 **사용자 자신**이라 위 프레임을 그대로 쓸 수 없다.
# 두 군데가 정반대다: ①"사용자가 알려준 게 아니야"는 여기선 거짓이고 ②뉴스는 되묻지
# 말아야 하지만 콜백은 되묻는 것이 목적이다.
_CALLBACK_FRAME = (
    "(사용자는 지금 아무 말도 안 했어. 아래는 사용자가 전에 한 말에서 나온 거야 — "
    "그 뒤가 어떻게 됐는지 궁금해서 네가 먼저 물어보는 거고, "
    "새로 알게 된 소식이 아니야. 짧게 한두 문장으로: {trigger})"
)

# turn_assembly.md §3.3의 "enum + 전략맵". 조각이 더 늘면 Builder로 승격한다.
_FRAMES = {
    TurnKind.PROACTIVE: _PROACTIVE_FRAME,
    TurnKind.PROACTIVE_CALLBACK: _CALLBACK_FRAME,
}


class Conductor:
    def __init__(self, card: CharacterCard, memory: MemoryStore, config: Config):
        self._card = card
        self._memory = memory
        self._config = config

    def set_card(self, card: CharacterCard) -> None:
        """페르소나 카드 교체 — 다음 build_request부터 적용.

        system_prompt는 매 턴 재조립(캐시 없음)이라 재할당만으로 충분하다.
        """
        log.info("카드 교체: %s → %s", self._card.character, card.character)
        self._card = card

    @property
    def card(self) -> CharacterCard:
        return self._card

    def build_request(
        self,
        trigger_text: str,
        user_id: int,
        session_id: str,
        *,
        kind: TurnKind = TurnKind.REACTIVE,
    ) -> LlmRequest:
        # session_id는 Phase 1에선 미사용 — 단기기억을 세션 경계 없이 인출해야
        # "껐다 켜도 어제 대화를 기억"이 성립한다. 계약(4.6) 유지 차원에서 받아둔다.
        intimacy = self._memory.get_intimacy(user_id)
        turns = self._memory.recall_recent_for_user(user_id, self._config.recent_turns)
        messages = [Message(role=t.role, text=t.text) for t in turns]
        messages.extend(self._frame(kind, trigger_text))
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

    @staticmethod
    def _frame(kind: TurnKind, trigger_text: str) -> list[Message]:
        """트리거를 kind에 맞는 tail 메시지로 조립한다 (turn_assembly.md §3.1).

        REACTIVE는 진짜 사용자 발화라 그대로, 선제 kind는 나비에게 주어진 소재라
        프레이밍으로 감싼다. 시스템 프롬프트(카드 코어)는 **모든 kind에서 무변경** —
        차이는 여기 tail에만 둬 캐시 prefix를 안 쪼갠다.
        """
        frame = _FRAMES.get(kind)
        if frame is None:  # REACTIVE — 진짜 사용자 발화라 감싸지 않는다
            return [Message(role="user", text=trigger_text)]
        return [Message(role="user", text=frame.format(trigger=trigger_text))]
