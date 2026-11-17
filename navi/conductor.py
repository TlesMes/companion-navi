"""오케스트레이터 (01 문서 4.6절) — 페르소나+기억+트리거를 한 요청으로 조립.

조립 순서는 캐싱 친화적으로 고정한다(마스터 플랜 — 입력비 0 수렴):
[고정: 캐릭터 카드 시스템 프롬프트] → [가끔 변함: 기억한 사실] → [매번 변함: 최근 턴 +
이번 트리거]. 사실은 추출이 돌 때만 바뀐다(design/memory.md).
"""

from __future__ import annotations

import logging
import re

from navi.config import Config
from navi.memory import MemoryPort
from navi.models import Fact, LlmRequest, Message, Turn, TurnKind
from navi.persona import CharacterCard

log = logging.getLogger(__name__)

# 재료는 한국어 고정인데(feed.md 3.3 — 수집이 배치라 어느 카드가 말할지 모른다) 그
# 한국어성이 카드의 언어 지시를 압도한다. 실측 2026.08.28: example_jp("日本語で短く
# 簡潔に答える")가 선제 3회 중 2회 한국어로 답했다. 카드 서사가 긴 aris는 버텼는데,
# **카드 강도에 따라 갈리는 게 문제**다 — 일본어 페르소나가 한국어를 뱉으면 TTS의
# ref_lang이 안 맞아 G2P가 깨진다.
#
# 처음엔 "말은 네가 늘 쓰는 언어로 해"라는 보편 문구였는데, 그건 **모델이 카드에서
# 언어를 추론**해야 하고 실패하던 게 정확히 그 추론이었다. 카드가 language를 선언하게
# 하고(E5) 그 값을 여기 박는다 — 추론이 사라진다. 카드 값이라 종속도 안 생긴다.
# 재료는 **항상 한국어**다(feed.md 3.3) — 그래서 카드도 한국어면 이 지시는 자기를
# 부정하는 문장이 된다("한국어로 적혀 있지만 답은 한국어로"). 같으면 아예 안 넣는다:
# 헛도는 대조 접속은 모델을 헷갈리게 하고 매 턴 토큰만 축낸다.
_MATERIAL_LANGUAGE = "ko"
_LANGUAGE_RULE = "소재는 한국어로 적혀 있지만 답은 {language}로 해."

# 프롬프트에 쓸 언어 이름 — 코드(ko)를 그대로 넣으면 모델이 덜 확실하게 받는다.
_LANGUAGE_NAMES = {"ko": "한국어", "ja": "일본어", "zh": "중국어", "en": "영어"}

