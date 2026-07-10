"""DaemonCore 검증 — 마이크·키 없이 전 사이클을 돈다 (arch 4.11).

프레임은 asyncio.Queue로 한 단계씩 먹여 dispatcher와의 경합을 결정론적으로 만든다
(리스트 주입은 ear_task가 전 프레임을 한 번에 삼켜 request_sleep 타이밍이 어긋난다).
FakeWakeWord로 깨우고, transcribe는 대본(scripted texts)으로 STT를 대신한다.
"""

import asyncio
import os
import struct
import time

import pytest

from navi.bus import Event, EventBus, EventKind
from navi.daemon import DaemonCore, acquire_pidfile, cmd_stop, release_pidfile
from navi.ear import FakeWakeWord, ListenSession
from navi.models import AudioChunk

SR = 16000
FRAME_SAMPLES = 512


def _frame(amplitude: int) -> AudioChunk:
    return AudioChunk(pcm=struct.pack("<h", amplitude) * FRAME_SAMPLES, sample_rate=SR)


SPEECH = _frame(10000)
SILENCE = _frame(0)


async def _wait(pred, timeout=2.0):
    t0 = time.monotonic()
    while not pred():
        assert time.monotonic() - t0 < timeout, "조건 대기 타임아웃"
        await asyncio.sleep(0.01)


def _session() -> ListenSession:
    # test_listening.py와 동일 튜닝: 발화 시작 2프레임·종료 2프레임 (frame_ms=32)
    return ListenSession(
        FakeWakeWord(trigger=lambda c: c.pcm == SPEECH.pcm),
        vad=None,
        active_timeout_ms=60000,
        start_speech_ms=64,
        endpoint_silence_ms=64,
        preroll_ms=32,
    )


async def _frames_from(queue: asyncio.Queue):
    while True:
        f = await queue.get()
        if f is None:
            return
        yield f


def _make_core(texts: list[str], turns: list[str], frame_q: asyncio.Queue, bus: EventBus):
    script = iter(texts)

    async def transcribe(_utt) -> str:
        return next(script)

    async def run_turn(text: str) -> None:
        turns.append(text)

    session = _session()
    return DaemonCore(
        bus=bus,
        transcribe=transcribe,
        run_turn=run_turn,
        session=session,
        frames=_frames_from(frame_q),
        tick_interval=999,
        stop_poll=999,
    )


@pytest.mark.asyncio
async def test_full_cycle_wake_turn_sleep():
    # WAKE → 발화1(일반 대화 → 턴) → 발화2(수면 명령 → 턴 없이 SLEEP 복귀)
    bus = EventBus()
    frame_q: asyncio.Queue = asyncio.Queue()
    turns: list[str] = []
    core = _make_core(["안녕 나비야", "이제 그만 잘게"], turns, frame_q, bus)
    observer = bus.subscribe("observer", maxsize=256)
    task = asyncio.create_task(core.run())

    frame_q.put_nowait(SPEECH)  # 깨움
    await _wait(lambda: core.state.listening_mode == "active")

    for f in [SPEECH, SPEECH, SILENCE, SILENCE]:  # 발화1 → UTTERANCE
        frame_q.put_nowait(f)
    await _wait(lambda: turns == ["안녕 나비야"])
    assert core.state.turns_count == 1

    for f in [SPEECH, SPEECH, SILENCE, SILENCE]:  # 발화2 = 수면 명령
        frame_q.put_nowait(f)
    # dispatcher가 검문①을 처리해 request_sleep을 걸 때까지 기다린 뒤 다음 프레임을 준다
    # (프레임을 먼저 주면 ear가 플래그 반영 전에 소비해 버려 비결정적이 된다)
    await _wait(lambda: core._session._sleep_requested)  # noqa: SLF001 — 타이밍 고정용
    frame_q.put_nowait(SILENCE)  # request_sleep 반영은 다음 프레임에서
    await _wait(lambda: core.state.listening_mode == "sleep")
    assert turns == ["안녕 나비야"]  # 수면 명령은 LLM(턴)으로 가지 않는다 — 결정론 게이트

    bus.publish(Event(EventKind.SHUTDOWN, time.monotonic()))
    await asyncio.wait_for(task, timeout=2)

    kinds = []
    while not observer.empty():
        kinds.append(observer.get_nowait().kind)
    assert EventKind.WAKE in kinds
    assert EventKind.TURN_STARTED in kinds and EventKind.TURN_ENDED in kinds
    assert EventKind.SLEEP in kinds


@pytest.mark.asyncio
async def test_tick_published_periodically():
    bus = EventBus()
    observer = bus.subscribe("observer", maxsize=256)
    core = DaemonCore(
        bus=bus,
        transcribe=None,
        run_turn=None,
        tick_interval=0.01,
        stop_poll=999,
    )
    task = asyncio.create_task(core.run())
    await _wait(lambda: observer.qsize() >= 3)  # 이 시나리오의 발행은 TICK뿐
    bus.publish(Event(EventKind.SHUTDOWN, time.monotonic()))
    await asyncio.wait_for(task, timeout=2)


@pytest.mark.asyncio
async def test_stop_sentinel_triggers_shutdown():
    bus = EventBus()
    flag = {"stop": False}
    core = DaemonCore(
        bus=bus,
        transcribe=None,
        run_turn=None,
        tick_interval=999,
        stop_requested=lambda: flag["stop"],
        stop_poll=0.01,
    )
    task = asyncio.create_task(core.run())
    await asyncio.sleep(0.05)
    assert not task.done()
    flag["stop"] = True
    await asyncio.wait_for(task, timeout=2)  # SHUTDOWN 발행 → dispatcher 종료


