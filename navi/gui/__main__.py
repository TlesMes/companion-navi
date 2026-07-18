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
from pathlib import Path

# 대기 화면 — 프런트(index.html)와 같은 다크 팔레트. 데몬이 뜨면 파이썬 폴러가 갈아끼운다.
_WAITING_HTML = """<!doctype html><html lang="ko"><head><meta charset="utf-8"><style>
body { margin: 0; height: 100vh; display: flex; flex-direction: column; align-items: center;
       justify-content: center; gap: 14px; background: #262521; color: #a8a396;
       font-family: "Malgun Gothic", system-ui, sans-serif; font-size: 13px; user-select: none; }
.bar { position: fixed; top: 0; left: 0; right: 34px; height: 34px; }  /* frameless 드래그 영역 */
.x { position: fixed; top: 0; right: 0; width: 34px; height: 34px; border: 0; cursor: pointer;
     background: transparent; color: #7a756a; font-size: 15px; }
.x:hover { color: #ece9e2; }
.dot { width: 46px; height: 46px; border-radius: 50%; background: #402a20; color: #edad8e;
       display: flex; align-items: center; justify-content: center; font-size: 18px;
       animation: pulse 1.6s ease-in-out infinite; }
@keyframes pulse { 0%,100% { opacity: 0.35; } 50% { opacity: 1; } }
code { font-family: Consolas, monospace; color: #7a756a; font-size: 12px; }
.hint { color: #6b6659; font-size: 11px; margin: -6px 0 0; }
</style></head><body>
<div class="bar pywebview-drag-region"></div>
<button class="x" onclick="pywebview.api.close()">&#10005;</button>
<div class="dot">나</div>
<p>데몬을 기다리는 중…</p>
<code>.\scripts\run_navi.ps1</code>
<p class="hint">음성 모드는 TTS 웜업에 수십 초 걸립니다</p>
</body></html>"""


def _daemon_up(base_url: str) -> bool:
    try:
        with urllib.request.urlopen(f"{base_url}/status", timeout=1.0):
            return True
    except OSError:
        return False


class _Api:
    """프런트 헤더의 ─·✕ 버튼용 — frameless 창이라 OS 제목줄이 없다(브라우저 JS로는 불가).

    창 참조는 반드시 비공개(_window)로 든다: pywebview는 js_api의 공개 속성을
    재귀 직렬화해 JS 브리지를 만드는데, Window.native(WinForms)가 걸리면
    AccessibilityObject.Bounds.Empty… 무한 재귀로 메인 스레드가 멎는다(실측).
    """

    def __init__(self) -> None:
        self._window = None

    def close(self) -> None:
        if self._window is not None:
            self._window.destroy()

    def minimize(self) -> None:
        if self._window is not None:
            self._window.minimize()


def _control_port() -> int:
    """config.yaml의 control.port — 못 읽으면 기본 8765 (GUI는 데몬 없이도 떠야 한다).

    정수 하나 때문에 load_config를 부르지 않는다 — 그쪽은 .env 로드와 페르소나 카드
    파싱까지 딸려 와서, 카드가 깨져 있으면 포트만 원하는 이 호출이 경고를 뱉는다.
    GUI는 오디오도 페르소나도 만지지 않으므로 필요한 키만 직접 읽는다.
    """
    try:
        import yaml

        raw = yaml.safe_load(Path.cwd().joinpath("config.yaml").read_text(encoding="utf-8"))
        return int(raw["control"]["port"])
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
    # frameless — OS 제목줄 없이 앱 헤더가 제목줄 역할(드래그 영역은 프런트가
    # pywebview-drag-region 클래스로 지정, 창 조작은 js_api로).
    window = webview.create_window(
        "나비",
        url=base_url if up else None,
        html=None if up else _WAITING_HTML,
        js_api=api,
        width=360,
        height=420,  # 콘텐츠 실측 ~350px + 취침창 편집 행(~40px) 여유. 패널·로그는 오버레이
        resizable=False,
        frameless=True,
        easy_drag=False,
    )
    api._window = window

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