# 선제 발화 프레이밍 — 서술형 소재가 "사용자가 한 말"로 오해되지 않게, 트리거를 감싼다
# (turn_assembly.md §2). 태그가 아니라 자기설명형 자연어라 시스템 프롬프트가 규약을 몰라도
# 성립한다. 문구는 튜닝 대상(값은 배선용, 실 재료 후 A/B).
#
# "출처를 지어내지 마"가 들어간 건 실측 때문이다(turn_assembly.md §4.1 ①) — 프레이밍이
# 출처를 안 주니 모델이 빈칸을 "어제 뉴스에서 봤는데"로 메웠다.
#
# ⚠ 이 문구는 **어느 카드에도 성립해야 한다.** 여기는 모든 페르소나가 공유하는 코드라,
# 특정 카드의 세계관("집 안에 흘러든 이야기로 알게 된 거야")을 넣으면 집과 무관한 카드
# (example_jp 등)가 남의 설정을 뒤집어쓴다. 한 번 그렇게 썼다가 되돌렸다(2026.08.25).
# 그래서 프레임은 **금지(보편)**만 말하고, "어떻게 알게 됐는가"라는 **경로는 카드 소유**다
# (나비는 background가 "바깥 이야기가 흘러들어오되 어디서 왔는진 모른다"로 준다).
# 경로를 안 주는 카드는 출처 없이 사실만 말하게 되는데, 그건 결함이 아니라 정상 폴백이다.
#
# "굳이 말하지 마"까지 넣은 건 사용자 판단이다(2026.08.25) — 자발적으로 출처를 밝히면
# 어색하고 발화만 길어진다. 카드 traits의 "짧고 가볍게, 한 번에 1~3문장"과도 맞다.
# 힌트를 뺀 직후 실측에선 이미 자발 언급이 없었지만, 그건 규칙이 아니라 그때 모델의
# 성향이라 명시해 둔다(재료 언어에서 같은 교훈을 얻었다 — feed.md §3.3).
# 물어보면 답하는 건 카드 몫이고 실제로 답한다(§7 실측).
_PROACTIVE_FRAME = (
    "(사용자는 지금 아무 말도 안 했어. 아래 소재로 네가 먼저 말을 꺼내줘 — "
    "사용자가 알려준 게 아니야. 어떻게 알게 됐는지는 굳이 말하지 말고, "
    "어디서 봤다거나 들었다고 출처를 지어내지도 마. "
    "소재를 다 옮기려 하지 말고 네가 가장 흥미로운 것 하나만 골라서, "
    "거기 나온 작품·제품 이름은 그대로 말해. 질문으로 끝내지 마. "
    "{language_rule}: {trigger})"
)
# "짧게"를 길이 지시가 아니라 **선택 지시**로 바꾼 이유(사용자 판단 2026.08.28):
# 길이를 세게 강제하면 사실이 잘려 나간다. 분업을 다시 그었다 — 요약기는 누락 없이
# 남기고(SUMMARY_SYSTEM ⑦), 무엇을 말할지 고르는 건 여기가 한다. 지금까진 아무도
# "고르라"는 말을 안 들어서, 요약기는 압축하다 고유명사를 버리고 두뇌는 다 전하려다
# 길어졌다(실측: haiku 112자/21초).
# 고유명사 규칙을 여기도 둔 건 요약기 ⑥이 애써 남긴 이름을 두뇌가 다시 버렸기 때문이다
# (실측: gemini가 "GTA 6"을 "이번에 새로 나올 게임"으로 뭉갰다).
# "되묻지 말고"를 "질문으로 끝내지 마"로 바꾼 근거(실측 2026.08.28, 실 재료 4건): 나쁜 3건이
# **전부 끝을 질문으로 맺었다**("너 게임 하는 사람이야?"). 되묻기 자체보다 나쁜 건 이미
# 알아야 할 걸 묻는 것이다 — 카드상 같이 사는 사이인데 처음 만난 사람에게 할 말이 된다.
# 관측된 실패가 문장 끝의 질문이라 표적을 거기로 좁혔다. 세계관과 무관해 카드 종속은 없다.

# 대화 콜백은 소재의 출처가 **사용자 자신**이라 위 프레임을 그대로 쓸 수 없다.
# 두 군데가 정반대다: ①"사용자가 알려준 게 아니야"는 여기선 거짓이고 ②뉴스는 되묻지
# 말아야 하지만 콜백은 되묻는 것이 목적이다.
_CALLBACK_FRAME = (
    "(사용자는 지금 아무 말도 안 했어. 아래는 사용자가 전에 한 말에서 나온 거야 — "
    "그 뒤가 어떻게 됐는지 궁금해서 네가 먼저 물어보는 거고, "
    "새로 알게 된 소식이 아니야. "
    "짧게 한두 문장으로. "
    "{language_rule}: {trigger})"
)

# turn_assembly.md §3.3의 "enum + 전략맵". 조각이 더 늘면 Builder로 승격한다.
_FRAMES = {
    TurnKind.PROACTIVE: _PROACTIVE_FRAME,
    TurnKind.PROACTIVE_CALLBACK: _CALLBACK_FRAME,
}


# 친밀도는 폐기됐다(D9, aliveness.md §3.3). 저장소의 값은 올리는 곳이 없어 늘 0이었고,
# 그래서 카드의 첫 프로필만 골라 왔다. 그 0을 기억에서 읽어 오던 것을 상수로 바꿨다 —
# 거동은 같고, 조립이 기억 포트(MemoryPort)에 폐기된 메서드를 요구하지 않게 된다.
# 카드의 프로필 단계 구조 자체는 페르소나 스키마를 손볼 때 함께 정리한다.
_NO_INTIMACY = 0.0

