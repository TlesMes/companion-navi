"""컨트롤 플레인 서버 — GUI(별도 프로세스)가 데몬을 관찰·조작하는 유일한 통로 (Stage 15 PR ①).

버스가 프로세스 내 pub/sub이라 서버는 데몬 이벤트 루프의 태스크로 돈다(밖에선 구독 불가).
판정 로직은 갖지 않는다 — 버튼은 음성 명령과 같은 DaemonCore.command_mode()를 부를 뿐
(결정론 게이트는 데몬 소유, gui.md 원칙). 바인딩 127.0.0.1 고정 — localhost 밖 노출 없음.

GUI 죽어도 나비는 산다: WS 구독자는 버스의 유한 큐 + 논블로킹 publish로 격리되고,
서버 태스크 예외는 데몬(_run)이 삼킨다 — 서버가 죽어도 오디오 핫패스는 무사하다.
"""

from __future__ import annotations

import asyncio
import logging
import time
import uuid
from contextlib import contextmanager
from datetime import time as dtime
from enum import Enum

from pathlib import Path

import uvicorn
from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse
from pydantic import BaseModel

from navi.bus import Event, EventBus, EventKind
from navi.control.runtime import SwapBusy, SwapRuntime
from navi.daemon import DaemonCore
from navi.heartbeat import Mode, ModeCommand, SleepWindow

log = logging.getLogger(__name__)

# URL 조각 → 명령 (gui.md API 표면). 음성 구절(검문①)과 어휘가 같다 — 같은 상태머신 API.
_COMMANDS = {
    "wake": ModeCommand.WAKE,
    "snooze": ModeCommand.SNOOZE,
    "dnd": ModeCommand.DND,
    "dnd_clear": ModeCommand.DND_CLEAR,
    "sleep": ModeCommand.SLEEP,
}


class WindowBody(BaseModel):
    start: str  # "HH:MM"
    end: str


class PersonaBody(BaseModel):
    id: str  # personas/<id>.yaml 파일명 stem


class VoiceBody(BaseModel):
    name: str  # 현재 페르소나 톤 목록의 name


def _payload_json(payload) -> object:
    """이벤트 payload를 JSON 가능한 형태로 — 원문 객체(Utterance 등)는 str 요약만."""
    if payload is None or isinstance(payload, (str, int, float, bool)):
        return payload
    if isinstance(payload, Enum):
        return payload.name
    if isinstance(payload, (tuple, list)):
        return [_payload_json(item) for item in payload]
    if isinstance(payload, dict):
        return {key: _payload_json(value) for key, value in payload.items()}
    return str(payload)


def event_json(event: Event) -> dict:
    return {
        "kind": event.kind.name,
        "ts": event.ts,
        # 이벤트 ts는 monotonic이라 GUI가 시각으로 못 읽는다 — 직렬화 시점의
        # 두 시계 차로 벽시계 근사치를 실어 보낸다(백필 이벤트도 원래 시각 유지).
        "wall_ts": time.time() - (time.monotonic() - event.ts),
        "payload": _payload_json(event.payload),
    }


# GUI 단일 파일 프런트(gui.md PR ③) — 같은 오리진으로 서빙해 CORS 없이 API를 부른다.
_INDEX_HTML = Path(__file__).resolve().parents[1] / "gui" / "static" / "index.html"


