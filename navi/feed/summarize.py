"""수집한 항목을 나비 입말 재료로 소화한다 (D13/feed.md 3.3).

"언제 말할까=규칙, 무엇을 말할까=모델" 원칙 안에 있다 — 수집 주기·중복방지·필터는
전부 결정론이고, LLM은 요약이라는 "무엇을 말할까"에만 쓴다. 저가 티어 1회성 호출.

collect.py가 아니라 별도 모듈인 이유: 프롬프트는 튜닝 대상 자산이고, feed.md PR2의
대화 콜백 추출 프롬프트가 여기 나란히 붙을 자리다. collect.py는 오케스트레이션만 한다.
"""

from __future__ import annotations

import logging
import re

from datetime import datetime

from navi.brain.base import BrainAdapter
from navi.feed.base import RawItem
from navi.models import LlmRequest, Message, Turn

log = logging.getLogger(__name__)

# 2인칭 요약("네가 응원하는 팀이…")을 주면 두뇌가 화자를 재해석한다 — 실측으로 확인된
# 결함이다(turn_assembly.md 4.1 ③). 인칭·말투는 발화 시점에 두뇌가 입히는 것이고,
# 여기서 만드는 건 재료(사실)일 뿐이다.
#
# ④ 언어를 못 박는 이유: 재료는 사용자에게도 페르소나에게도 안 보이는 내부 표현이고,
# 오직 두뇌만 읽는다. 페르소나에 맞출 수도 없다 — 수집은 배치라 어느 카드가 이 재료로
# 말할지 그 시점엔 모르고, 사용자는 런타임에 페르소나를 갈아끼운다. 지금까진 시스템
# 프롬프트가 한국어라 모델이 따라오는 부작용에 기대고 있었다(실측 2026.08.02: gemini·
# haiku 둘 다 영문 기사를 한국어로 요약). 규칙이 아니라 부작용이라 모델이 바뀌면 흔들린다.
SUMMARY_SYSTEM = (
    "너는 뉴스 항목에서 **나중에 쓸 재료**를 남기는 정리기다. 규칙: "
    "①사실만, 중립 시점으로 쓴다. "
    "②청자를 지칭하지 않는다('너', '네가' 금지). "
    "③인사·감상·말투를 붙이지 않는다. 인칭과 말투는 나중에 다른 곳에서 입힌다. "
    "④원문이 무슨 언어든 한국어로 쓴다. "
    "⑤제목·머리말·목록기호 없이 문장만 쓴다. "
    "⑥작품·제품·인물 이름 같은 고유명사와 숫자·날짜는 반드시 남긴다. "
    "회사명보다 작품명이 먼저다. "
    "⑦짧게 만들려고 사실을 버리지 마라. 세 문장까지 써도 된다 — "
    "무엇을 말할지 고르는 건 이 다음 단계가 한다."
)
# ⑥의 근거(실측 2026.08.28): gemini가 게임 뉴스를 요약하며 **회사명은 남기고 게임명을
# 버렸다**("엘엔케이로직코리아가 서비스 23주년을 맞아…"). 두뇌는 재료에 없는 걸 말할 수
# 없어 "게임이 23년을 버텼대"로 뭉갰고, 무슨 게임인지 모르는 발화가 됐다. 같은 기사를
# haiku가 요약했을 땐 "붉은보석"이 남아 발화 품질이 눈에 띄게 나았다.

_FENCE = re.compile(r"^```[^\n]*\n(.*?)\n?```$", re.DOTALL)
_HEADING = re.compile(r"^\s{0,3}#{1,6}\s+.*$", re.MULTILINE)
_BULLET = re.compile(r"^\s{0,3}[-*+]\s+", re.MULTILINE)
# 번호목록은 별도 취급 — "2026. 8월 출시"·"3. 1운동" 같은 정상 문장이 같은 모양이라
# 무조건 지우면 연도나 날짜가 소리 없이 잘려 나간다. 줄이 둘 이상 걸릴 때만 진짜 목록으로
# 본다(요약은 한두 문장이라 번호목록이면 항목이 여럿이다).
_NUMBERED = re.compile(r"^\s{0,3}\d+\.\s+", re.MULTILINE)
_NUMBERED_MIN_LINES = 2
_BOLD = re.compile(r"\*\*(.+?)\*\*", re.DOTALL)
_WHITESPACE = re.compile(r"\s+")


