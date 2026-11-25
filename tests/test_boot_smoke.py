"""부팅 스모크 — 진짜 `_run`을 띄워 한 턴 돌리고 내린다.

test_daemon.py는 `DaemonCore`에 협력자를 직접 주입해 부팅 경로(`_run`)를 건너뛴다. 그래서
`_build_feed`가 `user_id` 정의보다 앞에 있던 부팅 즉사 버그(2026.08.30)가 411 tests green
속에 숨었다. 린터(ruff F821)가 이름 해석 종류는 가져갔지만 조립 순서·config 불일치·어댑터
계약 위반은 실행해야 드러난다 — 이 파일이 그 자리다.

격리: 저장소의 **커밋된** config.yaml과 카드만 임시 루트로 복사한다. config.local.yaml·.env·
본 기억 DB는 읽지 않는다(머신마다 다른 값이 테스트를 흔들지 않게, 실호출이 새지 않게).
두뇌는 echo, 귀·입은 끈다. 컨트롤 플레인은 빈 포트에 띄운다 — 떠 있는 실데몬(8765)과 겹치지 않게.
"""

import asyncio
import json
import shutil
import signal
import socket
import sqlite3
import urllib.request
from argparse import Namespace
from dataclasses import replace
from pathlib import Path

import pytest

from navi import daemon
from navi.config import load_config

_REPO = Path(__file__).resolve().parent.parent


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _args(**overrides) -> Namespace:
    """main()의 argparse 기본값과 같은 모양 — 텍스트 모드(귀·입 없음)."""
    base = dict(
        command="run", brain="echo", voice=False, wakeword=False, mouth=None, persona=None,
        mic=None, vad_threshold=None, stt_model="large-v3-turbo", stt_lang=None,
        active_timeout=None, tick_interval=3600.0, db=None, server="", verbose=0,
    )
    base.update(overrides)
    return Namespace(**base)


@pytest.fixture
def repo_root(tmp_path: Path) -> Path:
    shutil.copy(_REPO / "config.yaml", tmp_path / "config.yaml")
    shutil.copytree(_REPO / "personas", tmp_path / "personas")
    return tmp_path


@pytest.fixture(autouse=True)
def _restore_signals(tmp_path, monkeypatch):
    """`_run`은 SIGINT·SIGBREAK 핸들러를 갈아 끼운다 — pytest 쪽 Ctrl+C를 되돌려 둔다."""
    saved = {
        sig: signal.getsignal(sig)
        for sig in (signal.SIGINT, getattr(signal, "SIGBREAK", None))
        if sig is not None
    }
    # stop 센티널은 저장소의 logs/navi.stop이다 — 떠 있는 실데몬의 것과 섞이지 않게 옮긴다.
    monkeypatch.setattr(daemon, "STOP_FILE", tmp_path / "navi.stop")
    yield
    for sig, handler in saved.items():
        signal.signal(sig, handler)


def _http(port: int, method: str, path: str, body: dict | None = None) -> tuple[int, dict]:
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(
        f"http://127.0.0.1:{port}{path}", data=data, method=method,
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=5) as resp:
        return resp.status, json.loads(resp.read() or b"{}")


async def _wait_until(check, run: asyncio.Task, *, timeout: float = 20.0, what: str) -> None:
    deadline = asyncio.get_running_loop().time() + timeout
    while True:
        if run.done():
            # 부팅이 죽었으면 타임아웃까지 기다리지 말고 그 예외를 그대로 드러낸다.
            run.result()
            raise AssertionError(f"{what} 전에 `_run`이 끝났다")
        try:
            if await asyncio.to_thread(check):
                return
        except OSError:
            pass  # 서버가 아직 안 떴다
        if asyncio.get_running_loop().time() > deadline:
            raise AssertionError(f"{what} — {timeout}s 안에 안 됐다")
        await asyncio.sleep(0.1)


def _turn_count(db: Path) -> int:
    with sqlite3.connect(db) as conn:
        return conn.execute("SELECT COUNT(*) FROM conversation_turn").fetchone()[0]


def test_run_boots_takes_a_text_turn_and_shuts_down(repo_root: Path):
    db = repo_root / "smoke.db"
    port = _free_port()
    config = load_config(repo_root, db_path=db, brain_vendor="echo", server_url="")
    config = replace(config, control=replace(config.control, enabled=True, port=port))

    async def scenario() -> None:
        run = asyncio.create_task(daemon._run(config, _args()))
        try:
            await _wait_until(
                lambda: _http(port, "GET", "/status")[0] == 200, run, what="컨트롤 플레인 기동"
            )
            status, body = await asyncio.to_thread(_http, port, "GET", "/status")
            assert body["server"] is None  # 단독 모드

            status, _ = await asyncio.to_thread(_http, port, "POST", "/say", {"text": "안녕"})
            assert status == 202
            # 턴이 끝나면 사용자·나비 두 행이 기억에 남는다(echo 두뇌도 정상 턴이다).
            await _wait_until(lambda: _turn_count(db) >= 2, run, what="텍스트 턴 기록")

            await asyncio.to_thread(_http, port, "POST", "/shutdown")
            await asyncio.wait_for(run, timeout=10)
        finally:
            if not run.done():
                run.cancel()

    asyncio.run(scenario())
    assert _turn_count(db) == 2
