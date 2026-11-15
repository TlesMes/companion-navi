from navi.brain.anthropic import _normalize
from navi.brain.echo import EchoBrain
from navi.models import LlmRequest, Message


def _request(text: str) -> LlmRequest:
    return LlmRequest(system="", messages=[Message("user", text)], model="echo")


async def test_echo_streams_tokens_and_fixes_result():
    brain = EchoBrain()
    tokens = [t async for t in brain.generate_stream(_request("안녕 나비"))]
    assert "".join(tokens) == "안녕 나비"  # 들은 말 그대로 (접두사 없음)
    assert brain.last_result.full_text == "안녕 나비"
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
    # strict=False가 의도 — 인접쌍 비교라 두 리스트 길이가 1 다른 게 정상이다
    assert all(a != b for a, b in zip(roles, roles[1:], strict=False))  # 교대
    assert any(m.text == "응답1\n응답2" for m in out)  # 연속 user 병합


# --- LlmRequest.context가 벤더 요청에 실리는 자리 ---


class _FakeAnthropicStream:
    def __init__(self, calls, kwargs):
        calls.append(kwargs)

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    @property
    def text_stream(self):
        async def gen():
            yield "응"

        return gen()

    async def get_final_message(self):
        from types import SimpleNamespace

        return SimpleNamespace(
            content=[SimpleNamespace(type="text", text="응")],
            usage=SimpleNamespace(input_tokens=1, output_tokens=1),
        )


async def _anthropic_system_blocks(context: str) -> list[dict]:
    from types import SimpleNamespace

    from navi.brain.anthropic import AnthropicBrain
    from navi.models import LlmRequest

    calls: list[dict] = []
    brain = AnthropicBrain(api_key="test")
    brain._client = SimpleNamespace(
        messages=SimpleNamespace(stream=lambda **kw: _FakeAnthropicStream(calls, kw))
    )
    request = LlmRequest(
        system="카드", messages=[Message(role="user", text="안녕")], model="m", context=context
    )
    _ = [t async for t in brain.generate_stream(request)]
    return calls[0]["system"]


async def test_anthropic_sends_context_as_its_own_block_after_the_card():
    """카드 → 사실 순서, 각자 캐시 경계 — 사실이 바뀌어도 카드 경계는 그대로 맞는다."""
    blocks = await _anthropic_system_blocks("[기억]\n- 사용자는 개발자다.")

    assert [b["text"] for b in blocks] == ["카드", "[기억]\n- 사용자는 개발자다."]
    assert all(b["cache_control"] == {"type": "ephemeral"} for b in blocks)


async def test_anthropic_sends_no_empty_context_block():
    """빈 text 블록은 API가 거절한다 — 사실이 없는 사용자의 턴이 통째로 죽는다."""
    assert [b["text"] for b in await _anthropic_system_blocks("")] == ["카드"]


async def _gemini_system_instruction(context: str) -> str:
    from types import SimpleNamespace

    from navi.brain.gemini import GeminiBrain
    from navi.models import LlmRequest

    seen: dict = {}

    async def fake_stream(*, model, contents, config):
        seen["system"] = config.system_instruction

        async def gen():
            yield SimpleNamespace(text="응", usage_metadata=None)

        return gen()

    brain = GeminiBrain(api_key="test")
    brain._client = SimpleNamespace(
        aio=SimpleNamespace(models=SimpleNamespace(generate_content_stream=fake_stream))
    )
    request = LlmRequest(
        system="카드", messages=[Message(role="user", text="안녕")], model="m", context=context
    )
    _ = [t async for t in brain.generate_stream(request)]
    return seen["system"]


async def test_gemini_appends_context_after_the_card():
    assert await _gemini_system_instruction("[기억]\n- 사실") == "카드\n\n[기억]\n- 사실"
    assert await _gemini_system_instruction("") == "카드"