def clean_summary(text: str) -> str:
    """모델이 얹은 서식을 걷어낸다 — 재료는 그대로 발화 트리거가 되기 때문이다.

    프롬프트로 형식을 막는 건 확률을 낮출 뿐이라 결정론 후처리를 둔다(무드 태그를
    peel_mood가 흡수하는 것과 같은 자리). 실측 2026.08.02: haiku가 '# 요약'을 머리말로
    붙였고, 그대로 두면 나비가 "샵 요약"이라고 소리 내 읽는다.
    """
    text = text.strip()
    fence = _FENCE.match(text)
    if fence:
        text = fence.group(1)
    text = _HEADING.sub("", text)
    text = _BULLET.sub("", text)
    if len(_NUMBERED.findall(text)) >= _NUMBERED_MIN_LINES:
        text = _NUMBERED.sub("", text)
    text = _BOLD.sub(r"\1", text)
    return _WHITESPACE.sub(" ", text).strip()


async def drain(brain: BrainAdapter, request: LlmRequest) -> str:
    """스트림을 끝까지 소진하고 **정제하지 않은** 확정 전문을 돌려준다.

    BrainAdapter 계약상 last_result는 소진 후에만 유효하다(brain/base.py). 요약은
    스트리밍이 필요 없지만 어댑터에 1회성 호출 경로가 없어 이 관용구를 쓴다.

    정제(clean_summary)는 호출부 몫이다. 예전엔 여기서 몰래 걸었는데, 그러면 줄 단위
    구조를 가진 응답(콜백 추출)이 파싱 전에 공백으로 뭉개진다. 이름값대로 전문만 준다.
    """
    async for _ in brain.generate_stream(request):
        pass
    result = brain.last_result
    return result.full_text if result else ""


async def summarize_item(brain: BrainAdapter, model: str, item: RawItem) -> str:
    """항목 하나 → 나비가 꺼낼 재료 한 문장. 실패·빈 응답은 호출부가 스킵으로 처리."""
    request = LlmRequest(
        system=SUMMARY_SYSTEM,
        messages=[Message(role="user", text=f"{item.title}\n{item.summary}".strip())],
        model=model,
    )
    return clean_summary(await drain(brain, request))


# ─── ② 대화 콜백 추출 (feed.md 3.4) ──────────────────────────

# 재료는 **중립 진술**이지 완성된 되물음이 아니다. feed.md 3.4는 원래 "되물음 문장"이라고
# 썼는데 같은 문서 3.3("중립 시점으로, 청자를 지칭하지 말 것")과 충돌했고, 3.3이
# turn_assembly.md 4.1 실측을 근거로 든 쪽이라 그걸 따랐다(2026.08.24 확정).
# 완성된 되물음은 반말·종결어미가 재료에 박혀 존댓말 카드로 갈아끼울 때 카드와 싸운다 —
# 수집은 배치라 어느 카드가 이 재료로 말할지 그 시점엔 모른다.
# 대신 규칙 ⑤(미결 여부)가 되물음의 *근거*를 남긴다. 문장 형태는 두뇌가 만든다.
CALLBACK_SYSTEM = (
    "너는 사용자의 최근 대화에서 '나중에 다시 물어볼 만한 화제'를 뽑는 추출기다. "
    "여러 번 나왔거나 결론이 나지 않은 화제를 최대 2개 고른다. 규칙: "
    "①한 줄에 하나씩 `[topic:라벨] 문장` 형식으로만 쓴다. 그 밖의 말은 쓰지 않는다. "
    "②라벨은 화제를 가리키는 짧은 명사 하나(10자 이내). "
    "③문장은 사용자를 '사용자'라고 3인칭으로 부르고, 사용자가 무슨 말을 했는지 사실만 적는다. "
    "④청자를 지칭하지 않는다('너', '네가' 금지). 질문·인사·말투를 쓰지 않는다. "
    "⑤결론이 났는지 아직 안 났는지가 드러나면 함께 적는다. "
    "⑥한국어로 쓴다. "
    "⑦뽑을 만한 화제가 없으면 아무것도 쓰지 않는다."
)

_TOPIC_RE = re.compile(r"^\s*\[topic:\s*([^\]\n]{1,20})\s*\]\s*(\S.*)$", re.MULTILINE)
_MAX_TURN_CHARS = 200  # 붙여넣기 한 덩어리가 프롬프트를 삼키는 걸 막는다

