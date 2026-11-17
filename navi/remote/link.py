"""서버와의 연결 — 기기 토큰과 WebSocket 하나 (design/protocol.md §2).

토큰은 처음 한 번 서버에서 받아 파일에 둔다. **토큰이 곧 이 기기의 정체다** — 서버의 대화
기록은 토큰이 가리키는 사용자에게 붙어 있다. 그래서 토큰이 거부됐다고 조용히 새로 받지
않는다: 새 토큰은 새 사용자이고, 나비가 아무 말 없이 기억을 잃게 된다.
"""

from __future__ import annotations

import asyncio
import json
import logging
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

import websockets
from websockets.exceptions import ConnectionClosed, InvalidStatus

from navi.brain.base import BrainAuthError, BrainUnavailable

log = logging.getLogger(__name__)

_CONNECT_TIMEOUT_S = 10


class ServerError(BrainUnavailable):
    """서버가 턴을 거절했거나 실패했다(`turn.error`).

    BrainUnavailable의 하위다 — 호출부(파이프라인·데몬)는 두뇌가 답하지 못한 것으로 다룬다.
    code는 protocol.md §3.3의 값이고, retry_after_s는 서버가 알려 줬을 때만 있다.
    """

    def __init__(self, code: str, message: str, retry_after_s: int | None = None):
        super().__init__(f"{code}: {message}")
        self.code = code
        self.retry_after_s = retry_after_s


class ServerLink:
    """서버 연결 하나. 필요할 때 열고, 끊기면 다음 턴에 다시 연다.

    끊긴 턴을 이어받지 않는다(D22) — 다시 연결하면 새 턴부터다.
    """

    def __init__(self, url: str, token_path: Path):
        self._http_base = url.rstrip("/")
        self._ws_url = self._http_base.replace("http", "ws", 1) + "/v1/turn-stream"
        self._token_path = token_path
        self._ws: Any = None
        self.ready: dict[str, Any] = {}  # 서버의 session.ready — 한도·페르소나 목록

    @property
    def url(self) -> str:
        return self._http_base

    async def connect(self) -> dict[str, Any]:
        """연결돼 있으면 그대로, 아니면 연다. 서버의 session.ready를 돌려준다."""
        if self._ws is not None:
            return self.ready
        token = await asyncio.to_thread(self._token)
        try:
            ws = await websockets.connect(
                self._ws_url,
                additional_headers={"Authorization": f"Bearer {token}"},
                open_timeout=_CONNECT_TIMEOUT_S,
            )
        except InvalidStatus as exc:
            if exc.response.status_code == 401:
                raise BrainAuthError(
                    f"서버가 기기 토큰을 거부했어요 — {self._token_path}. 이 파일을 지우면 "
                    "새 기기로 등록되지만 서버의 대화 기록과는 이어지지 않아요"
                ) from exc
            raise BrainUnavailable(f"서버 연결 실패(HTTP {exc.response.status_code})") from exc
        except (OSError, TimeoutError) as exc:
            raise BrainUnavailable(f"서버에 닿지 못했어요 — {self._http_base}") from exc
        try:
            self.ready = json.loads(await asyncio.wait_for(ws.recv(), _CONNECT_TIMEOUT_S))
        except (ConnectionClosed, TimeoutError) as exc:
            raise BrainUnavailable("서버가 연결 직후 응답하지 않았어요") from exc
        self._ws = ws
        log.info(
            "서버 연결 — %s, user=%s, 한도=%s",
            self._http_base, self.ready.get("user_id"), self.ready.get("limits"),
        )
        return self.ready

    async def send(self, message: dict[str, Any]) -> None:
        if self._ws is None:
            raise BrainUnavailable("서버와 연결돼 있지 않아요")
        try:
            await self._ws.send(json.dumps(message, ensure_ascii=False))
        except ConnectionClosed as exc:
            self._drop()
            raise BrainUnavailable("서버와의 연결이 끊겼어요") from exc

    async def recv(self) -> dict[str, Any]:
        if self._ws is None:
            raise BrainUnavailable("서버와 연결돼 있지 않아요")
        try:
            msg = json.loads(await self._ws.recv())
        except ConnectionClosed as exc:
            self._drop()
            raise BrainUnavailable("서버와의 연결이 끊겼어요") from exc
        if msg.get("type") in ("session.standby", "session.active"):
            # 활성권 변화(D20). 대기여도 끊지 않는다 — 이 기기에서 사용자가 말을 걸면 서버가
            # 반응 턴을 받으면서 활성권을 돌려준다. 여기서는 알리기만 한다.
            log.info("서버 활성권 — %s", "대기(다른 기기가 가져감)" if msg["type"] == "session.standby" else "활성")
        return msg

    async def close(self) -> None:
        ws, self._ws = self._ws, None
        if ws is not None:
            await ws.close()

    def _drop(self) -> None:
        """죽은 연결을 버린다 — 다음 connect가 새로 연다."""
        self._ws = None

    def _token(self) -> str:
        """저장된 토큰. 없으면 서버에 기기를 등록해 받아 둔다(블로킹 — 스레드에서 부른다)."""
        if self._token_path.exists():
            token = self._token_path.read_text(encoding="utf-8").strip()
            if token:
                return token
        request = urllib.request.Request(f"{self._http_base}/v1/devices", method="POST", data=b"")
        try:
            with urllib.request.urlopen(request, timeout=_CONNECT_TIMEOUT_S) as response:
                device = json.load(response)
        except urllib.error.HTTPError as exc:
            if exc.code == 429:
                # 같은 곳에서 등록이 너무 잦다(서버의 등록 제한). 기다리면 된다.
                wait = exc.headers.get("Retry-After", "?")
                raise BrainUnavailable(f"기기 등록이 너무 잦아요 — {wait}초 뒤에 다시 해 주세요") from exc
            raise BrainUnavailable(f"서버가 기기 등록을 거절했어요(HTTP {exc.code})") from exc
        except (urllib.error.URLError, OSError) as exc:
            raise BrainUnavailable(f"서버에 기기를 등록하지 못했어요 — {self._http_base}") from exc
        self._token_path.parent.mkdir(parents=True, exist_ok=True)
        self._token_path.write_text(device["device_token"], encoding="utf-8")
        log.info(
            "서버에 새 기기로 등록 — user=%s, 토큰은 %s에 저장",
            device.get("user_id"), self._token_path,
        )
        return device["device_token"]
