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
from navi.control import SwapRuntime, create_app
from navi.daemon import DaemonCore
from navi.heartbeat import ModeMachine, SleepWindow
from navi.mouth.fake import FakeMouth
from navi.persona import CharacterCard
from navi.pipeline import TurnPipeline

WINDOW = SleepWindow(start=dtime(23, 0), end=dtime(7, 0))
DAYTIME = datetime(2026, 7, 10, 14, 0)  # 창 밖 — 기본 ACTIVE


class _Clock:
    def __init__(self, at: datetime):
        self.at = at

    def __call__(self) -> datetime:
        return self.at


def _make(at: datetime = DAYTIME, *, with_machine: bool = True, swap=None):
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
    client = TestClient(create_app(core=core, bus=bus, swap=swap))
    return client, core, bus, persisted


# --- SwapRuntime 픽스처 (Stage 15-②) — tmp 인라인 카드, aris.yaml 비의존 ---

# 최소 프로필 1개 + supertonic 톤 2개(F1·F2). voice_block으로 섹션을 바꿔 끼운다.
_SUPERTONIC_VOICE = (
    "voice:\n"
    "  name: {name}\n"
    "  supertonic:\n"
    "    tones:\n"
    "      - {{name: 기본, icon: mood-smile, voice_id: F1}}\n"
    "      - {{name: 신남, icon: mood-happy, voice_id: F2}}\n"
)


class _StubConductor:
    """SwapRuntime이 쓰는 것만 — set_card / card. system 교체 검증은 test_conductor 몫."""

    def __init__(self, card):
        self._card = card

    def set_card(self, card) -> None:
        self._card = card

    @property
    def card(self):
        return self._card


def _write_card(path, character: str, *, voice_block: str = "") -> None:
    path.write_text(
        f"character: {character}\n"
        f"{voice_block}"
        "profiles:\n"
        "  - {name: 기본, min_intimacy: 0, background: b, traits: t,\n"
        "     example_dialogues: [{user: u, assistant: a}]}\n",
        encoding="utf-8",
    )


def _make_swap(tmp_path, *, with_pipeline: bool = True):
    """navi(supertonic 2톤) + other(supertonic 2톤) 카드로 SwapRuntime 구성."""
    personas = tmp_path / "personas"
    personas.mkdir(exist_ok=True)
    _write_card(personas / "navi.yaml", "나비", voice_block=_SUPERTONIC_VOICE.format(name="navi"))
    _write_card(personas / "other.yaml", "다른애", voice_block=_SUPERTONIC_VOICE.format(name="other"))

    card = CharacterCard.load(personas / "navi.yaml", root=tmp_path)
    conductor = _StubConductor(card)
    pipeline = None
    if with_pipeline:
        tone = card.voice.default_tone("supertonic")
        pipeline = TurnPipeline(
            brain=None,  # run_turn을 부르지 않으므로 저장만 됨(교체 계약만 검증)
            mouth=FakeMouth(),
            conductor=conductor,
            voice=card.voice.profile(tone),
        )
    swap = SwapRuntime(
        conductor=conductor,
        pipeline=pipeline,
        personas_dir=personas,
        root=tmp_path,
        vendor="supertonic",
        persona_id="navi",
        loaded_ckpts=("", ""),  # supertonic = 무가중치
    )
    return swap, pipeline


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


# --- 페르소나·톤 교체 (Stage 15-②) — SwapRuntime 주입 ---


def test_personas_scan_lists_current(tmp_path):
    swap, _ = _make_swap(tmp_path)
    client, *_ = _make(swap=swap)
    body = client.get("/personas").json()
    by_id = {p["id"]: p for p in body}
    assert by_id["navi"]["current"] is True
    assert by_id["navi"]["character"] == "나비"
    assert by_id["other"]["current"] is False


def test_persona_swap_changes_card_and_reports_voice_swapped(tmp_path):
    swap, pipeline = _make_swap(tmp_path)
    client, *_ = _make(swap=swap)
    body = client.post("/persona", json={"id": "other"}).json()
    assert body == {"id": "other", "character": "다른애", "voice_swapped": True}
    assert swap.character == "다른애"  # 카드 교체됨
    # 같은 supertonic(ckpts 일치)이라 목소리도 새 카드 기본 톤(name=other)으로
    assert pipeline.current_voice.name == "other"