# 기억한 사실 블록의 머리말. 프레임과 같은 제약이 걸린다 — **어느 카드에도 성립해야 한다.**
# 그래서 "어떤 사이인지"는 말하지 않고(카드 소유) 쓰는 법만 말한다.
#   · "'사용자'는 지금 너와 말하는 상대야": 사실 문장이 3인칭("사용자는 …")이라 그게
#     누구인지 못 박아 둔다. 안 그러면 제3자 이야기로 읽힐 수 있다.
#   · "대화 기록에는 없어도 실제로 있었던 일": 카드 공통 규칙(persona/card.py)이
#     "이어지는 대화 기록만이 실제로 있었던 일이다"라고 말한다. 사실은 바로 그 기록
#     밖에서 오므로, 풀어 주지 않으면 모델이 규칙을 따라 사실을 무시할 수 있다.
#     system 쪽 규칙을 고치지 않은 건 사실이 없는 턴의 프롬프트를 건드리지 않기 위해서다.
#   · "늘어놓지 마": 아는 걸 다 말하려 드는 게 가장 흔한 실패일 것이라 미리 막는다.
#     **실측 전의 추정이다** — 실제로 어떻게 새는지 보고 고친다.
# 사실은 재료와 마찬가지로 한국어 고정이라, 카드 언어가 다르면 같은 언어 규칙을 붙인다.
_FACTS_HEADER = (
    "[기억]\n"
    "아래는 지난 대화들에서 네가 알게 된 것들이야. 지금 보이는 대화 기록에는 없어도 "
    "실제로 있었던 일이야. '사용자'는 지금 너와 말하는 상대야. "
    "이미 알고 있는 걸 전제로 말하되, 지금 이야기와 상관없으면 꺼내지 말고 "
    "목록처럼 늘어놓지도 마."
)
_FACTS_LANGUAGE_RULE = " 아래는 한국어로 적혀 있지만 답은 {language}로 해."


def _facts_block(facts: list[Fact], language: str) -> str:
    """사실 목록 → 프롬프트 블록. 없으면 빈 문자열(머리말만 보내지 않는다)."""
    if not facts:
        return ""
    header = _FACTS_HEADER
    if language != _MATERIAL_LANGUAGE:
        header += _FACTS_LANGUAGE_RULE.format(
            language=_LANGUAGE_NAMES.get(language, language)
        )
    return "\n".join([header, *(f"- {f.text}" for f in facts)])


