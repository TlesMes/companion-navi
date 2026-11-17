"""공유 픽스처가 지금의 Python 조립과 일치하는지 — 낡은 파일로 서버를 검증하지 않게 한다.

깨지면: 조립 코드나 카드가 바뀐 것이다. 의도한 변경이면
`python -m tests.assembly_fixtures --write`로 다시 뜨고, 서버(Java) 쪽도 같이 고친다.
"""

import json

import pytest

from tests.assembly_fixtures import ASSEMBLY_PATH, BUILDERS, MOOD_PATH


@pytest.mark.parametrize("path", list(BUILDERS), ids=lambda p: p.name)
def test_fixture_is_current(path):
    assert path.exists(), "픽스처가 없다 — python -m tests.assembly_fixtures --write"
    assert json.loads(path.read_text(encoding="utf-8")) == BUILDERS[path]()


def test_assembly_covers_every_branch():
    """갈림길이 빠진 채 green이 되지 않게 — 픽스처가 실제로 양쪽을 다 담았는지 본다."""
    cases = json.loads(ASSEMBLY_PATH.read_text(encoding="utf-8"))["cases"]
    assert {c["input"]["kind"] for c in cases} == {
        "reactive", "proactive", "proactive_callback",
    }
    assert {c["expected"]["language"] for c in cases} >= {"ko", "ja"}
    assert any(c["expected"]["context"] for c in cases)
    assert any(not c["expected"]["context"] for c in cases)
    # 정규화가 실제로 일을 한 경우(병합·머리 삽입)가 들어 있어야 한다.
    assert any(
        c["expected"]["messages"] != c["expected"]["anthropic_messages"] for c in cases
    )
    # 다른 언어 카드의 선제 프레임에는 언어 규칙이 붙고, 한국어 카드에는 안 붙는다.
    by_name = {c["name"]: c for c in cases}
    assert "일본어로 해" in by_name["example_jp/proactive/alternating/nofacts"][
        "expected"
    ]["messages"][-1]["text"]
    assert "답은" not in by_name["navi/proactive/empty/nofacts"]["expected"]["messages"][
        -1
    ]["text"]


def test_mood_covers_fallbacks():
    data = json.loads(MOOD_PATH.read_text(encoding="utf-8"))
    by_name = {c["name"]: c for c in data["peel"]}
    assert by_name["tag_split_mid_literal"]["mood"] == "calm"
    assert by_name["tag_unknown_key"]["mood"] == "neutral"
    # 태그가 아니면 원문이 한 글자도 안 빠진다.
    for name in ("no_tag", "no_tag_bracket_other", "no_tag_prefix_then_diverge"):
        case = by_name[name]
        assert case["body"] == "".join(case["tokens"])