def test_persona_swap_unknown_id_is_404(tmp_path):
    swap, _ = _make_swap(tmp_path)
    client, *_ = _make(swap=swap)
    assert client.post("/persona", json={"id": "없는애"}).status_code == 404


def test_persona_swap_is_idempotent(tmp_path):
    swap, _ = _make_swap(tmp_path)
    client, *_ = _make(swap=swap)
    assert client.post("/persona", json={"id": "navi"}).json()["character"] == "나비"


def test_persona_swap_without_voice_section_keeps_voice(tmp_path):
    """대상 카드에 voice 섹션 없음 → 카드만 교체, 톤 유지, voice_swapped false."""
    swap, pipeline = _make_swap(tmp_path)
    _write_card(tmp_path / "personas" / "plain.yaml", "민짜")  # voice 섹션 없음
    client, *_ = _make(swap=swap)
    before = pipeline.current_voice
    body = client.post("/persona", json={"id": "plain"}).json()
    assert body["voice_swapped"] is False
    assert pipeline.current_voice is before  # 목소리 불변


def test_voices_lists_current_tone(tmp_path):
    swap, _ = _make_swap(tmp_path)
    client, *_ = _make(swap=swap)
    body = client.get("/voices").json()
    names = {v["name"]: v for v in body}
    assert names["기본"]["current"] is True  # 부팅 기본 톤 = F1
    assert names["신남"]["current"] is False
    assert names["신남"]["icon"] == "mood-happy"


def test_voice_swap_applies_to_pipeline(tmp_path):
    swap, pipeline = _make_swap(tmp_path)
    client, *_ = _make(swap=swap)
    body = client.post("/voice", json={"name": "신남"}).json()
    assert body == {"name": "신남", "applied": "next_turn"}
    assert pipeline.current_voice.vendor_voice_id == "F2"  # 다음 턴 반영


def test_voice_swap_unknown_tone_is_404(tmp_path):
    swap, _ = _make_swap(tmp_path)
    client, *_ = _make(swap=swap)
    assert client.post("/voice", json={"name": "없는톤"}).status_code == 404


def test_swap_while_playing_is_409(tmp_path):
    swap, pipeline = _make_swap(tmp_path)
    pipeline._mouth._playing = True  # 재생 중 시뮬레이션
    client, *_ = _make(swap=swap)
    assert client.post("/voice", json={"name": "신남"}).status_code == 409
    assert client.post("/persona", json={"id": "other"}).status_code == 409


def test_swap_endpoints_503_without_swap_runtime(tmp_path):
    """swap 미주입(PR ① 그대로) → 교체 API는 전부 503, 기존 표면은 무영향."""
    client, *_ = _make()  # swap=None
    assert client.get("/personas").status_code == 503
    assert client.post("/persona", json={"id": "navi"}).status_code == 503
    assert client.get("/voices").status_code == 503
    assert client.post("/voice", json={"name": "기본"}).status_code == 503
    assert client.get("/status").status_code == 200  # 기존 표면은 그대로


def test_text_mode_pipeline_none_voices_503_persona_ok(tmp_path):
    """텍스트 모드(pipeline=None) — 톤 API는 503, 페르소나 교체는 카드만 성공."""
    swap, _ = _make_swap(tmp_path, with_pipeline=False)
    client, *_ = _make(swap=swap)
    assert client.get("/voices").status_code == 503
    assert client.post("/voice", json={"name": "기본"}).status_code == 503
    body = client.post("/persona", json={"id": "other"}).json()
    assert body == {"id": "other", "character": "다른애", "voice_swapped": False}


def test_personas_scan_skips_broken_yaml(tmp_path):
    swap, _ = _make_swap(tmp_path)
    (tmp_path / "personas" / "broken.yaml").write_text("character: [\n", encoding="utf-8")
    client, *_ = _make(swap=swap)
    ids = {p["id"] for p in client.get("/personas").json()}
    assert ids == {"navi", "other"}  # 파손 카드는 조용히 건너뜀
