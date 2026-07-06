"""문장 경계 정규식(navi.mouth.sentence) — 스트리밍 청킹 규칙 고정.

전각 종결(。！？…)은 뒤 공백 없이도 경계(일본어 문말 관행), 반각(.!?)은
공백/버퍼 끝을 요구해 소수점(3.5)·ASCII 말줄임(...) 중간에서 안 끊긴다.
"""

from navi.mouth.sentence import SENTENCE_END


def _split(text: str) -> list[str]:
    """어댑터 합성 워커와 같은 소비 방식 — match로 앞에서부터 잘라낸다."""
    out, buf = [], text
    while (m := SENTENCE_END.match(buf)) is not None:
        out.append(m.group(0).strip())
        buf = buf[m.end():]
    if buf.strip():
        out.append(buf.strip())
    return out


def test_japanese_sentences_split_without_space():
    # 일본어는 。 뒤에 공백을 두지 않는다 — 그래도 문장마다 잘려야 TTFA가 산다
    assert _split("おはよう。今日もいい天気だね。散歩しよう！") == [
        "おはよう。",
        "今日もいい天気だね。",
        "散歩しよう！",
    ]


def test_fullwidth_question_mark_splits():
    assert _split("元気？うん、元気だよ。") == ["元気？", "うん、元気だよ。"]


def test_halfwidth_with_space_still_splits():
    assert _split("안녕. 잘 잤어?") == ["안녕.", "잘 잤어?"]


def test_decimal_point_not_split():
    assert _split("무게는 3.5키로야. 가볍지?") == ["무게는 3.5키로야.", "가볍지?"]


def test_ellipsis_run_consumed_as_one_boundary():
    # 연속 말줄임(……) 중간에서 끊기지 않고 한 덩어리로 소비된다
    assert _split("그건……좀 그래。") == ["그건……", "좀 그래。"]


def test_ellipsis_trailing_period_absorbed():
    # 한국어 관행: 말줄임표 뒤 마침표(….) — 마침표만 남아 홀로 합성되지 않는다
    assert _split("그래…. 알겠어.") == ["그래….", "알겠어."]


def test_closing_quote_included_after_fullwidth_terminator():
    assert _split('"はい。"わかった。') == ['"はい。"', "わかった。"]
