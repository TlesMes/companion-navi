"""데몬 코어 — 상주 프로세스로 귀·시계·대화를 이벤트 버스 위에서 돌린다 (arch 4.11).

실행: python -m navi.daemon [--voice --wakeword ...]   종료: Ctrl+C 또는 `python -m navi.daemon stop`

CLI(navi/cli.py)가 "켜서 대화하고 끄는" 단발 세션이라면, 데몬은 한 번 띄우면 상주한다 —
잘 때는 호출어만 기다리고, 시계(TICK)가 흘러 이후 Heartbeat(선제 발화)·모드 판정의 원료가 된다.
구조는 발행자(ear_task·tick_task)와 구독자(dispatcher·console)로 갈라져 있어 Phase 3의
나머지(모드 상태머신·GUI·Heartbeat)가 구독자 추가만으로 붙는다.

원칙: "언제"는 결정론 — 이 파일의 루프·tick·종료 판정에 LLM은 없다. 무엇을 말할지만
dispatcher가 TurnPipeline로 넘긴다. cli.py는 개발 도구로 보존하고, 여기는 같은 부품
(Brain/Mouth/Conductor/TurnPipeline/ListenSession)을 새로 조립만 한다.

생명주기(Windows, systemd 없음): 시작 시 logs/navi.pid로 단일 인스턴스 가드. 종료는
Ctrl+C, stop 서브커맨드의 센티널 파일(logs/navi.stop — Windows 프로세스 간 시그널 불안정),
또는 컨트롤 플레인 POST /shutdown(Stage 15, navi/control/). GUI 관찰·제어는 컨트롤 플레인이
같은 루프의 태스크로 담당한다 — 서버가 죽어도 데몬 본체는 산다.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import random
import signal
import sys
import time
import uuid
from collections import deque
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING

from navi.bus import Event, EventBus, EventKind
from navi.cli import _build_wakeword, _setup_logging, _transcribe_utterance
from navi.ear import EventKind as ListenKind
from navi.ear import ListenSession, SleepReason
from navi.gatekeeper import GateResult, check_gate
from navi.heartbeat import (
    Mode,
    ModeCommand,
    ModeMachine,
    pick_topic,
    should_initiate,
    time_of_day,
)
from navi.models import AudioChunk

if TYPE_CHECKING:
    from navi.config import ProactiveConfig

log = logging.getLogger("navi.daemon")

# 절대경로로 고정 — 임포트 시점(cwd=프로젝트, chdir 이전)에 resolve한다. gptsovits
# warmup이 os.chdir(repo)를 하므로(gptsovits.py) 상대경로면 tick의 STOP_FILE.exists·
# release_pidfile이 chdir된 엉뚱한 디렉토리를 가리켜 stop 무효·orphan을 낳았다. cmd_stop은
# 별도 프로세스(chdir 없음)라 같은 import resolve로 동일 절대경로를 얻는다.
PID_FILE = (Path("logs") / "navi.pid").resolve()
STOP_FILE = (Path("logs") / "navi.stop").resolve()

_LISTEN_TO_BUS = {
    ListenKind.WAKE: EventKind.WAKE,
    ListenKind.UTTERANCE: EventKind.UTTERANCE,
    ListenKind.SLEEP: EventKind.SLEEP,
}

# 검문① 결과 → 능동축 명령 (Stage 14). SLEEP은 청취축 처리와 겸해 별도 분기.
_GATE_TO_COMMAND = {
    GateResult.WAKE: ModeCommand.WAKE,
    GateResult.SNOOZE: ModeCommand.SNOOZE,
    GateResult.DND: ModeCommand.DND,
    GateResult.DND_CLEAR: ModeCommand.DND_CLEAR,
}

# 명령 접수 안내 — LLM 미경유 결정론 응답이라 문구도 고정이다.
_GATE_ACK = {
    GateResult.WAKE: "[좋은 아침 — 이제 먼저 말 걸 수 있어요]",
    GateResult.SNOOZE: "[알았어요 — 조금 있다 다시 올게요]",
    GateResult.DND: "[방해하지 않을게요 — 「이제 말 걸어도 돼」로 해제]",
    GateResult.DND_CLEAR: "[해제했어요 — 다시 말 걸 수 있어요]",
}


@dataclass
class DaemonState:
    """데몬의 현재 상태 스냅샷 — 3단계 GUI의 GET /status가 이걸 직렬화하면 끝."""

    started_at: float
    listening_mode: str = "sleep"  # sleep | active — 청취축(마이크, D16)
    proactive_mode: str = "active"  # sleep | active | dnd | snooze — 능동축(먼저 말 걸기)
    turns_count: int = 0
    last_events: deque[Event] = field(default_factory=lambda: deque(maxlen=50))

    def record(self, event: Event) -> None:
        self.last_events.append(event)
        if event.kind == EventKind.WAKE:
            self.listening_mode = "active"
        elif event.kind == EventKind.SLEEP:
            self.listening_mode = "sleep"

    def snapshot(self, *, now: Callable[[], float] = time.monotonic) -> dict:
        return {
            "listening_mode": self.listening_mode,
            "proactive_mode": self.proactive_mode,
            "can_speak": self.proactive_mode == Mode.ACTIVE.value,  # 검문② 요약
            "uptime_s": round(now() - self.started_at, 1),
            "turns_count": self.turns_count,
            "last_events": [e.kind.name for e in self.last_events],
        }


class DaemonCore:
    """발행자 태스크들 + dispatcher를 돌리는 오케스트레이터.

    부품은 전부 주입받는다 — frames·시계·transcribe·run_turn을 가짜로 갈아끼우면
    마이크·키 없이 전 사이클이 유닛 테스트가 된다(ListenSession과 동일 규약).
    """

    def __init__(
        self,
        *,
        bus: EventBus,
        transcribe: Callable[..., Awaitable[str]],  # (Utterance) -> 텍스트
        run_turn: Callable[[str], Awaitable[None]],
        session: ListenSession | None = None,
        frames: AsyncIterator[AudioChunk] | None = None,
        tick_interval: float = 10.0,
        stop_requested: Callable[[], bool] = lambda: False,
        stop_poll: float = 1.0,
        now: Callable[[], float] = time.monotonic,
        mode_machine: ModeMachine | None = None,
        persist_mode: Callable[[str, str | None], None] | None = None,
        # ── 능동성 2·3층 (Phase 3 순서 4) — 전부 주입, 미주입이면 선제 발화 비활성 ──
        run_initiation: Callable[[str], Awaitable[None]] | None = None,
        proactive: ProactiveConfig | None = None,
        wall_now: Callable[[], datetime] = datetime.now,
        log_interaction: Callable[[str, str | None, str | None], None] | None = None,
        count_initiations_today: Callable[[], int] | None = None,
        memory_snapshot: Callable[[], list] | None = None,
        response_window_s: float = 300.0,
        rng: random.Random | None = None,  # 2층 hazard 주사위 — 테스트에서 seed 고정
    ) -> None:
        self._bus = bus
        self._transcribe = transcribe
        self._run_turn = run_turn
        self._session = session
        self._frames = frames
        self._tick_interval = tick_interval
        self._stop_requested = stop_requested
        self._stop_poll = stop_poll
        self._now = now
        self._machine = mode_machine
        self._persist_mode = persist_mode
        self._run_initiation = run_initiation
        self._proactive = proactive
        self._wall_now = wall_now
        self._log_interaction = log_interaction
        self._count_initiations_today = count_initiations_today
        self._memory_snapshot = memory_snapshot
        self._response_window_s = response_window_s
        self._rng = rng or random.Random()
        # 마지막 상호작용 시각(벽시계) — 타이밍 2층의 기준. 기동 시각으로 시작해
        # base_interval이 지나기 전엔 콜드 오픈하지 않는다.
        self._last_interaction_at = wall_now()
        # 응답 대기 중인 능동 발화 시각 — 응답/무시/오버라이드 판정용(None=대기 없음).
        self._pending_initiation: datetime | None = None
        self.state = DaemonState(started_at=now())
        if mode_machine is not None:
            self.state.proactive_mode = mode_machine.current_mode().value

    async def run(self) -> None:
        """SHUTDOWN 이벤트까지 상주. 발행자 태스크들은 종료 시 전부 취소한다."""
        core_q = self._bus.subscribe("core", maxsize=256)
        tasks = [
            asyncio.create_task(self._tick_loop(), name="tick"),
            asyncio.create_task(self._stop_watch(), name="stop_watcher"),
        ]
        if self._session is not None and self._frames is not None:
            tasks.append(asyncio.create_task(self._ear_loop(), name="ear"))
        try:
            await self._dispatch(core_q)
        finally:
            for t in tasks:
                t.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            self._bus.unsubscribe("core")

    # ── 발행자들 ──

    async def _ear_loop(self) -> None:
        """청취축(ListenSession) 이벤트를 버스 Event로 감싸 발행 — 판정은 하지 않는다."""
        async for lev in self._session.run(self._frames):
            payload = lev.utterance if lev.kind == ListenKind.UTTERANCE else lev.reason
            self._bus.publish(Event(_LISTEN_TO_BUS[lev.kind], self._now(), payload))

    async def _tick_loop(self) -> None:
        """순수 시계 이벤트 — Heartbeat(4단계)·모드 판정(2단계)이 나중에 구독한다."""
        while True:
            await asyncio.sleep(self._tick_interval)
            self._bus.publish(Event(EventKind.TICK, self._now()))

    async def _stop_watch(self) -> None:
        while True:
            await asyncio.sleep(self._stop_poll)
            if self._stop_requested():
                log.info("종료 신호 감지 — SHUTDOWN 발행")
                self._bus.publish(Event(EventKind.SHUTDOWN, self._now()))
                return

    # ── 구독자(core) ──

    async def _dispatch(self, queue: asyncio.Queue[Event]) -> None:
        while True:
            event = await queue.get()
            self.state.record(event)
            if event.kind == EventKind.SHUTDOWN:
                return
            if event.kind == EventKind.UTTERANCE:
                await self._handle_utterance(event.payload)
            elif event.kind == EventKind.TICK:
                if self._machine is not None:
                    self._apply_mode(self._machine.tick())  # 시간 전이(창 진입·만료)
                await self._maybe_initiate()  # 능동성 2·3층 — 게이트 통과 시에만
                log.debug("tick — %s", self.state.snapshot(now=self._now))

    def command_mode(self, cmd: ModeCommand) -> Mode:
        """능동축 명령의 단일 진입점 — 음성(검문①)과 GUI 버튼(컨트롤 플레인)이 공유.

        명령 경로는 겉모드 무변화여도 오버라이드가 생길 수 있어 항상 영속화한다.
        상태머신이 구성 안 됐으면 RuntimeError — 호출자(서버는 503)가 처리한다.
        """
        if self._machine is None:
            raise RuntimeError("모드 상태머신이 구성되지 않았습니다")
        mode = self._machine.command(cmd)
        self._apply_mode(mode, force_persist=True)
        return mode

    def sleep_window(self):
        """현재 취침창 — 상태머신 미구성이면 None. GET /status가 GUI 스트립에 싣는다."""
        return self._machine.window if self._machine is not None else None

    def set_sleep_window(self, window) -> Mode:
        """취침창 런타임 변경(Stage 14 예고) — 변경 즉시 시간 전이를 재평가한다."""
        if self._machine is None:
            raise RuntimeError("모드 상태머신이 구성되지 않았습니다")
        self._machine.set_window(window)
        mode = self._machine.tick()
        self._apply_mode(mode)
        return mode

    def _apply_mode(self, mode: Mode, *, force_persist: bool = False) -> None:
        """능동축 모드를 스냅샷에 반영 — 바뀌었을 때만 MODE_CHANGED 발행.

        영속화 대상은 겉모드가 아니라 저장 상태(오버라이드 근원 — 창SLEEP은 시계에서
        파생이라 저장 안 됨). 명령 경로는 겉모드가 안 바뀌어도 오버라이드가 생길 수
        있어(예: 창 안 스누즈) force_persist로 항상 저장한다.
        """
        old = self.state.proactive_mode
        changed = mode.value != old
        if changed:
            self.state.proactive_mode = mode.value
            log.info("능동축 %s → %s", old, mode.value)
            self._bus.publish(
                Event(EventKind.MODE_CHANGED, self._now(), (old, mode.value))
            )
        if (changed or force_persist) and self._persist_mode is not None:
            self._persist_mode(*self._machine.export_state())

    def _stage(self, stage: str, phase: str, detail: dict | None = None) -> None:
        """STAGE 계측 발행(Stage 15) — GUI 노드 점등 재료 + 구간별 지연 상시 기록."""
        self._bus.publish(Event(EventKind.STAGE, self._now(), (stage, phase, detail)))

    # ── 능동성 2·3층 (arch 4.11 tick 배선) ──

    async def _maybe_initiate(self) -> None:
        """TICK마다 "지금 먼저 말 걸까"를 판정하고, 그렇다면 주제→발화까지 굴린다.

        게이트 순서(전부 통과해야 발화): ①현재 모드 ACTIVE(검문②, 1층 mode.py) →
        ②daily_cap 미만 → ③should_initiate(2층 timing.py). 하나라도 막히면 침묵.
        검문①(오버라이드)·검문②(취침창)는 여기서 건드리지 않는다 — 이미 통과한 뒤다.
        """
        if self._run_initiation is None or self._proactive is None or self._machine is None:
            return  # 능동성 미배선(텍스트 유닛 등) — 조용히 통과
        now = self._wall_now()
        self._settle_pending(now)  # 지난 발화가 응답 창을 넘겼으면 먼저 무시로 마감
        if self._machine.current_mode() is not Mode.ACTIVE:
            return  # 검문② — 취침창·DND·SNOOZE면 선제 발화 금지
        if (
            self._count_initiations_today is not None
            and self._count_initiations_today() >= self._proactive.daily_cap
        ):
            return  # 하루 상한 — 원가·피로 방지
        if not should_initiate(
            now,
            self._last_interaction_at,
            self._proactive.time_weights,
            base_interval_s=self._proactive.base_interval_s,
            min_gap_s=self._proactive.min_gap_s,
            tick_interval_s=self._tick_interval,
            shape_k=self._proactive.hazard_shape_k,
            rng=self._rng,
        ):
            return
        snapshot = self._memory_snapshot() if self._memory_snapshot is not None else None
        topic = pick_topic(snapshot, None, time_of_day(now), [])
        if topic is None:
            return  # 3층이 걸 게 없다고 판단
        mode_val = self._machine.current_mode().value
        log.info("능동 발화 — %s", topic)
        if self._log_interaction is not None:
            self._log_interaction("initiated", mode_val, topic)
        self._last_interaction_at = now
        self._pending_initiation = now
        self._bus.publish(Event(EventKind.TURN_STARTED, self._now(), topic))
        try:
            await self._run_initiation(topic)
            self.state.turns_count += 1
        finally:
            self._bus.publish(Event(EventKind.TURN_ENDED, self._now(), topic))

    def _settle_pending(self, now: datetime) -> None:
        """응답 창(response_window)을 넘긴 능동 발화를 '무시됨'으로 마감한다.

        판정 규칙은 문서에 규정이 없어 여기서 정한다: 먼저 건 뒤 response_window_s
        안에 사용자 발화(응답/오버라이드)가 없으면 user_ignored. 창 값은 배선용
        기본(300s)이고, 응답률/무시율이 쌓이면 실제 값을 튜닝한다(진행 원칙 2).
        """
        if self._pending_initiation is None:
            return
        if (now - self._pending_initiation).total_seconds() >= self._response_window_s:
            self._resolve_pending("user_ignored")

    def _resolve_pending(self, event: str) -> None:
        """대기 중인 능동 발화의 결말(responded/overrode/ignored)을 로그로 남기고 비운다."""
        if self._pending_initiation is None:
            return
        mode_val = self._machine.current_mode().value if self._machine is not None else None
        if self._log_interaction is not None:
            self._log_interaction(event, mode_val, None)
        self._pending_initiation = None

    async def _handle_utterance(self, utterance) -> None:
        """UTTERANCE → STT → 검문①(결정론) → 통과 시 한 턴. cli의 루프 몸통과 같은 순서."""
        self._stage("stt", "start")
        stt_t0 = time.perf_counter()
        text = await self._transcribe(utterance)
        self._stage("stt", "done", {"ms": round((time.perf_counter() - stt_t0) * 1000)})
        if not text:
            print("[인식 결과 없음 — 다시 말하세요]")
            return
        print(f"나> {text}")
        # 사용자가 말했다 = 상호작용 — 타이밍 기준을 지금으로 리셋(연달아 안 건다).
        self._last_interaction_at = self._wall_now()
        gate = check_gate(text)
        self._stage("gate", "done", {"result": gate.name})  # 게이트는 즉답 — done만
        if gate == GateResult.SLEEP:
            # 두 축을 함께 재운다 — 청취축은 세션 종료, 능동축은 다음 기상까지 SLEEP
            log.info("검문① SLEEP — %r", text)
            # 방금 먼저 건 말에 "잘게"로 응했으면 오버라이드(거절)로 마감
            self._resolve_pending("user_overrode")
            if self._session is not None:
                self._session.request_sleep()
            if self._machine is not None:
                self.command_mode(ModeCommand.SLEEP)
            return
        if gate != GateResult.PASS:
            # 능동축 명령(Stage 14) — LLM 미경유, 상태머신 없으면(구성 안 됨) 무시 안내
            log.info("검문① %s — %r", gate.name, text)
            # "더 잘래"·"조용히 해" 등도 방금 발화에 대한 오버라이드로 본다
            self._resolve_pending("user_overrode")
            if self._machine is None:
                print("[모드 상태머신이 꺼져 있어 무시합니다]")
                return
            self.command_mode(_GATE_TO_COMMAND[gate])
            print(_GATE_ACK[gate], flush=True)
            return
        # 실제 대화로 응답 — 방금 먼저 건 말에 사용자가 대꾸했다
        self._resolve_pending("user_responded")
        self._bus.publish(Event(EventKind.TURN_STARTED, self._now(), text))
        try:
            await self._run_turn(text)
            self.state.turns_count += 1
        finally:
            self._bus.publish(Event(EventKind.TURN_ENDED, self._now(), text))


# ── 생명주기: pid 가드 · stop 센티널 (Windows에 시그널 대신 파일) ──


def _pid_alive(pid: int) -> bool:
    """PID 생존 확인. Windows의 os.kill은 시그널 0도 TerminateProcess를 부르므로 금지."""
    if sys.platform == "win32":
        import ctypes

        PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
        STILL_ACTIVE = 259
        kernel32 = ctypes.windll.kernel32
        handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
        if not handle:
            return False
        code = ctypes.c_ulong()
        ok = kernel32.GetExitCodeProcess(handle, ctypes.byref(code))
        kernel32.CloseHandle(handle)
        return bool(ok) and code.value == STILL_ACTIVE
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def _read_pid(path: Path) -> int | None:
    try:
        return int(path.read_text().strip())
    except (OSError, ValueError):
        return None


def acquire_pidfile(path: Path = PID_FILE) -> bool:
    """단일 인스턴스 가드 — 살아있는 데몬이 이미 있으면 False. 죽은 pid는 덮어쓴다."""
    pid = _read_pid(path)
    if pid is not None and pid != os.getpid() and _pid_alive(pid):
        return False
    path.parent.mkdir(exist_ok=True)
    path.write_text(str(os.getpid()))
    return True


def release_pidfile(path: Path = PID_FILE, stop_path: Path = STOP_FILE) -> None:
    path.unlink(missing_ok=True)
    stop_path.unlink(missing_ok=True)


def cmd_stop(path: Path = PID_FILE, stop_path: Path = STOP_FILE) -> int:
    """stop 서브커맨드 — 센티널 파일을 만들고 데몬이 내려갈 때까지 잠깐 기다린다."""
    pid = _read_pid(path)
    if pid is None or not _pid_alive(pid):
        print("데몬이 떠 있지 않습니다")
        release_pidfile(path, stop_path)  # 죽은 pid 잔재 정리
        return 1
    stop_path.parent.mkdir(exist_ok=True)
    stop_path.touch()
    print(f"종료 요청 — PID {pid}가 내려가길 기다립니다…")
    for _ in range(30):  # 최대 ~15초
        time.sleep(0.5)
        if not _pid_alive(pid):
            print("데몬 종료 확인")
            return 0
    print("아직 종료 중 — logs/navi.log 확인")
    return 0


# ── 조립부: cli.chat()의 배선을 미러링 (공통화 리팩터링은 다음 PR) ──


async def _run(config, args) -> None:
    from navi.brain import create_brain
    from navi.conductor import Conductor
    from navi.memory import MemoryStore
    from navi.persona import CharacterCard, mouth_options
    from navi.pipeline import TurnPipeline
    from navi.schedule import ConfigSchedule

    store = MemoryStore(config.db_path)
    # root= 로 voice 섹션의 상대경로(wav·ckpt)를 지금 절대화 — gptsovits 웜업이
    # os.chdir를 하므로 지연 해석은 깨진다(persona/voice.py).
    card = CharacterCard.load(config.persona_card_path, root=config.root)
    brain = create_brain(config)
    conductor = Conductor(card=card, memory=store, config=config)
    user_id = store.ensure_user(display_name="친구")
    session_id = uuid.uuid4().hex
    bus = EventBus()

    # Ctrl+C(SIGINT)·Ctrl+Break(SIGBREAK)를 KeyboardInterrupt 대신 SHUTDOWN 발행으로.
    # 기본 동작은 런너가 전 태스크를 일괄 취소해 uvicorn WS·lifespan이 CancelledError
    # 소음(ERROR 로그 2건)을 남긴다 — stop 커맨드·POST /shutdown과 같은 정상 종료
    # 경로로 합류시키면 조용하다. 두 번째 Ctrl+C는 기본 핸들러 복원(강제 탈출구).
    loop = asyncio.get_running_loop()

    def _graceful_interrupt(signum, frame) -> None:
        signal.signal(signal.SIGINT, signal.default_int_handler)
        loop.call_soon_threadsafe(
            bus.publish, Event(EventKind.SHUTDOWN, time.monotonic())
        )

    signal.signal(signal.SIGINT, _graceful_interrupt)
    if hasattr(signal, "SIGBREAK"):  # Windows 전용 — Ctrl+Break·콘솔 닫기
        signal.signal(signal.SIGBREAK, _graceful_interrupt)

    # 능동축 상태머신(Stage 14) — 재기동 시 mode_state 복원(만료 오버라이드는 tick이 정리)
    schedule = ConfigSchedule(config.mode.sleep_start, config.mode.sleep_end)
    machine = ModeMachine(schedule.get_sleep_window(), config.mode.snooze_minutes)
    saved = store.get_mode_state(user_id)
    if saved is not None:
        machine.restore_state(*saved)
    log.info("능동축 모드 — %s (복원=%s)", machine.tick().value, saved is not None)
    log.info("데몬 시작 — session=%s, vendor=%s, pid=%d", session_id, config.brain.vendor, os.getpid())

    # 페르소나 번들(gui.md PR ②) — 활성 벤더의 목소리 섹션. 부팅도 번들 우선:
    # 카드에 섹션이 있으면 가중치·초기 톤을 카드에서, 없으면 config mouth 폴백(하위호환).
    vendor_voice = card.voice.vendor(config.mouth.vendor) if card.voice else None

    pipeline: TurnPipeline | None = None
    if args.voice:
        from navi.mouth import create_mouth

        # 벤더 경계는 persona.mouth_options가 지킨다 — 가중치 kwarg는 그걸 받는
        # 벤더에만 간다(예전엔 무조건 주입해 SupertonicMouth(gpt_ckpt=…)로 죽었다).
        options = mouth_options(config.mouth.vendor, config.mouth.options, vendor_voice)
        initial_voice = config.mouth.voice
        default_tone = card.voice.default_tone(config.mouth.vendor) if card.voice else None
        if default_tone is not None:
            initial_voice = card.voice.profile(default_tone)
        mouth = create_mouth(config.mouth.vendor, **options)
        print(f"[TTS 엔진 로딩 중… {config.mouth.vendor}]", flush=True)
        await asyncio.to_thread(mouth.warmup)
        pipeline = TurnPipeline(
            brain=brain,
            mouth=mouth,
            conductor=conductor,
            voice=initial_voice,
            # 파이프라인은 버스를 모른다 — STAGE 발행은 여기서 연결(Stage 15)
            on_stage=lambda stage, phase, detail: bus.publish(
                Event(EventKind.STAGE, time.monotonic(), (stage, phase, detail))
            ),
        )

    # 페르소나·톤 교체 파사드(Stage 15-②) — 텍스트 모드(pipeline=None)에서도
    # 카드 교체는 되므로 항상 만든다. 컨트롤 플레인과 run_turn 프롬프트가 쓴다.
    from navi.control.runtime import SwapRuntime

    swap = SwapRuntime(
        conductor=conductor,
        pipeline=pipeline,
        personas_dir=config.persona_card_path.parent,
        root=config.root,
        vendor=config.mouth.vendor,
        persona_id=config.persona_card_path.stem,
        loaded_ckpts=(
            vendor_voice.ckpts
            if vendor_voice is not None
            else (
                config.mouth.options.get("gpt_ckpt", ""),
                config.mouth.options.get("sovits_ckpt", ""),
            )
        ),
    )

    async def run_turn(text: str) -> None:
        started = time.perf_counter()
        # 캐릭터명은 파사드에서 동적으로 — 페르소나 교체 후에도 새 이름으로 찍힌다
        print(f"{swap.character}> ", end="", flush=True)

        def _echo(token: str) -> None:
            print(token, end="", flush=True)

        try:
            if pipeline is not None:
                result = await pipeline.run_turn(
                    text, user_id=user_id, session_id=session_id, echo=_echo
                )
            else:
                request = conductor.build_request(
                    text, user_id=user_id, session_id=session_id
                )
                async for token in brain.generate_stream(request):
                    _echo(token)
                result = brain.last_result
            print()
        except Exception:
            print()
            log.exception("두뇌 호출 실패 — 이 턴은 기억에 남기지 않는다")
            print("(…말이 끊겼다. logs/navi.log 참고)")
            return
        if result is None:
            return
        store.append_turn(session_id, user_id, role="user", text=text)
        store.append_turn(session_id, user_id, role="assistant", text=result.full_text)
        store.log_usage("llm", result.usage)
        log.info("응답 완료 — %d자, 총 %.0fms", len(result.full_text), (time.perf_counter() - started) * 1000)

    async def run_initiation(topic: str) -> None:
        """능동 발화 — 나비가 먼저 건다. run_turn과 달리 사용자 발화가 없다:

        topic 힌트는 트리거(LLM 프롬프트)일 뿐 사용자 말이 아니므로 기억엔 나비의
        답변만 trigger_type=proactive로 남긴다(user 턴을 지어내지 않는다).
        """
        print(f"\n{swap.character}> ", end="", flush=True)

        def _echo(token: str) -> None:
            print(token, end="", flush=True)

        try:
            if pipeline is not None:
                result = await pipeline.run_turn(
                    topic, user_id=user_id, session_id=session_id, echo=_echo
                )
            else:
                request = conductor.build_request(
                    topic, user_id=user_id, session_id=session_id
                )
                async for token in brain.generate_stream(request):
                    _echo(token)
                result = brain.last_result
            print()
        except Exception:
            print()
            log.exception("능동 발화 실패 — 이 턴은 기억에 남기지 않는다")
            return
        if result is None:
            return
        store.append_turn(
            session_id, user_id, role="assistant",
            text=result.full_text, trigger_type="proactive",
        )
        store.log_usage("llm", result.usage)
        log.info("능동 발화 완료 — %d자", len(result.full_text))

    from datetime import UTC

    def count_initiations_today() -> int:
        """오늘(사용자 로컬 자정 이후) 능동 발화 횟수 — daily_cap 판정."""
        local_midnight = (
            datetime.now().astimezone().replace(hour=0, minute=0, second=0, microsecond=0)
        )
        return store.count_interactions("initiated", local_midnight.astimezone(UTC).isoformat())

    # 귀 배선 — --wakeword일 때만 마이크·STT를 든다 (없으면 tick만 도는 상주 골격)
    session: ListenSession | None = None
    frames = None
    wakeword = None
    listen_stt = None
    if args.wakeword:
        if not config.wakeword.ready:
            print("[웨이크워드 설정 미비 — config.yaml ear.wakeword 확인]")
            store.close()
            return
        from navi.ear import create_vad
        from navi.ear.mic import MicListener
        from navi.stt.fasterwhisper import FasterWhisperStt

        wakeword = _build_wakeword(config.wakeword)
        vad = create_vad("energy", threshold=args.vad_threshold) if args.vad_threshold else None
        session = ListenSession(
            wakeword,
            vad=vad,
            active_timeout_ms=(
                int(args.active_timeout * 1000)
                if args.active_timeout is not None
                else config.wakeword.active_timeout_ms
            ),
        )
        frames = MicListener(
            vad, device=args.mic, sample_rate=session.sample_rate, frame_ms=session.frame_ms
        ).frames()
        listen_stt = FasterWhisperStt(model_size=args.stt_model)
        print(f"[STT 모델 로딩 중… {args.stt_model}]", flush=True)
        await asyncio.to_thread(listen_stt.warmup)
        print("[잠든 채 호출어를 기다립니다 — stop 커맨드나 Ctrl+C로 종료]", flush=True)
    else:
        print("[귀 없이 상주 — tick만 돕니다. stop 커맨드나 Ctrl+C로 종료]", flush=True)

    # STT 언어 = 페르소나 발화 언어(카드 gen_lang). 없으면 ko 폴백(navi 등 supertonic
    # 한국어 카드). Whisper에 강제해 일본어 페르소나가 한국어로 오역되는 걸 막는다.
    # 부팅 시점 고정 — 런타임 페르소나 교체 시 언어까지 바꾸는 건 후속(전환은 같은 언어 내).
    stt_lang = vendor_voice.gen_lang if (vendor_voice and vendor_voice.gen_lang) else "ko"

    async def transcribe(utt) -> str:
        print("[받아쓰는 중…]")
        stt_t0 = time.perf_counter()
        text = await _transcribe_utterance(listen_stt, utt, stt_lang)
        log.info("STT %.0fms — lang=%s", (time.perf_counter() - stt_t0) * 1000, stt_lang)
        return text

    core = DaemonCore(
        bus=bus,
        transcribe=transcribe,
        run_turn=run_turn,
        session=session,
        frames=frames,
        tick_interval=args.tick_interval,
        stop_requested=STOP_FILE.exists,
        mode_machine=machine,
        persist_mode=lambda mode, until: store.set_mode_state(user_id, mode, until),
        # 능동성 2·3층 (Phase 3 순서 4)
        run_initiation=run_initiation,
        proactive=config.proactive,
        log_interaction=lambda ev, mode, note: store.log_interaction(ev, mode, note),
        count_initiations_today=count_initiations_today,
        memory_snapshot=lambda: store.recall_recent_for_user(user_id, config.recent_turns),
    )

    # 컨트롤 플레인(Stage 15) — 같은 이벤트 루프의 태스크로 기동. 예외는 삼킨다:
    # 서버가 죽어도 데몬 본체(오디오 핫패스)는 산다.
    control_server = None
    control_task: asyncio.Task | None = None
    if config.control.enabled:
        from navi.control import create_app, create_server

        control_server = create_server(
            create_app(core=core, bus=bus, swap=swap), config.control.port
        )

        async def serve_control() -> None:
            try:
                await control_server.serve()
            except Exception:
                log.exception("컨트롤 플레인 서버 예외 — 데몬 본체는 계속 돈다")

        control_task = asyncio.create_task(serve_control(), name="control")
        log.info("컨트롤 플레인 — http://127.0.0.1:%d", config.control.port)

    async def console() -> None:
        """독립 구독자 시연 — 상태 안내는 dispatcher가 아니라 관찰자가 찍는다(GUI 자리)."""
        queue = bus.subscribe("console")
        while True:
            ev = await queue.get()
            if ev.kind == EventKind.WAKE:
                print("[나비가 깨어났습니다 — 말하세요]", flush=True)
            elif ev.kind == EventKind.SLEEP:
                if ev.payload == SleepReason.TIMEOUT:
                    print("[조용해서 다시 잠듭니다]", flush=True)
                else:
                    print("[나비가 잠들었습니다 — 부르면 깨어납니다]", flush=True)
            elif ev.kind == EventKind.MODE_CHANGED:
                old_mode, new_mode = ev.payload
                print(f"[능동축: {old_mode} → {new_mode}]", flush=True)
            elif ev.kind == EventKind.SHUTDOWN:
                return

    console_task = asyncio.create_task(console(), name="console")
    try:
        await core.run()
    finally:
        # 우아한 종료: 서버 내림 → 재생·생성 중단 → 귀 정리 → 기억 닫기 (arch 4.11)
        console_task.cancel()
        if control_server is not None:
            control_server.should_exit = True
            try:
                await asyncio.wait_for(control_task, timeout=3)
            except (TimeoutError, asyncio.CancelledError):
                control_task.cancel()
        if pipeline is not None:
            pipeline.interrupt()
        if wakeword is not None:
            wakeword.close()
        store.close()
        log.info("데몬 종료 — session=%s, %s", session_id, core.state.snapshot())


def main() -> None:
    parser = argparse.ArgumentParser(prog="navi-daemon", description="companion-navi 상주 데몬")
    parser.add_argument("command", nargs="?", choices=["run", "stop"], default="run",
                        help="run(기본): 데몬 기동 / stop: 떠 있는 데몬에 종료 요청")
    parser.add_argument("--brain", choices=["gemini", "anthropic", "echo"],
                        help="config.yaml의 brain.vendor를 이번 실행만 덮어쓴다")
    parser.add_argument("--voice", action="store_true",
                        help="음성 모드 — 나비가 음성으로 답변(.venv-voice 필요)")
    parser.add_argument("--wakeword", action="store_true",
                        help="청취축 켜기 — 마이크+호출어로 대화(.venv-voice 필요)")
    parser.add_argument("--mouth", choices=["fake", "supertonic", "gptsovits"],
                        help="mouth.vendor 덮어쓰기 (--voice와 함께)")
    parser.add_argument("--persona",
                        help="페르소나를 이번 실행만 교체 — 이름만(personas/<이름>.yaml). "
                             "TTS 엔진은 부팅 카드가 정하므로 다른 엔진의 음색을 쓰려면 이걸로 재기동한다")
    parser.add_argument("--mic", type=int, metavar="INDEX", help="입력 장치 번호")
    parser.add_argument("--vad-threshold", type=float, metavar="RMS", help="발화 RMS 임계")
    parser.add_argument("--stt-model", default="large-v3-turbo", metavar="SIZE",
                        help="faster-whisper 모델 크기")
    parser.add_argument("--active-timeout", type=float, metavar="SEC",
                        help="ACTIVE 유지 시간(무음 기준)")
    parser.add_argument("--tick-interval", type=float, default=10.0, metavar="SEC",
                        help="TICK 발행 주기 (기본 10초)")
    parser.add_argument("--db", help="기억 DB 경로 덮어쓰기(테스트용)")
    parser.add_argument("-v", "--verbose", action="count", default=0)
    args = parser.parse_args()

    # 상주 프로세스는 출력이 파일로 리다이렉트되기 쉽다(Windows 기본 cp949) — 인코딩 불가
    # 문자로 죽지 않게 replace로 완화한다. 콘솔 인코딩 자체는 건드리지 않는다.
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(errors="replace")

    if args.command == "stop":
        raise SystemExit(cmd_stop())

    from dataclasses import replace

    from navi.config import load_config

    _setup_logging(args.verbose)
    persona_card = f"personas/{args.persona}.yaml" if args.persona else None
    config = load_config(mouth_vendor=args.mouth, persona_card=persona_card)
    if args.brain:
        config = replace(config, brain=replace(config.brain, vendor=args.brain))
    if args.db:
        config = replace(config, db_path=Path(args.db))

    if not acquire_pidfile():
        print(f"이미 실행 중입니다 (PID {_read_pid(PID_FILE)}) — stop으로 먼저 내리세요")
        raise SystemExit(1)
    STOP_FILE.unlink(missing_ok=True)  # 이전 실행의 잔여 센티널 제거
    failed = False
    try:
        asyncio.run(_run(config, args))
        print("(나비 데몬 내려감)")
    except KeyboardInterrupt:  # 핸들러 설치 전 인터럽트·두 번째 Ctrl+C(강제)
        print("\n(나비 데몬 내려감)")
    except BaseException:
        failed = True
        if args.voice:
            # --voice는 아래 finally에서 os._exit로 프로세스를 즉시 없앤다 — 파이썬의
            # 기본 traceback 출력에 도달하지 못해 "로그 2줄 남기고 exit 0"으로 위장했다
            # (부팅 실패가 이렇게 숨어 있었다). 그 경로에서만 사인을 직접 남긴다.
            # 비음성 경로는 예외가 정상 전파돼 파이썬이 알아서 찍으므로 여기서 찍으면 중복.
            log.exception("데몬이 예외로 종료됨")
        raise
    finally:
        release_pidfile()
        if args.voice:
            # cli.py os._exit(0)과 동일 사유 — torch/PortAudio 잔여 스레드가 종료를 막는다.
            # 실패는 반드시 0이 아닌 코드로 — 실행 스크립트가 이걸 보고 판단한다.
            os._exit(1 if failed else 0)


if __name__ == "__main__":
    main()