def test_pid_alive_reality_check():
    from navi.daemon import _pid_alive

    assert _pid_alive(os.getpid()) is True
    assert _pid_alive(999999999) is False


def test_pidfile_single_instance_guard(tmp_path, monkeypatch):
    pid_file = tmp_path / "navi.pid"
    # 다른 살아있는 데몬(pid 12345 생존 가정)이 있으면 기동 거부
    pid_file.write_text("12345")
    monkeypatch.setattr("navi.daemon._pid_alive", lambda pid: True)
    assert acquire_pidfile(pid_file) is False
    # 죽은 pid면 덮어쓰고 기동 허용
    monkeypatch.setattr("navi.daemon._pid_alive", lambda pid: False)
    assert acquire_pidfile(pid_file) is True
    assert pid_file.read_text() == str(os.getpid())
    release_pidfile(pid_file, tmp_path / "navi.stop")
    assert not pid_file.exists()


def test_cmd_stop_without_daemon(tmp_path, capsys):
    # 떠 있는 데몬이 없으면 1 리턴 + 잔재 정리
    pid_file = tmp_path / "navi.pid"
    stop_file = tmp_path / "navi.stop"
    pid_file.write_text("999999999")
    assert cmd_stop(pid_file, stop_file) == 1
    assert not pid_file.exists()
    assert "떠 있지 않습니다" in capsys.readouterr().out


# ── Stage 14: 능동축 모드 배선 ──

from datetime import datetime, time as dtime  # noqa: E402

from navi.heartbeat import ModeMachine, SleepWindow  # noqa: E402

WINDOW = SleepWindow(start=dtime(23, 0), end=dtime(7, 0))


class _Clock:
    def __init__(self, at: datetime):
        self.at = at

    def __call__(self) -> datetime:
        return self.at


def _mode_core(clock: _Clock, bus: EventBus, persisted: list, **kw):
    machine = ModeMachine(WINDOW, 30, now=clock)
    core = DaemonCore(
        bus=bus,
        transcribe=kw.pop("transcribe", None),
        run_turn=kw.pop("run_turn", None),
        tick_interval=kw.pop("tick_interval", 999),
        stop_poll=999,
        mode_machine=machine,
        persist_mode=lambda mode, until: persisted.append((mode, until)),
        **kw,
    )
    return core, machine


@pytest.mark.asyncio
async def test_tick_drives_time_transition_and_persists():
    # 시계가 취침창에 들어가면 TICK이 SLEEP 전이를 굴리고, MODE_CHANGED·영속화가 따라온다
    bus = EventBus()
    observer = bus.subscribe("observer", maxsize=256)
    clock = _Clock(datetime(2026, 7, 9, 22, 59))
    persisted: list = []
    core, _ = _mode_core(clock, bus, persisted, tick_interval=0.01)
    task = asyncio.create_task(core.run())

    await _wait(lambda: core.state.snapshot()["uptime_s"] >= 0)  # 기동 대기
    assert core.state.proactive_mode == "active"

    clock.at = datetime(2026, 7, 9, 23, 0)  # 취침창 진입
    await _wait(lambda: core.state.proactive_mode == "sleep")
    # 창SLEEP은 시계에서 파생 — 저장(export)엔 오버라이드 근원(active/None)이 남는다
    assert persisted and persisted[-1] == ("active", None)

    clock.at = datetime(2026, 7, 10, 7, 0)  # 기상
    await _wait(lambda: core.state.proactive_mode == "active")

    bus.publish(Event(EventKind.SHUTDOWN, time.monotonic()))
    await asyncio.wait_for(task, timeout=2)
    kinds = []
    while not observer.empty():
        kinds.append(observer.get_nowait().kind)
    assert kinds.count(EventKind.MODE_CHANGED) == 2  # SLEEP 진입 + ACTIVE 복귀


@pytest.mark.asyncio
async def test_snooze_command_via_gate_sets_mode_without_turn():
    # "나 조금만 더 잘래" → 검문①이 가로채 SNOOZE 전이, LLM(턴)으로는 안 간다
    bus = EventBus()
    frame_q: asyncio.Queue = asyncio.Queue()
    turns: list[str] = []
    persisted: list = []
    script = iter(["나 조금만 더 잘래"])

    async def transcribe(_utt) -> str:
        return next(script)

    async def run_turn(text: str) -> None:
        turns.append(text)

    clock = _Clock(datetime(2026, 7, 10, 7, 5))  # 창 밖(기상 직후) — 스누즈의 전형
    core, _ = _mode_core(
        clock,
        bus,
        persisted,
        transcribe=transcribe,
        run_turn=run_turn,
        session=_session(),
        frames=_frames_from(frame_q),
    )
    task = asyncio.create_task(core.run())

    frame_q.put_nowait(SPEECH)  # 깨움
    await _wait(lambda: core.state.listening_mode == "active")
    for f in [SPEECH, SPEECH, SILENCE, SILENCE]:  # 발화 = 스누즈 명령
        frame_q.put_nowait(f)
    await _wait(lambda: core.state.proactive_mode == "snooze")

    assert turns == []  # 결정론 게이트 — LLM 미경유
    assert persisted[-1][0] == "snooze"
    assert core.state.snapshot()["can_speak"] is False  # 검문② — 선제 발화 금지

    bus.publish(Event(EventKind.SHUTDOWN, time.monotonic()))
    await asyncio.wait_for(task, timeout=2)


def test_snapshot_exposes_proactive_mode_for_gui():
    # 3단계 GUI GET /status 재료 — 두 축이 나란히 실린다 (D16)
    from navi.daemon import DaemonState

    snap = DaemonState(started_at=time.monotonic()).snapshot()
    assert snap["proactive_mode"] == "active"
    assert snap["can_speak"] is True
    assert "listening_mode" in snap
