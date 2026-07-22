"""무드 선행 태그 파서·톤 매핑 검증.

peel_mood가 스트림 앞머리의 `[mood:key]`를 정확히 떼고, 태그가 본문으로 새지 않으며,
어긋난 입력은 전부 neutral로 수렴함을 고정한다. tone_for_mood는 카드 무드→톤 매핑.
"""

from __future__ import annotations

from collections.abc import AsyncIterator

from navi.mouth.mood import DEFAULT_MOOD, peel_mood, strip_mood_tag
from navi.persona.voice import PersonaVoice


async def _stream(*tokens: str) -> AsyncIterator[str]:
    for token in tokens:
        yield token


async def _drain(ait: AsyncIterator[str]) -> str:
    return "".join([tok async for tok in ait])


# --- peel_mood: 정상 태그 ------------------------------------------------


async def test_peels_tag_split_across_tokens():
    """태그가 토큰 경계에 쪼개져 와도 재조립해 뗀다."""
    mood, body = await peel_mood(_stream("[mo", "od:", "com", "fort", "] ", "고생했네."))
    # comfort는 1차 스키마 밖 → neutral 폴백(키 자체는 파싱됐지만 미지원)
    assert mood == "neutral"
    assert await _drain(body) == "고생했네."


async def test_peels_known_mood_and_strips_tag_from_body():
    mood, body = await peel_mood(_stream("[mood:bright] ", "오 ", "자랑해 봐."))
    assert mood == "bright"
    text = await _drain(body)
    assert text == "오 자랑해 봐."
    assert "[mood" not in text  # 태그가 본문으로 새지 않는다


async def test_calm_key():
    mood, body = await peel_mood(_stream("[mood:calm]", "조용히 있을게."))
    assert mood == "calm"
    assert await _drain(body) == "조용히 있을게."


async def test_uppercase_and_inner_spaces():
    mood, body = await peel_mood(_stream("[mood:  BRIGHT ]다행이다"))
    assert mood == "bright"
    assert await _drain(body) == "다행이다"


# --- peel_mood: 폴백(무손실) ---------------------------------------------


async def test_missing_tag_falls_back_to_neutral_and_keeps_all_text():
    mood, body = await peel_mood(_stream("안", "녕 ", "나비"))
    assert mood == DEFAULT_MOOD == "neutral"
    assert await _drain(body) == "안녕 나비"  # 미리 당긴 토큰까지 무손실


async def test_unknown_key_falls_back_to_neutral():
    mood, body = await peel_mood(_stream("[mood:angry] 화났어"))
    assert mood == "neutral"
    assert await _drain(body) == "화났어"


async def test_malformed_bracket_is_not_swallowed():
    """`[mood:`로 시작하지 않는 대괄호는 태그가 아니라 본문 — 삼키면 안 된다."""
    mood, body = await peel_mood(_stream("[웃음] 어서 와"))
    assert mood == "neutral"
    assert await _drain(body) == "[웃음] 어서 와"


async def test_empty_stream():
    mood, body = await peel_mood(_stream())
    assert mood == "neutral"
    assert await _drain(body) == ""


# --- strip_mood_tag: 기억 저장용 전문 정리 --------------------------------


def test_strip_removes_leading_tag_only():
    assert strip_mood_tag("[mood:bright] 자랑해 봐.") == "자랑해 봐."
    assert strip_mood_tag("태그 없음") == "태그 없음"
    # 본문 중간의 대괄호는 건드리지 않는다
    assert strip_mood_tag("[mood:calm] 아 [한숨] 그렇구나") == "아 [한숨] 그렇구나"


# --- tone_for_mood: 카드 무드 → 톤 ---------------------------------------


def _voice_with_moods() -> PersonaVoice:
    return PersonaVoice.parse(
        {
            "name": "aris",
            "gptsovits": {
                "tones": [
                    {"name": "기본", "voice_id": "base.wav"},  # mood 없음(neutral 기준)
                    {"name": "신남", "voice_id": "b.wav", "mood": "bright"},
                    {"name": "차분", "voice_id": "c.wav", "mood": "calm"},
                ]
            },
        }
    )


def test_tone_for_mood_matches_declared_mood():
    vv = _voice_with_moods().vendor("gptsovits")
    assert vv.tone_for_mood("bright").name == "신남"
    assert vv.tone_for_mood("calm").name == "차분"


def test_tone_for_mood_missing_or_empty_returns_none():
    vv = _voice_with_moods().vendor("gptsovits")
    assert vv.tone_for_mood("comfort") is None  # 카드에 없는 무드
    assert vv.tone_for_mood("") is None  # 빈 무드(neutral 폴백은 호출부 몫)
