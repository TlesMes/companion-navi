from navi.brain.anthropic import _normalize
from navi.brain.echo import EchoBrain
from navi.models import LlmRequest, Message


def _request(text: str) -> LlmRequest:
    return LlmRequest(system="", messages=[Message("user", text)], model="echo")


async def test_echo_streams_tokens_and_fixes_result():
    brain = EchoBrain()
    tokens = [t async for t in brain.generate_stream(_request("안녕 나비"))]
    assert "".join(tokens) == "(echo) 안녕 나비"
    assert brain.last_result.full_text == "(echo) 안녕 나비"
    assert brain.last_result.usage.input_tokens == 0


async def test_cancel_stops_stream_mid_generation():
    brain = EchoBrain()
    received = []
    async for token in brain.generate_stream(_request("하나 둘 셋 넷")):
        received.append(token)
        brain.cancel()  # 첫 토큰 직후 barge-in 시뮬레이션
    assert len(received) < 5  # "(echo) 하나 둘 셋 넷" = 토큰 5개가 다 오면 실패


def test_anthropic_normalize_merges_same_role_and_forces_user_first():
    out = _normalize(
        [
            Message("assistant", "능동 발화였음"),
            Message("user", "응답1"),
            Message("user", "응답2"),
        ]
    )
    assert out[0].role == "user"  # Anthropic 제약: user로 시작
    roles = [m.role for m in out]
    assert all(a != b for a, b in zip(roles, roles[1:]))  # 교대
    assert any(m.text == "응답1\n응답2" for m in out)  # 연속 user 병합