class Conductor:
    def __init__(self, card: CharacterCard, memory: MemoryPort, config: Config):
        self._card = card
        self._memory = memory
        self._config = config

    def set_card(self, card: CharacterCard, persona_id: str | None = None) -> None:
        """페르소나 카드 교체 — 다음 build_request부터 적용.

        system_prompt는 매 턴 재조립(캐시 없음)이라 재할당만으로 충분하다.
        persona_id는 여기선 안 쓴다 — 연결 모드의 짝(remote.RemoteConductor)이 서버에 보낼
        키로 쓴다. 호출부(SwapRuntime)가 모드를 몰라도 되게 같은 모양으로 받는다.
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
        turns = self._recall_turns(user_id)
        messages = [Message(role=t.role, text=t.text) for t in turns]
        messages.extend(self._frame(kind, trigger_text, self._card.language))
        facts = self._recall_facts(user_id, trigger_text)
        request = LlmRequest(
            system=self._card.system_prompt(_NO_INTIMACY),
            messages=messages,
            model=self._config.brain.model,
            context=_facts_block(facts, self._card.language),
        )
        # 사실 수와 글자 수를 매 턴 남긴다 — 사실이 늘어 전부 넣을 수 없게 되는 시점
        # (검색 도입)의 근거다(design/memory.md §1 조건 3). 토큰이 아니라 글자인 건
        # 벤더 중립으로 셀 수 있는 게 그것뿐이어서다.
        log.info(
            "요청 조립 — system %d자, 사실 %d건(%d자), 메시지 %d개(기억 %d턴 + 트리거), model=%s",
            len(request.system), len(facts), len(request.context),
            len(messages), len(turns), request.model,
        )
        log.debug("system 전문:\n%s", request.system)
        log.debug("context 전문:\n%s", request.context)
        log.debug("messages: %s", messages)
        return request

    def _recall_turns(self, user_id: int) -> list[Turn]:
        """최근 대화. 사실을 주입하는 동안만 줄어드는 창을 쓴다.

        창을 줄여도 되는 근거는 "빠진 구간을 사실이 대신한다"뿐이다. 주입을 꺼 두면
        (enabled·max_injected) 그 근거가 없으므로 예전처럼 고정 길이로 읽는다.
        """
        cfg = self._config
        if not cfg.facts.enabled or cfg.facts.max_injected <= 0:
            return self._memory.recall_recent_for_user(user_id, cfg.recent_turns)
        return self._memory.recall_working_window(
            user_id, cfg.recent_turns_min, cfg.recent_turns
        )

    def _recall_facts(self, user_id: int, trigger_text: str) -> list[Fact]:
        """이번 턴에 넣을 사실. 꺼 두었거나(enabled·max_injected) 인출이 실패하면 빈 목록.

        실패를 삼키는 이유: 사실은 있으면 좋은 것이지 턴의 전제가 아니다. 기억 저장소가
        서버로 가면 인출은 네트워크 호출이 되는데, 그게 늦거나 죽었다고 나비가 대답을
        못 하면 안 된다. 최근 턴 인출은 삼키지 않는다 — 그건 없으면 대화가 아니다.
        """
        cfg = self._config.facts
        if not cfg.enabled or cfg.max_injected <= 0:
            return []
        try:
            return self._memory.recall_relevant_facts(user_id, trigger_text, cfg.max_injected)
        except Exception:
            log.warning("사실 인출 실패 — 이번 턴은 사실 없이 간다", exc_info=True)
            return []

    @staticmethod
    def _frame(kind: TurnKind, trigger_text: str, language: str = "ko") -> list[Message]:
        """트리거를 kind에 맞는 tail 메시지로 조립한다 (turn_assembly.md §3.1).

        REACTIVE는 진짜 사용자 발화라 그대로, 선제 kind는 나비에게 주어진 소재라
        프레이밍으로 감싼다. 시스템 프롬프트(카드 코어)는 **모든 kind에서 무변경** —
        차이는 여기 tail에만 둬 캐시 prefix를 안 쪼갠다.

        language는 카드가 선언한 발화 언어(E5) — 선제 프레이밍에만 박힌다. REACTIVE는
        사용자 발화가 이미 그 언어라 지시가 불필요하고, 넣으면 매 턴 토큰만 는다.
        """
        frame = _FRAMES.get(kind)
        if frame is None:  # REACTIVE — 진짜 사용자 발화라 감싸지 않는다
            return [Message(role="user", text=trigger_text)]
        # 카드 언어가 재료 언어와 같으면 지시가 자기를 부정한다 — 아예 빼는 게 맞다.
        # 카드 검증을 통과한 언어만 오지만(SUPPORTED_LANGUAGES), 이름이 없으면 코드를
        # 그대로 쓴다 — 프레이밍 한 줄 때문에 턴이 죽는 게 더 나쁘다.
        rule = (
            ""
            if language == _MATERIAL_LANGUAGE
            else _LANGUAGE_RULE.format(language=_LANGUAGE_NAMES.get(language, language))
        )
        text = frame.format(trigger=trigger_text, language_rule=rule)
        # 규칙이 빠지면 " .:" 같은 자국이 남는다 — 공백 정리 + 문장부호 중복 제거.
        return [Message(role="user", text=re.sub(r"\s+", " ", text).replace(".:", ":"))]