# 라벨 드리프트 흡수용 접미 목록. LLM이 같은 화제를 "이직"과 "이직 고민"으로 번갈아 부르면
# dedup이 뚫려 같은 얘기를 반복해서 되묻는다. 형태소 분석이 아니라 짧은 목록이라
# feed.md 5의 비목표(임베딩·유사도 병합)를 넘지 않는다.
_LABEL_SUFFIXES = ("고민", "이야기", "얘기", "문제", "관련", "근황", "계획")


def normalize_topic_label(label: str) -> str:
    """dedup 축이 될 라벨을 정규화한다 — 저장되는 topic_key도 이 값이다.

    한계: "커리어"와 "이직"처럼 어휘가 아예 다른 드리프트는 막지 못한다. 그 대가는 12시간에
    후보 1건 추가이고 max_callbacks 상한이 감싼다. 의미 기반 병합은 D6(임베딩) 이후.
    """
    normalized = _WHITESPACE.sub("", label).casefold()
    for suffix in _LABEL_SUFFIXES:
        if normalized.endswith(suffix) and len(normalized) - len(suffix) >= 2:
            normalized = normalized[: -len(suffix)]
            break  # 한 겹만 — "고민이야기" 같은 중첩까지 쫓지 않는다
    return normalized


def parse_callbacks(text: str, max_callbacks: int) -> list[tuple[str, str]]:
    """`[topic:라벨] 문장` 줄들을 (정규화 라벨, 문장) 쌍으로. 못 읽는 줄은 버린다.

    JSON이 아니라 줄 태그인 이유는 peel_mood(mood.py)의 선례이기도 하지만, 부분 실패가
    국소적이기 때문이다 — 모델이 앞에 "아래와 같이 정리했습니다:"를 붙이면 그 줄만 매치가
    안 돼 무시된다. JSON이면 같은 상황에서 전체 파싱이 터진다.

    파싱 실패는 예외가 아니라 폴백이다(빈 리스트 → ② 스킵).
    """
    fence = _FENCE.match(text.strip())  # 통째로 코드펜스에 감싸 오는 경우
    if fence:
        text = fence.group(1)

    pairs: list[tuple[str, str]] = []
    seen: set[str] = set()
    for raw_label, raw_summary in _TOPIC_RE.findall(text):
        label = normalize_topic_label(raw_label)
        summary = clean_summary(raw_summary)  # 줄마다 — 통짜로 걸면 줄 구조가 뭉개진다
        if not label or not summary or label in seen:
            continue
        seen.add(label)
        pairs.append((label, summary))
        if len(pairs) >= max_callbacks:
            break
    return pairs


def _relative_day(then: datetime, now: datetime) -> str:
    """결정론 상대 날짜 — 이게 있어야 요약에 '지난주에' 같은 시점이 들어간다."""
    days = (now.date() - then.date()).days
    if days <= 0:
        return "오늘"
    if days == 1:
        return "어제"
    if days < 7:
        return f"{days}일 전"
    if days < 30:
        return f"{days // 7}주 전"
    return f"{days // 30}개월 전"


def build_callback_prompt(turns: list[Turn], now: datetime) -> str:
    """사용자 턴만 시간순으로, 줄마다 상대 날짜를 붙여 한 덩어리로."""
    lines = [
        f"({_relative_day(t.created_at, now)}) {t.text[:_MAX_TURN_CHARS]}"
        for t in turns
        if t.role == "user"
    ]
    return "\n".join(lines)


async def extract_callbacks(
    brain: BrainAdapter, model: str, turns: list[Turn], now: datetime, max_callbacks: int
) -> list[tuple[str, str]]:
    """최근 턴 → (라벨, 중립 진술) 쌍 몇 개. LLM 호출은 배치당 1회.

    규칙 기반(형태소 빈도) 대신 LLM인 이유는 한국어 형태소 분석기 의존성 부담이고,
    추출은 "무엇을 말할까"라 원칙상 LLM 허용 구간이다(feed.md 3.4).
    """
    request = LlmRequest(
        system=CALLBACK_SYSTEM,
        messages=[Message(role="user", text=build_callback_prompt(turns, now))],
        model=model,
    )
    return parse_callbacks(await drain(brain, request), max_callbacks)
