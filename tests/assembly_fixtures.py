"""조립 공유 픽스처 — Python 조립의 결과를 JSON으로 떠서 서버(Java)가 같은 값을 내는지 본다.

서버의 요청 조립은 `conductor.py`·`persona/card.py`·`mouth/mood.py`와 Anthropic 어댑터의
메시지 정규화를 Java로 다시 쓴 것이다(design/service.md §11). 다시 쓴 코드가 원본과 같은
문자열을 내는지는 눈으로 못 본다 — 공백 하나, 문장부호 하나가 프롬프트를 바꾼다. 그래서
원본을 실제로 돌린 결과를 파일로 남기고 양쪽 테스트가 같은 파일을 읽는다.

- 이 파일이 **기대값의 출처**다. 손으로 JSON을 고치지 않는다.
- 조립 코드나 카드를 고치면 `python -m tests.assembly_fixtures --write`로 다시 뜬다.
  안 뜨면 `test_assembly_fixtures.py`가 깨진다(파일이 낡았다는 뜻).
- 동등성은 제품 보증이 아니다(service.md §2.3) — 이식 시점의 검증 수단이다. 프레임 문구의
  원본이 서버로 넘어가면 이 방향(Python → Java)은 뒤집힐 수 있다.
"""

from __future__ import annotations

import asyncio
import json
import sys
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

from navi.brain.anthropic import _normalize
from navi.conductor import Conductor
from navi.memory import MemoryPort
from navi.models import Fact, Message, Turn, TurnKind
from navi.mouth.mood import peel_mood, strip_mood_tag
from navi.persona import CharacterCard

ROOT = Path(__file__).parents[1]
PERSONAS = ROOT / "personas"
FIXTURES = Path(__file__).parent / "fixtures"
ASSEMBLY_PATH = FIXTURES / "assembly.json"
MOOD_PATH = FIXTURES / "mood.json"

_MODEL = "fixture-model"
_TS = datetime(2026, 11, 16, tzinfo=UTC)


class _FixedMemory(MemoryPort):
    """주어진 턴·사실을 그대로 돌려주는 기억 — 창 길이 계산은 이 픽스처의 대상이 아니다."""

    def __init__(self, turns: list[Turn], facts: list[Fact]):
        self._turns = turns
        self._facts = facts

    def append_turn(self, session_id, user_id, role, text, trigger_type="manual", *, ts=None):
        raise AssertionError("조립은 기억에 쓰지 않는다")

    def recall_recent(self, session_id, n):
        return self._turns

    def recall_recent_for_user(self, user_id, n):
        return self._turns

    def recall_working_window(self, user_id, min_rows, max_rows):
        return self._turns

    def recall_relevant_facts(self, user_id, query, k):
        return self._facts[:k]


def _config() -> SimpleNamespace:
    # Conductor가 읽는 필드만 — Config 전체(마이크·모드·GUI…)는 조립과 무관하다.
    return SimpleNamespace(
        brain=SimpleNamespace(model=_MODEL),
        facts=SimpleNamespace(enabled=True, max_injected=50),
        recent_turns=20,
        recent_turns_min=8,
    )


# 대화 이력 형태 — 정규화(_normalize)가 흡수해야 하는 모양을 일부러 넣는다.
_HISTORIES: dict[str, list[tuple[str, str]]] = {
    "empty": [],
    "alternating": [
        ("user", "오늘 회사에서 발표했어."),
        ("assistant", "오, 어땠어?"),
        ("user", "생각보다 괜찮았어."),
        ("assistant", "그럴 줄 알았어."),
    ],
    # 능동 발화가 첫 행 — assistant로 시작한다.
    "assistant_first": [
        ("assistant", "일어났어?"),
        ("user", "응 방금."),
        ("assistant", "잘 잤어?"),
    ],
    # 답 없이 끝난 세션 뒤 새 질문 — user가 연달아 온다. 마지막 user는 트리거와도 붙는다.
    "consecutive_user": [
        ("user", "나 왔어."),
        ("user", "아무도 없나?"),
        ("assistant", "여기 있어."),
        ("user", "아 깜짝이야."),
    ],
}

_FACTS = [
    "사용자의 동생 이름은 민준이다.",
    "사용자는 매운 음식을 못 먹는다.",
    "사용자는 2026년 11월 20일에 이직 면접이 있다.",
]

_TRIGGERS = {
    TurnKind.REACTIVE: "나 오늘 좀 피곤해.",
    TurnKind.PROACTIVE: "GTA 6의 출시일이 다시 미뤄졌다. 락스타는 완성도를 이유로 들었다.",
    TurnKind.PROACTIVE_CALLBACK: "사용자는 지난주에 이직 면접을 본다고 했다.",
}

# (카드 파일, kind, 이력, 사실 유무). 전 조합이 아니라 갈림길마다 하나씩:
#   · 카드 언어가 재료 언어(ko)와 같을 때/다를 때 — 언어 규칙이 빠지거나 붙는다
#   · kind 셋 — 프레임 없음/소식 프레임/콜백 프레임
#   · 사실 유무 — context가 비거나 채워진다
#   · 이력 형태 — 정규화가 달라진다
_CASES: list[tuple[str, TurnKind, str, bool]] = [
    ("navi.yaml", TurnKind.REACTIVE, "empty", False),
    ("navi.yaml", TurnKind.REACTIVE, "alternating", False),
    ("navi.yaml", TurnKind.REACTIVE, "alternating", True),
    ("navi.yaml", TurnKind.REACTIVE, "assistant_first", False),
    ("navi.yaml", TurnKind.REACTIVE, "consecutive_user", True),
    ("navi.yaml", TurnKind.PROACTIVE, "empty", False),
    ("navi.yaml", TurnKind.PROACTIVE, "alternating", True),
    ("navi.yaml", TurnKind.PROACTIVE_CALLBACK, "alternating", True),
    ("navi.yaml", TurnKind.PROACTIVE_CALLBACK, "consecutive_user", False),
    ("example_kr.yaml", TurnKind.REACTIVE, "alternating", True),
    ("example_jp.yaml", TurnKind.REACTIVE, "empty", False),
    ("example_jp.yaml", TurnKind.REACTIVE, "alternating", True),
    ("example_jp.yaml", TurnKind.PROACTIVE, "alternating", False),
    ("example_jp.yaml", TurnKind.PROACTIVE, "assistant_first", True),
    ("example_jp.yaml", TurnKind.PROACTIVE_CALLBACK, "empty", True),
]


