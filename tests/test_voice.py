"""음성 어댑터 계약 검증 — fake 어댑터로 STT/Mouth 계약(4.3·4.8)을 고정한다.

벤더(D2·D3)는 미정이지만 계약은 지금 박는다 (진행원칙 1: 계약부터 고정).
"""

import asyncio

import pytest

from navi.models import AudioChunk, VoiceProfile
from navi.mouth import create_mouth
from navi.mouth.fake import FakeMouth
from navi.stt import create_stt
from navi.stt.fake import FakeStt

VOICE = VoiceProfile(name="navi", vendor_voice_id="stub")


async def _stream(*tokens: str):
    for token in tokens:
        yield token


# --- STT (4.3) ---


async def test_stt_session_feeds_frames_and_finalizes_to_injected_transcript():
    stt = FakeStt()
    stt.next_transcript = "오늘 좀 피곤하네"
    session = await stt.open_stream("ko")
    for _ in range(3):
        await session.feed(AudioChunk(pcm=b"\x00\x00"))
    result = await session.finalize()
    assert result.text == "오늘 좀 피곤하네"
    assert result.lang == "ko"
    assert result.confidence == 1.0
    assert session.frames_fed == 3  # feed가 실제로 흘렀는지


# --- Mouth (4.8) ---


async def test_mouth_synthesizes_full_token_stream():
    mouth = FakeMouth()
    assert not mouth.is_playing()
    await mouth.speak_stream(_stream("안", "녕 ", "나비"), VOICE)
    assert mouth.spoken == ["안녕 나비"]
    assert mouth.last_voice is VOICE
    assert not mouth.is_playing()  # 재생 끝나면 내려간다


async def test_mouth_stop_interrupts_mid_stream():
    mouth = FakeMouth()

    async def slow_stream():
        for token in ["하나", "둘", "셋", "넷"]:
            yield token
            await asyncio.sleep(0.01)

    async def speak():
        await mouth.speak_stream(slow_stream(), VOICE)

    task = asyncio.create_task(speak())
    await asyncio.sleep(0.015)  # 첫 토큰쯤 흐른 뒤
    mouth.stop()  # barge-in
    await task
    assert mouth.spoken[0] != "하나둘셋넷"  # 전부 다 합성됐다면 중단 실패
    assert not mouth.is_playing()


# --- 팩토리: 벤더 종속 금지 + 보류 결정 안내 ---


def test_factories_build_fake_by_default():
    assert isinstance(create_stt(), FakeStt)
    assert isinstance(create_mouth(), FakeMouth)


def test_pending_vendors_raise_with_decision_pointer():
    with pytest.raises(NotImplementedError, match="D2"):
        create_stt("vito")
    with pytest.raises(NotImplementedError, match="D3"):
        create_mouth("supertone")


def test_unknown_vendor_raises_value_error():
    with pytest.raises(ValueError):
        create_stt("nope")
    with pytest.raises(ValueError):
        create_mouth("nope")
