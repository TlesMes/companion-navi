"""청취축 상태머신 검증 — 마이크 없이 프레임을 주입해 SLEEP↔ACTIVE 전이를 고정한다 (D16·D7).

ListenSession은 frames(주입)·now(주입)로 순수하게 돈다 — FakeWakeWord로 깨우고, 가짜 프레임으로
발화를 만들고, 가짜 시계로 타임아웃을 결정론적으로 본다.
"""

import struct

import pytest

from navi.ear import EventKind, FakeWakeWord, ListenSession, SleepReason
from navi.models import AudioChunk

SR = 16000
FRAME_SAMPLES = 512  # FakeWakeWord 기본 frame_length


def _frame(amplitude: int) -> AudioChunk:
    pcm = struct.pack("<h", amplitude) * FRAME_SAMPLES
    return AudioChunk(pcm=pcm, sample_rate=SR)


SPEECH = _frame(10000)
SILENCE = _frame(0)


async def _aiter(frames: list[AudioChunk]):
    for f in frames:
        yield f


async def _collect(session: ListenSession, frames, *, now=None) -> list:
    kw = {"now": now} if now else {}
    return [ev async for ev in session.run(_aiter(frames), **kw)]


def _session(**kw) -> ListenSession:
    # start=2프레임, endpoint=2프레임, preroll=1프레임 (frame_ms=32 기준 작은 값)
    return ListenSession(
        FakeWakeWord(detect_at=kw.pop("detect_at", 1)),
        vad=None,
        active_timeout_ms=kw.pop("active_timeout_ms", 30000),
        start_speech_ms=kw.pop("start_speech_ms", 64),
        endpoint_silence_ms=kw.pop("endpoint_silence_ms", 64),
        preroll_ms=kw.pop("preroll_ms", 32),
        **kw,
    )


@pytest.mark.asyncio
async def test_stays_asleep_without_wakeword():
    # 호출어가 안 잡히면(detect_at 없음) 발화를 줘도 아무 이벤트 없음 — STT 문이 닫혀 있다
    session = ListenSession(FakeWakeWord(), active_timeout_ms=30000)
    events = await _collect(session, [SPEECH, SILENCE] * 5)
    assert events == []


@pytest.mark.asyncio
async def test_wake_then_utterance():
    # 1번째 프레임에서 깨어남 → 이후 발화가 끝나면 UTTERANCE
    session = _session(detect_at=1)
    frames = [SILENCE] + [SPEECH] * 3 + [SILENCE] * 3  # 첫 프레임=깨움
    events = await _collect(session, frames)
    kinds = [e.kind for e in events]
    assert kinds[0] == EventKind.WAKE
    assert EventKind.UTTERANCE in kinds
    utt_ev = next(e for e in events if e.kind == EventKind.UTTERANCE)
    assert utt_ev.utterance is not None and utt_ev.utterance.duration_ms > 0


@pytest.mark.asyncio
async def test_silence_timeout_returns_to_sleep():
    # 깨어난 뒤 무음만 흐르면 타임아웃 → SLEEP(reason=TIMEOUT)
    clock = {"t": 0.0}

    def now():
        clock["t"] += 10.0  # 프레임마다 10초 — 30초 타임아웃을 금방 넘긴다
        return clock["t"]

    session = _session(detect_at=1, active_timeout_ms=30000)
    frames = [SILENCE] * 6  # 1번째=깨움, 이후 무음
    events = await _collect(session, frames, now=now)
    assert events[0].kind == EventKind.WAKE
    sleeps = [e for e in events if e.kind == EventKind.SLEEP]
    assert sleeps and sleeps[0].reason == SleepReason.TIMEOUT


@pytest.mark.asyncio
async def test_request_sleep_returns_to_sleep_by_command():
    # 검문① 수면명령 시뮬레이션: 깨운 뒤 request_sleep() → 다음 프레임에서 SLEEP(COMMAND)
    session = _session(detect_at=1)
    frames_pre = [SILENCE, SILENCE]  # 깨움 + 1프레임
    # run을 직접 돌리며 중간에 request_sleep 호출
    events = []
    agen = session.run(_aiter([SILENCE, SILENCE, SILENCE]))
    ev = await agen.__anext__()  # WAKE
    events.append(ev)
    session.request_sleep()
    ev = await agen.__anext__()  # SLEEP(COMMAND)
    events.append(ev)
    await agen.aclose()
    assert events[0].kind == EventKind.WAKE
    assert events[1].kind == EventKind.SLEEP
    assert events[1].reason == SleepReason.COMMAND


@pytest.mark.asyncio
async def test_re_wake_after_sleep_full_cycle():
    # 전 사이클: 깨움 → 발화 → 타임아웃 SLEEP → 다시 깨움 → 발화
    clock = {"t": 0.0}

    def now():
        clock["t"] += 0.5
        return clock["t"]

    # detect_at=1과 재깨움을 위해 trigger 콜백 사용: SPEECH 프레임에서 깨운다
    session = ListenSession(
        FakeWakeWord(trigger=lambda c: c.pcm == SPEECH.pcm),
        active_timeout_ms=2000,  # 0.5초/프레임 * 4프레임이면 타임아웃
        start_speech_ms=64,
        endpoint_silence_ms=64,
        preroll_ms=32,
    )
    frames = (
        [SPEECH]              # 깨움 1
        + [SPEECH] * 2 + [SILENCE] * 2  # 발화1 종료
        + [SILENCE] * 6       # 무음 → 타임아웃 SLEEP
        + [SPEECH]            # 깨움 2
        + [SPEECH] * 2 + [SILENCE] * 2  # 발화2 종료
    )
    events = await _collect(session, frames, now=now)
    kinds = [e.kind for e in events]
    assert kinds.count(EventKind.WAKE) == 2
    assert kinds.count(EventKind.UTTERANCE) == 2
    assert any(
        e.kind == EventKind.SLEEP and e.reason == SleepReason.TIMEOUT for e in events
    )


@pytest.mark.asyncio
async def test_frame_ms_derived_from_wakeword():
    # 512샘플 / 16kHz = 32ms
    session = ListenSession(FakeWakeWord(frame_length=512, sample_rate=16000))
    assert session.frame_ms == 32
    assert session.sample_rate == 16000