def _messages(msgs: list[Message]) -> list[dict[str, str]]:
    return [{"role": m.role, "text": m.text} for m in msgs]


def build_assembly() -> dict:
    cases = []
    for card_file, kind, history, with_facts in _CASES:
        card = CharacterCard.load(PERSONAS / card_file, root=ROOT)
        turns = [
            Turn(role=r, text=t, created_at=_TS, session_id="fixture")
            for r, t in _HISTORIES[history]
        ]
        fact_texts = _FACTS if with_facts else []
        facts = [
            Fact(
                fact_id=i,
                kind="profile",
                text=t,
                source_turn_id=None,
                status="active",
                expires_at=None,
                created_at=_TS,
            )
            for i, t in enumerate(fact_texts, 1)
        ]
        conductor = Conductor(card=card, memory=_FixedMemory(turns, facts), config=_config())
        trigger = _TRIGGERS[kind]
        request = conductor.build_request(trigger, user_id=1, session_id="fixture", kind=kind)
        cases.append(
            {
                "name": f"{Path(card_file).stem}/{kind.value}/{history}/"
                f"{'facts' if with_facts else 'nofacts'}",
                "input": {
                    "card": card_file,
                    "kind": kind.value,
                    "trigger": trigger,
                    "turns": [{"role": r, "text": t} for r, t in _HISTORIES[history]],
                    "facts": fact_texts,
                },
                "expected": {
                    "language": card.language,
                    "system": request.system,
                    "context": request.context,
                    "messages": _messages(request.messages),
                    # Anthropic 제약(user 시작·교대)을 맞춘 뒤의 모양 — 어댑터가 실제로 보낸다.
                    "anthropic_messages": _messages(_normalize(request.messages)),
                },
            }
        )
    return {
        "_comment": "생성 파일 — 손으로 고치지 않는다. python -m tests.assembly_fixtures --write",
        "cases": cases,
    }


# 두뇌 응답 스트림의 토큰 쪼개짐 — 태그가 어디서 잘려 와도 같은 결과여야 한다.
_MOOD_STREAMS: dict[str, list[str]] = {
    "tag_single_token": ["[mood:bright] 오, 자랑해 봐."],
    "tag_split_mid_literal": ["[mo", "od:ca", "lm]", " 괜찮아.", " 천천히 해."],
    "tag_split_per_char": [*"[mood:neutral]", " 응."],
    "tag_then_body_same_token": ["[mood:bright]", "오", " 좋네."],
    "tag_leading_whitespace": ["  [mood:calm]  그랬구나."],
    "tag_uppercase_key": ["[mood:BRIGHT] 와."],
    "tag_spaces_inside": ["[mood: calm ] 응."],
    "tag_unknown_key": ["[mood:angry] 뭐라고?"],
    "tag_only": ["[mood:calm]"],
    "no_tag": ["그냥 ", "말하는 ", "거야."],
    "no_tag_bracket_other": ["[웃음] 하하."],
    "no_tag_prefix_then_diverge": ["[mo", "ve] 가자."],
    "unclosed_tag_hits_limit": ["[mood:", "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa", " 뒤"],
    "tag_not_at_start": ["음 [mood:bright] 그래."],
    "empty_stream": [],
    "empty_tokens_then_tag": ["", "[mood:bright] 응."],
}

_STRIP_TEXTS = [
    "[mood:bright] 오, 자랑해 봐.",
    "  [mood:calm]\n그랬구나.",
    "[mood:angry] 뭐라고?",
    "태그 없음.",
    "음 [mood:bright] 그래.",
    "[mood:bright] [mood:calm] 두 개.",
    "",
]


async def _peel(tokens: list[str]) -> tuple[str, str]:
    async def gen():
        for t in tokens:
            yield t

    mood, body = await peel_mood(gen())
    return mood, "".join([t async for t in body])


def build_mood() -> dict:
    peel = []
    for name, tokens in _MOOD_STREAMS.items():
        mood, body = asyncio.run(_peel(tokens))
        peel.append({"name": name, "tokens": tokens, "mood": mood, "body": body})
    strip = [{"text": t, "expected": strip_mood_tag(t)} for t in _STRIP_TEXTS]
    return {
        "_comment": "생성 파일 — 손으로 고치지 않는다. python -m tests.assembly_fixtures --write",
        "peel": peel,
        "strip": strip,
    }


def dumps(data: dict) -> str:
    return json.dumps(data, ensure_ascii=False, indent=2) + "\n"


BUILDERS = {ASSEMBLY_PATH: build_assembly, MOOD_PATH: build_mood}


def main(argv: list[str]) -> int:
    if argv != ["--write"]:
        print("사용법: python -m tests.assembly_fixtures --write")
        return 2
    FIXTURES.mkdir(exist_ok=True)
    for path, build in BUILDERS.items():
        # 줄바꿈을 LF로 고정한다 — Windows에서 떠도 바이트가 같아야 비교가 된다.
        path.write_text(dumps(build()), encoding="utf-8", newline="\n")
        print(f"썼다: {path.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