def create_app(
    *, core: DaemonCore, bus: EventBus, swap: SwapRuntime | None = None
) -> FastAPI:
    """DaemonCore·버스를 주입받아 FastAPI 앱 구성 — 테스트는 가짜 부품으로 같은 앱을 만든다."""
    # 엔드포인트는 전부 async — FastAPI는 동기 def를 스레드풀로 돌려서 SQLite 영속화
    # (persist_mode)의 단일 스레드 규약이 깨진다. 호출은 전부 논블로킹이라 루프 실행이 맞다.
    app = FastAPI(title="navi-control", docs_url=None, redoc_url=None)

    @app.get("/")
    async def index() -> FileResponse:
        if not _INDEX_HTML.is_file():
            raise HTTPException(404, "GUI 프런트 없음 — navi/gui/static/index.html")
        return FileResponse(_INDEX_HTML, media_type="text/html")

    @app.get("/status")
    async def status() -> dict:
        body = core.state.snapshot()
        window = core.sleep_window()
        body["sleep_window"] = (
            {"start": window.start.strftime("%H:%M"), "end": window.end.strftime("%H:%M")}
            if window is not None
            else None
        )
        return body

    @app.post("/mode/{cmd}")
    async def mode_command(cmd: str) -> dict:
        command = _COMMANDS.get(cmd)
        if command is None:
            raise HTTPException(404, f"지원하지 않는 명령: {cmd!r} (wake·snooze·dnd·dnd_clear·sleep)")
        try:
            mode = core.command_mode(command)
        except RuntimeError as exc:  # 상태머신 미구성
            raise HTTPException(503, str(exc)) from exc
        return {"mode": mode.value, "can_speak": mode is Mode.ACTIVE}

    @app.put("/mode/window")
    async def mode_window(body: WindowBody) -> dict:
        try:
            window = SleepWindow(dtime.fromisoformat(body.start), dtime.fromisoformat(body.end))
        except ValueError as exc:
            raise HTTPException(422, f"HH:MM 형식이 아닙니다: {exc}") from exc
        try:
            mode = core.set_sleep_window(window)
        except RuntimeError as exc:
            raise HTTPException(503, str(exc)) from exc
        return {"mode": mode.value, "start": body.start, "end": body.end}

    # --- 페르소나·톤 교체 (Stage 15-②) — 판정은 SwapRuntime, 여긴 HTTP 번역만 ---

    def _swap() -> SwapRuntime:
        if swap is None:  # 미구성 → 503 (command_mode의 상태머신 미구성 패턴과 동일)
            raise HTTPException(503, "페르소나 런타임 미구성")
        return swap

    @app.get("/personas")
    async def personas() -> list[dict]:
        return _swap().list_personas()

    @app.post("/persona")
    async def persona(body: PersonaBody) -> dict:
        try:
            return await _swap().swap_persona(body.id)
        except LookupError as exc:
            raise HTTPException(404, str(exc)) from exc
        except SwapBusy as exc:
            raise HTTPException(409, str(exc)) from exc

    @app.get("/voices")
    async def voices() -> list[dict]:
        try:
            return _swap().list_voices()
        except RuntimeError as exc:  # 파이프라인 없음(텍스트 모드)
            raise HTTPException(503, str(exc)) from exc

    @app.get("/voices/{name}/audio")
    async def voice_audio(name: str) -> FileResponse:
        """톤 레퍼런스 wav 시청취 — 재생은 GUI(<audio>)가 한다, 데몬 스피커 미사용."""
        path = _swap().tone_file(name)
        if path is None:
            raise HTTPException(404, f"시청취할 파일 없음: {name!r}")
        return FileResponse(path, media_type="audio/wav")

    @app.post("/voice")
    async def voice(body: VoiceBody) -> dict:
        try:
            return _swap().set_voice(body.name)
        except LookupError as exc:
            raise HTTPException(404, str(exc)) from exc
        except SwapBusy as exc:
            raise HTTPException(409, str(exc)) from exc
        except RuntimeError as exc:  # 파이프라인 없음(텍스트 모드)
            raise HTTPException(503, str(exc)) from exc

    @app.post("/shutdown")
    async def shutdown() -> dict:
        # 센티널 파일(logs/navi.stop) 방식 대체(Stage 13 예고) — SHUTDOWN이 전 구독자에 퍼진다
        bus.publish(Event(EventKind.SHUTDOWN, time.monotonic()))
        return {"ok": True}

    @app.websocket("/events")
    async def events(ws: WebSocket) -> None:
        await ws.accept()
        name = f"gui-{uuid.uuid4().hex[:8]}"  # 창 여러 개·재접속 대비 구독자명 유일화
        queue = bus.subscribe(name)
        try:
            for past in list(core.state.last_events):  # 링버퍼(50)로 초기 채움
                await ws.send_json(event_json(past))
            while True:
                try:
                    event = await asyncio.wait_for(queue.get(), timeout=1.0)
                except TimeoutError:
                    continue  # 폴링 재개 — 타 스레드 publish의 웨이크업 누락도 흡수
                await ws.send_json(event_json(event))
                if event.kind is EventKind.SHUTDOWN:
                    return
        except (WebSocketDisconnect, RuntimeError):
            pass  # 클라이언트가 끊었다 — GUI 죽어도 나비는 산다
        finally:
            bus.unsubscribe(name)

    return app


class _DaemonServer(uvicorn.Server):
    """시그널은 데몬이 소유한다(Ctrl+C = KeyboardInterrupt) — uvicorn 가로채기 무력화."""

    def install_signal_handlers(self) -> None:
        pass

    @contextmanager
    def capture_signals(self):  # uvicorn 0.29+ 경로 — 컨텍스트만 통과시킨다
        yield


def create_server(app: FastAPI, port: int) -> uvicorn.Server:
    config = uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning")
    return _DaemonServer(config)
