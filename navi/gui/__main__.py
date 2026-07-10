"""pywebview 창 기동 — 데몬 미기동이면 대기 화면을 띄우고 자동 재시도한다.

GUI는 오디오를 만지지 않는다(gui.md) — 의존성은 pywebview 하나, 기본 venv에서 돈다.
창은 컨트롤 플레인(127.0.0.1:{port})의 GET / 를 열 뿐이고, 상태·제어는 전부
프런트(index.html)가 같은 오리진 API/WS로 처리한다.
"""

from __future__ import annotations

import argparse
import threading
import time
import urllib.request

# 대기 화면 — 프런트(index.html)와 같은 다크 팔레트. 데몬이 뜨면 파이썬 폴러가 갈아끼운다.
_WAITING_HTML = """<!doctype html><html lang="ko"><head><meta charset="utf-8"><style>
body { margin: 0; height: 100vh; display: flex; flex-direction: column; align-items: center;
       justify-content: center; gap: 14px; background: #262521; color: #a8a396;
       font-family: "Malgun Gothic", system-ui, sans-serif; font-size: 13px; }
.dot { width: 46px; height: 46px; border-radius: 50%; background: #1e3a5f; color: #8ec2f2;
       display: flex; align-items: center; justify-content: center; font-size: 18px;
       animation: pulse 1.6s ease-in-out infinite; }
@keyframes pulse { 0%,100% { opacity: 0.35; } 50% { opacity: 1; } }
code { font-family: Consolas, monospace; color: #7a756a; font-size: 12px; }
</style></head><body>
<div class="dot">나</div>
<p>데몬을 기다리는 중…</p>
<code>python -m navi.daemon 으로 먼저 띄워 주세요</code>
</body></html>"""


def _daemon_up(base_url: str) -> bool:
    try:
        with urllib.request.urlopen(f"{base_url}/status", timeout=1.0):
            return True
    except OSError:
        return False


class _Api:
    """프런트 헤더의 ✕ 버튼용 — 브라우저 JS로는 네이티브 창을 못 닫는다."""

    def __init__(self) -> None:
        self.window = None

    def close(self) -> None:
        if self.window is not None:
            self.window.destroy()


def _control_port() -> int:
    """config.yaml의 control.port — 못 읽으면 기본 8765 (GUI는 데몬 없이도 떠야 한다)."""
    try:
        from navi.config import load_config

        return load_config().control.port
    except Exception:
        return 8765


def main() -> None:
    parser = argparse.ArgumentParser(prog="navi-gui", description="나비 GUI (pywebview)")
    parser.add_argument("--port", type=int, help="컨트롤 플레인 포트 (기본: config control.port)")
    args = parser.parse_args()

    import webview  # 무거운 import는 인자 파싱 뒤에

    base_url = f"http://127.0.0.1:{args.port or _control_port()}"
    up = _daemon_up(base_url)
    api = _Api()
    window = webview.create_window(
        "나비",
        url=base_url if up else None,
        html=None if up else _WAITING_HTML,
        js_api=api,
        width=360,
        height=640,
        resizable=False,
    )
    api.window = window

    def wait_for_daemon() -> None:
        while True:
            time.sleep(1.0)
            if _daemon_up(base_url):
                window.load_url(base_url)
                return

    if not up:
        threading.Thread(target=wait_for_daemon, daemon=True).start()
    webview.start()


if __name__ == "__main__":
    main()
