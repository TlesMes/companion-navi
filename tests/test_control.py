"""컨트롤 플레인 검증 — 가짜 부품 주입으로 HTTP/WS 표면을 고정한다 (Stage 15 PR ①).

FastAPI TestClient(마이크·키·uvicorn 불필요)로 앱 계약만 검증한다 — 실서버 기동은
오프라인 E2E(echo 두뇌 데몬 + curl)에서. GUI 버튼이 음성 명령(검문①)과 같은
DaemonCore.command_mode 경로를 탄다는 것("결정론 게이트는 데몬 소유")을 여기서 못 박는다.
"""

import time
from datetime import datetime
from datetime import time as dtime

from fastapi.testclient import TestClient

from navi.bus import Event, EventBus, EventKind
from navi.control import create_app
from navi.daemon import DaemonCore
from navi.heartbeat import ModeMachine, SleepWindow

WINDOW = SleepWindow(start=dtime(23, 0), end=dtime(7, 0))
DAYTIME = datetime(2026, 7, 10, 14, 0)  # 창 밖 — 기본 ACTIVE


class _Clock:
    def __init__(self, at: datetime):
        self.at = at

    def __call__(self) -> datetime:
        return self.at


def _make(at: datetime = DAYTIME, *, with_machine: bool = True):
    bus = EventBus()
    persisted: list = []
    machine = ModeMachine(WINDOW, 30, now=_Clock(at)) if with_machine else None
    core = DaemonCore(
        bus=bus,
        transcribe=None,
        run_turn=None,
        tick_interval=999,
        stop_poll=999,
        mode_machine=machine,
        persist_mode=lambda mode, until: persisted.append((mode, until)),
    )
    client = TestClient(create_app(core=core, bus=bus))
    return client, core, bus, persisted


# --- GET /status: DaemonState.snapshot 그대로 ---


def test_status_returns_snapshot():
    client, _core, _bus, _p = _make()
    body = client.get("/status").json()
    assert body["listening_mode"] == "sleep"
    assert body["proactive_mode"] == "active"
    assert body["can_speak"] is True
    assert body["turns_count"] == 0
    assert "uptime_s" in body and "last_events" in body


# --- POST /mode/{cmd}: 음성 명령과 같은 command_mode 경로 ---


def test_mode_command_transitions_persists_and_publishes():
    client, core, bus, persisted = _make()
    observer = bus.subscribe("observer", maxsize=16)

    body = client.post("/mode/snooze").json()
    assert body == {"mode": "snooze", "can_speak": False}
    assert core.state.proactive_mode == "snooze"
    assert persisted[-1][0] == "snooze"  # 명령 경로는 항상 영속화(force_persist)
    kinds = [observer.get_nowait().kind for _ in range(observer.qsize())]
    assert EventKind.MODE_CHANGED in kinds


def test_wake_clears_snooze_like_voice_command():
    client, core, _bus, _p = _make()
    client.post("/mode/snooze")
    body = client.post("/mode/wake").json()
    assert body == {"mode": "active", "can_speak": True}
    assert core.state.proactive_mode == "active"


def test_unknown_command_is_404():
    client, _core, _bus, _p = _make()
    assert client.post("/mode/party").status_code == 404


def test_mode_command_without_machine_is_503():
    client, _core, _bus, _p = _make(with_machine=False)
    assert client.post("/mode/wake").status_code == 503


# --- PUT /mode/window: 취침창 런타임 변경 — 즉시 시간 전이 재평가 ---


def test_window_change_applies_immediately():
    client, core, _bus, _p = _make(at=datetime(2026, 7, 10, 14, 0))
    body = client.put("/mode/window", json={"start": "13:00", "end": "15:00"}).json()
    assert body["mode"] == "sleep"  # 14시가 새 창 안 — 다음 tick을 기다리지 않는다
    assert core.state.proactive_mode == "sleep"


def test_window_bad_format_is_422():
    client, _core, _bus, _p = _make()
    assert client.put("/mode/window", json={"start": "정오", "end": "15:00"}).status_code == 422


# --- POST /shutdown: 센티널 파일 방식 대체 ---


def test_shutdown_publishes_shutdown_event():
    client, _core, bus, _p = _make()
    observer = bus.subscribe("observer", maxsize=16)
    assert client.post("/shutdown").json() == {"ok": True}
    assert observer.get_nowait().kind is EventKind.SHUTDOWN


# --- WS /events: 링버퍼 백필 → 라이브 구독 ---


def test_ws_backfills_ring_buffer_then_streams_live():
    client, core, bus, _p = _make()
    t0 = time.monotonic()
    core.state.record(Event(EventKind.WAKE, t0))
    core.state.record(Event(EventKind.MODE_CHANGED, t0, ("active", "snooze")))

    with client.websocket_connect("/events") as ws:
        first = ws.receive_json()
        second = ws.receive_json()
        assert first["kind"] == "WAKE" and first["payload"] is None
        assert second["kind"] == "MODE_CHANGED"
        assert second["payload"] == ["active", "snooze"]  # tuple → JSON 배열

        bus.publish(Event(EventKind.STAGE, t0, ("stt", "done", {"ms": 842})))
        live = ws.receive_json()
        assert live["kind"] == "STAGE"
        assert live["payload"] == ["stt", "done", {"ms": 842}]


def test_ws_serializes_opaque_payload_as_str():
    client, core, _bus, _p = _make()

    class _Utt:
        def __str__(self) -> str:
            return "<utterance 1.2s>"

    core.state.record(Event(EventKind.UTTERANCE, time.monotonic(), _Utt()))
    with client.websocket_connect("/events") as ws:
        assert ws.receive_json()["payload"] == "<utterance 1.2s>"  # 원문 객체는 요약만
