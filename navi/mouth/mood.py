"""무드 선행 태그 파서 — 두뇌 응답 첫 토큰의 `[mood:key]`를 흡수한다 (aliveness §1.2).

빠른 경로 원리(aliveness §0): 무드는 합성 *전에* 필요해 느린 경로에 못 둔다 → 두뇌가 응답
맨 앞에 선행 태그로 뱉고, 데몬이 그걸 읽어 레퍼런스(톤)를 고른 뒤 **나머지 본문만** TTS로
흘린다. 태그 문자열 자체는 절대 하류(TTS·기억·화면)로 새지 않는다 — 읽히면 "대괄호 기쁨" 사고.

파서는 스트림 앞머리만 잠깐 버퍼링한다: 여는 `[mood:`가 안 오면 즉시 원문을 무손실로 흘려
TTFA(첫 오디오)를 건드리지 않는다. 미지 키·형식 오류·태그 부재는 전부 `neutral` 폴백이라
LLM이 스키마 밖 키를 뱉어도 안전하다(aliveness §1.2 "폴백은 결정론").
"""

from __future__ import annotations

import re
from collections.abc import AsyncIterator

# 1차 무드 키 — neutral(평상, 필수)·bright(신남)·calm(차분). comfort(위로)는 후속(스키마만 열어둠).
MOODS = ("neutral", "bright", "calm")
DEFAULT_MOOD = "neutral"

# 선두 태그 `[mood:key]` — 앞뒤 공백 허용, 키는 영문(대소문자 무시).
_TAG_RE = re.compile(r"^\s*\[mood:\s*([A-Za-z]+)\s*\]\s*")
# 여는 리터럴 — 버퍼가 이것의 접두사인 동안만 태그를 더 기다린다.
_OPEN = "[mood:"
# 태그를 기다리며 버퍼링할 안전 한도 — 유효 태그(`[mood:neutral]`=14자)보다 넉넉.
_SCAN_LIMIT = 32


def _classify(key: str) -> str:
    """스키마 안이면 그대로, 밖이면 neutral 폴백."""
    key = key.lower()
    return key if key in MOODS else DEFAULT_MOOD


async def peel_mood(tokens: AsyncIterator[str]) -> tuple[str, AsyncIterator[str]]:
    """스트림 앞머리에서 `[mood:key]`를 떼고 `(mood, 본문_반복자)`를 돌려준다.

    태그가 없거나 형식이 어긋나면 `(neutral, 원문 전체)` — 무손실. 판정을 위해 미리 당긴
    토큰은 폴백 시 그대로 되돌려 흘린다. 본문 반복자는 지연 없이(read-ahead) 나머지를 잇는다.
    """
    buf = ""
    consumed: list[str] = []  # 태그 판정을 위해 미리 당긴 토큰(폴백 시 되돌린다)
    peeled = False
    mood = DEFAULT_MOOD
    body_start = ""

    ait = tokens.__aiter__()
    while True:
        try:
            tok = await ait.__anext__()
        except StopAsyncIteration:
            break
        consumed.append(tok)
        buf += tok
        m = _TAG_RE.match(buf)
        if m:
            mood = _classify(m.group(1))
            body_start = buf[m.end() :]
            peeled = True
            break
        stripped = buf.lstrip()
        # 아직 태그가 될 여지가 있나: 버퍼가 `[mood:`의 접두사인 동안만 기다린다.
        if stripped and not _OPEN.startswith(stripped[: len(_OPEN)]):
            break
        if len(buf) >= _SCAN_LIMIT:
            break

    async def _body() -> AsyncIterator[str]:
        if peeled:
            if body_start:
                yield body_start
        else:
            for t in consumed:  # 태그 없음 — 미리 당긴 토큰을 무손실로 흘린다
                yield t
        async for t in ait:
            yield t

    return mood, _body()


def strip_mood_tag(text: str) -> str:
    """확정 전문에서 선두 `[mood:key]` 태그를 제거한다 — 기억 저장·표시용.

    합성 경로는 peel_mood가 막지만, 두뇌 어댑터의 `full_text`엔 태그가 남는다.
    이걸 벗기지 않으면 단기기억에 저장돼 다음 턴 맥락으로 되먹여져 LLM이 태그를
    "대사"로 학습한다(오염).
    """
    return _TAG_RE.sub("", text, count=1)
