"""TurnPipeline 배선 검증 — Brain→Mouth 연결과 barge-in(kill switch)을 고정한다.

가짜 Brain/Mouth로 실모델·실오디오 없이 계약만 검증한다(test_voice와 같은 규약).
"""

import asyncio
from collections.abc import AsyncIterator

from navi.brain.base import BrainAdapter
from navi.models import BrainResult, LlmRequest, Message, Usage, VoiceProfile
from navi.mouth.fake import FakeMouth
from navi.pipeline import TurnPipeline

VOICE = VoiceProfile(name="navi", vendor_voice_id="stub")


class _StubConductor:
    """build_request만 흉내 — trigger_text를 그대로 마지막 메시지로 싣는다."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, int, str]] = []

    def build_request(
        self, trigger_text: str, *, user_id: int, session_id: str
    ) -> LlmRequest:
        self.calls.append((trigger_text, user_id, session_id))
        return LlmRequest(
            system="", messages=[Message(role="user", text=trigger_text)], model="stub"
        )


class _FakeBrain(BrainAdapter):
    """토큰을 정해진 대본대로 흘리되, 토큰 사이에 양보해 cancel을 받을 틈을 준다."""

    def __init__(self, tokens: list[str], gap: float = 0.0) -> None:
        super().__init__()
        self._tokens = tokens
        self._gap = gap

    async def generate_stream(self, request: LlmRequest) -> AsyncIterator[str]:
        self.last_result = None
        self._cancelled = False
        emitted: list[str] = []
        for token in self._tokens:
            if self._cancelled:  # barge-in — 다음 토큰 경계에서 멈춘다
                break
            emitted.append(token)
            yield token
            if self._gap:
                await asyncio.sleep(self._gap)
        self.last_result = BrainResult(full_text="".join(emitted), usage=Usage(0, 0))


def _build(tokens: list[str], gap: float = 0.0):
    brain = _FakeBrain(tokens, gap=gap)
    mouth = FakeMouth()
    conductor = _StubConductor()
    pipe = TurnPipeline(brain=brain, mouth=mouth, conductor=conductor, voice=VOICE)
    return pipe, brain, mouth, conductor


# --- run_turn: Brain 토큰이 Mouth로 흐르고 화면 echo로도 나간다 ---


async def test_run_turn_streams_tokens_to_mouth_and_echo():
    pipe, _brain, mouth, conductor = _build(["안", "녕 ", "나비"])
    echoed: list[str] = []
    result = await pipe.run_turn(
        "안녕", user_id=7, session_id="s1", echo=echoed.append
    )
    assert mouth.spoken == ["안녕 나비"]  # 토큰이 Mouth로 합성됨
    assert echoed == ["안", "녕 ", "나비"]  # 동시에 화면 echo로도
    assert mouth.last_voice is VOICE  # 단일 목소리 전달
    assert result is not None and result.full_text == "안녕 나비"  # 기억 적재용 반환
    assert conductor.calls == [("안녕", 7, "s1")]  # 요청 조립에 인자 전달


async def test_run_turn_without_echo_still_synthesizes():
    pipe, _brain, mouth, _conductor = _build(["하나."])
    await pipe.run_turn("x", user_id=1, session_id="s")
    assert mouth.spoken == ["하나."]


# --- on_stage: STAGE 계측(Stage 15) — brain 첫 토큰(TTFT)·tts 진입/종료 ---


async def test_on_stage_emits_brain_ttft_and_tts_span():
    stages: list[tuple] = []
    brain = _FakeBrain(["안", "녕"])
    pipe = TurnPipeline(
        brain=brain,
        mouth=FakeMouth(),
        conductor=_StubConductor(),
        voice=VOICE,
        on_stage=lambda s, p, d: stages.append((s, p, d)),
    )
    await pipe.run_turn("x", user_id=1, session_id="s")
    # 토큰은 speak_stream이 당길 때 흐른다(_tee는 lazy) — brain done은 tts start 뒤
    assert [(s, p) for s, p, _ in stages] == [
        ("brain", "start"),
        ("tts", "start"),
        ("brain", "done"),
        ("tts", "done"),
    ]
    detail = {(s, p): d for s, p, d in stages}
    assert detail[("brain", "done")]["ttft_ms"] >= 0  # 첫 토큰 지연 기록
    assert detail[("tts", "done")]["ms"] >= 0  # 합성+재생 전체 구간


async def test_no_on_stage_is_silent_noop():
    pipe, _brain, mouth, _conductor = _build(["하나."])
    await pipe.run_turn("x", user_id=1, session_id="s")  # 콜백 없이도 동작 불변
    assert mouth.spoken == ["하나."]


# --- interrupt(): barge-in = 재생 하드스톱 + LLM 생성 취소를 동시에 ---


async def test_interrupt_stops_mouth_and_cancels_brain():
    pipe, brain, mouth, _conductor = _build(["하나", "둘", "셋", "넷"], gap=0.01)
    task = asyncio.create_task(
        pipe.run_turn("x", user_id=1, session_id="s")
    )
    await asyncio.sleep(0.015)  # 첫 토큰쯤 흐른 뒤
    pipe.interrupt()  # kill switch
    await task
    assert brain._cancelled  # LLM 생성 취소 신호가 갔다
    assert not mouth.is_playing()  # 재생 즉시 내려감
    assert mouth.spoken[0] != "하나둘셋넷"  # 전부 합성됐다면 중단 실패
