"""검문① 키워드 게이트 검증 — LLM 없이 결정론적 모드 명령 판정을 고정한다.

명령어=긴 구절 + 정규화(2026.06.25 실측 반영): 짧은 단어는 Whisper STT가 오인식·환각으로
전멸 → 긴 구절 채택, STT가 붙이는 양끝 문장부호·공백은 정규화로 흡수한다.
"""

import pytest

from navi.gatekeeper import GateResult, _COMMANDS, check_gate


# --- PASS: 일반 대화는 통과 ---

def test_normal_utterance_passes():
    assert check_gate("오늘 날씨 어때?") == GateResult.PASS


def test_short_chat_passes():
    assert check_gate("안녕") == GateResult.PASS


def test_question_passes():
    assert check_gate("나비 뭐 하고 있어?") == GateResult.PASS


# --- PASS: 명령 구절이 문장에 묻힌 경우는 가로채지 않는다 (false positive 방지) ---

def test_sleep_phrase_in_sentence_passes():
    assert check_gate("나 이제 자라") == GateResult.PASS
    assert check_gate("나 오늘 잘 잤어") == GateResult.PASS
    # "이제 그만 잘게"를 포함하지만 발화 전체가 아니므로 통과
    assert check_gate("이제 그만 잘게라고 말했어") == GateResult.PASS


# --- 등록된 명령 구절은 결과까지 정확히 가로챈다 (집합 전수) ---

@pytest.mark.parametrize(
    ("text", "expected"), sorted(_COMMANDS.items(), key=lambda kv: kv[0])
)
def test_registered_commands_are_caught(text: str, expected: GateResult):
    assert check_gate(text) == expected


# --- 선톡축 명령(Stage 14): 자연 발화 형태 그대로 매칭 ---

def test_mode_commands_natural_forms():
    assert check_gate("나 조금만 더 잘래.") == GateResult.SNOOZE
    assert check_gate("잘 잤어, 나비!") == GateResult.WAKE
    assert check_gate("지금은 방해하지 마") == GateResult.DND
    assert check_gate("이제 말 걸어도 돼~") == GateResult.DND_CLEAR


def test_mode_phrase_in_sentence_passes():
    # 부분 일치는 안 잡는다 — "더 잘래"류가 문장에 묻히면 일반 대화
    assert check_gate("어제는 조금만 더 잘래 하고 말았지") == GateResult.PASS
    assert check_gate("방해하지 마라는 말 들었어") == GateResult.PASS


# --- 정규화: STT 출력의 양끝 부호·공백을 흡수한다 ---

def test_trailing_punctuation_normalized():
    # STT가 흔히 붙이는 종결 부호 — 정규화로 흡수해야 매칭
    assert check_gate("이제 자러 갈게.") == GateResult.SLEEP
    assert check_gate("이제 그만 잘게!") == GateResult.SLEEP


def test_surrounding_whitespace_normalized():
    assert check_gate("  이제 그만 잘게  ") == GateResult.SLEEP
    assert check_gate("\t잘 자 나비\n") == GateResult.SLEEP


def test_spacing_variations_normalized():
    # 한국어 STT는 단어 경계(띄어쓰기)를 일관되게 안 준다 — 공백을 전부 무시해 흡수
    assert check_gate("이제그만잘게") == GateResult.SLEEP        # 붙여쓰기
    assert check_gate("이제 그만잘게") == GateResult.SLEEP       # 부분 띄어쓰기
    assert check_gate("이제  그만   잘게") == GateResult.SLEEP   # 이중 공백
    assert check_gate("잘자 나비") == GateResult.SLEEP


def test_internal_punctuation_normalized():
    # 양끝뿐 아니라 내부 쉼표 등도 제거
    assert check_gate("이제, 그만 잘게.") == GateResult.SLEEP


# --- 경계: 빈 문자열·공백 ---

def test_empty_string_passes():
    assert check_gate("") == GateResult.PASS


def test_whitespace_only_passes():
    assert check_gate("   ") == GateResult.PASS
