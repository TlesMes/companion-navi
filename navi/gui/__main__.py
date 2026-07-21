"""pywebview 창 기동 — 데몬이 없으면 **런처**를 띄운다 (E6-4).

GUI는 오디오를 만지지 않는다(gui.md) — 의존성은 pywebview 하나, 기본 venv에서 돈다.
데몬이 떠 있으면 창은 컨트롤 플레인(127.0.0.1:{port})의 GET / 를 열고, 상태·제어는 전부
프런트(index.html)가 같은 오리진 API/WS로 처리한다.

데몬이 **없으면** 죽은 대기 화면 대신 런처(static/waiting.html)를 띄운다: preflight(순수 판정,
base venv에서 돎)로 "이 머신에서 뭘 띄울 수 있나"를 그리고, 엔진 칩을 클릭하면 데몬을
**독립 프로세스로** 기동한다. GUI는 데몬을 소유하지 않는다(detached·DEVNULL·핸들 폐기) —
GUI가 죽어도 나비는 산다. 기동 뒤엔 기존 폴러가 데몬을 감지해 실제 GUI로 갈아끼운다.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import threading
import time
import urllib.request
from pathlib import Path

# Windows 백그라운드 기동 플래그. 목표: 창 없이 뜨고, GUI가 죽어도 데몬은 산다.
# **DETACHED_PROCESS가 아니라 CREATE_NO_WINDOW다** — DETACHED는 콘솔을 아예 없애서
# run_navi.ps1의 Write-Host(콘솔 호스트 필요)가 ErrorActionPreference=Stop과 만나 데몬
# 실행 전에 스크립트가 조용히 죽는다(실측: powershell exit 0, 데몬 안 뜸). CREATE_NO_WINDOW는
# 숨은 콘솔을 주므로 호스트가 살아 스크립트가 정상 실행된다. 부모(GUI) 사망 시 자식 생존은
# Windows 기본 거동이라(잡 오브젝트 아님) DETACHED 없이도 성립한다.
# NEW_PROCESS_GROUP: GUI 콘솔의 Ctrl+C가 데몬으로 전파되지 않게(신호 격리).
_CREATE_NO_WINDOW = 0x08000000
_CREATE_NEW_PROCESS_GROUP = 0x00000200

# 클릭한 엔진이 이 시각까지 안 뜨면 부팅 실패로 본다(웜업 상한). gptsovits TTS 웜업이
# 수십 초라 넉넉히. 상한이지 대기값이 아니다 — 데몬은 대개 더 빨리 뜬다.
_LAUNCH_DEADLINE_S = 90.0


def _daemon_up(base_url: str) -> bool:
    try:
        with urllib.request.urlopen(f"{base_url}/status", timeout=1.0):
            return True
    except OSError:
        return False


def _waiting_html() -> str:
    """런처 화면 HTML — pre-daemon 단계라 서버가 없어 파일을 읽어 직접 주입한다."""
    return (Path(__file__).parent / "static" / "waiting.html").read_text(encoding="utf-8")


def build_launch_argv(script: Path, engine: str, persona: str | None) -> list[str]:
    """엔진 선택 → run_navi.ps1 argv. 순수 함수(테스트 가능) — Popen과 분리한다.

    엔진은 카드가 정하므로(-Mouth 안 씀) 목소리 엔진은 `-Persona`로 넘긴다. 목소리 없이는
    `-Mode text`(persona 없음). GUI가 아는 건 스크립트 파일명과 preflight가 준 라벨뿐 —
    venv·그 밖의 인자는 스크립트가 소유한다(gui.md).
    """
    argv = [
        "powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass",
        "-File", str(script),
    ]
    if engine == "none":
        argv += ["-Mode", "text"]
    elif persona:
        argv += ["-Persona", persona]
    return argv


class _Api:
    """프런트(런처·실제 GUI 공통)의 JS 브리지.

    창 참조는 반드시 비공개(_window)로 든다: pywebview는 js_api의 공개 속성을 재귀
    직렬화해 JS 브리지를 만드는데, Window.native(WinForms)가 걸리면 무한 재귀로 메인
    스레드가 멎는다(실측). launch가 무장하는 데드라인(_launch_at)도 폴러와 공유하는 상태다.
    """

    def __init__(self, root: Path, base_url: str) -> None:
        self._window = None
        self._root = root
        self._base_url = base_url
        self._launch_at: float | None = None  # 클릭 시각 — 폴러가 데드라인 판정에 쓴다
        self._polling = False                  # 폴러 중복 기동 방지

    # --- 창 조작 (frameless라 OS 제목줄이 없다) --------------------------

    def close(self) -> None:
        if self._window is not None:
            self._window.destroy()

    def minimize(self) -> None:
        if self._window is not None:
            self._window.minimize()

    # --- 폴러 (유일한 피드백 경로, gui.md 양보 불가) --------------------

    def _poll_loop(self) -> None:
        """데몬이 뜨면 실제 GUI로 갈아끼운다. launch 데드라인 초과 시 화면에 안내한다.

        새 폴러를 만들지 않고 이 한 루프가 '전환'과 '실패 안내'를 겸한다. 시작 시 한 번,
        종료 후 런처 복귀 시 한 번 — 같은 로직을 재사용한다(return_to_launcher).
        """
        notified_timeout = False
        while True:
            time.sleep(1.0)
            if _daemon_up(self._base_url):
                self._window.load_url(self._base_url)
                self._polling = False
                return
            launched = self._launch_at
            if (
                launched is not None
                and not notified_timeout
                and time.monotonic() - launched > _LAUNCH_DEADLINE_S
            ):
                notified_timeout = True
                self._launch_at = None  # 데드라인 해제 — 재시도를 위해 칩 재활성
                self._window.evaluate_js("window.naviLaunchTimedOut && window.naviLaunchTimedOut()")

    def start_poller(self) -> None:
        if self._polling:
            return
        self._polling = True
        threading.Thread(target=self._poll_loop, daemon=True).start()

    def return_to_launcher(self) -> None:
        """index.html의 종료 버튼이 부른다 — 대기 화면(런처)으로 되돌려 다른 엔진을 고를 수 있게.

        죽어가는 데몬이 잠깐 더 /status에 답할 수 있어, 바로 폴러를 켜면 그 死者로 되튄다.
        그래서 데몬이 **완전히 내려간 뒤** 폴러를 무장한다(별 스레드에서 대기 — 브리지 블록 금지).

        load_html은 이 함수 반환 *뒤*로 한 틱 늦춘다: pywebview는 이 함수가 끝난 뒤 원래
        페이지의 JS 콜백에 반환값을 돌려주려 하는데, 여기서 바로 페이지를 갈아끼우면 그
        콜백이 사라진 채라 매 종료마다 "callback is not a function"이 뜬다(무해하지만
        정상 경로 로그 오염 — util.py의 js_bridge_call 참고).
        """
        self._launch_at = None
        threading.Timer(0.05, self._window.load_html, args=(_waiting_html(),)).start()
        threading.Thread(target=self._resume_after_shutdown, daemon=True).start()

    def _resume_after_shutdown(self) -> None:
        for _ in range(15):
            if not _daemon_up(self._base_url):
                break
            time.sleep(1.0)
        self.start_poller()

    # --- 런처 -----------------------------------------------------------

    def preflight(self) -> str:
        """이 머신에서 뭘 띄울 수 있나 — 런처 화면이 로드 시 호출해 칩을 그린다.

        JSON 문자열로 넘긴다(pywebview 브리지는 dataclass를 모른다). 순수 판정이라
        base venv에서 돈다(torch 불요).
        """
        from navi.preflight import evaluate

        report = evaluate(self._root)
        return json.dumps(report.to_dict(), ensure_ascii=False)

    def launch(self, engine: str, persona: str | None) -> dict:
        """엔진을 골라 데몬을 독립 프로세스로 기동. 성공 여부만 반환하고 손을 뗀다.

        Popen을 즉시 폐기한다 — `.wait()`도 파이프도 없다(파이프가 차면 데몬이 막히고,
        로그는 logs/navi.log에 이미 있다). 기동 성공/실패의 관측은 기존 폴러가 데몬
        `/status`로 한다(새 폴러 금지). 여기선 데드라인만 무장한다.
        """
        script = self._root / "scripts" / "run_navi.ps1"
        if not script.is_file():
            return {"ok": False, "reason": f"실행 스크립트가 없어요: {script.name}"}
        argv = build_launch_argv(script, engine, persona)
        try:
            subprocess.Popen(  # noqa: S603 — argv 고정, 사용자 입력 셸 해석 없음
                argv,
                creationflags=_CREATE_NO_WINDOW | _CREATE_NEW_PROCESS_GROUP,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                cwd=str(self._root),
                close_fds=True,
            )  # Popen 객체를 붙잡지 않는다 — fire and forget
        except OSError as exc:
            return {"ok": False, "reason": f"기동 실패: {exc}"}
        self._launch_at = time.monotonic()
        return {"ok": True}


def _control_port() -> int:
    """config.yaml의 control.port — 못 읽으면 기본 8765 (GUI는 데몬 없이도 떠야 한다).

    정수 하나 때문에 load_config를 부르지 않는다 — 그쪽은 .env 로드와 페르소나 카드
    파싱까지 딸려 와서, 카드가 깨져 있으면 포트만 원하는 이 호출이 경고를 뱉는다.
    (preflight는 그 파싱이 목적이라 별개 — 런처 칩을 그릴 때만 부른다.)
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

    root = Path.cwd()
    base_url = f"http://127.0.0.1:{args.port or _control_port()}"
    up = _daemon_up(base_url)
    api = _Api(root, base_url)
    # frameless — OS 제목줄 없이 앱 헤더가 제목줄 역할(드래그 영역은 프런트가
    # pywebview-drag-region 클래스로 지정, 창 조작은 js_api로).
    window = webview.create_window(
        "나비",
        url=base_url if up else None,
        html=None if up else _waiting_html(),
        js_api=api,
        width=360,
        height=460,  # 런처 칩 3개 + 상태 캡션 여유. 실제 GUI로 전환되면 그쪽 높이를 따른다.
        resizable=False,
        frameless=True,
        easy_drag=False,
    )
    api._window = window

    # 데몬이 아직 없으면 런처를 띄운 채 폴러를 무장한다. 이미 떠 있으면 실제 GUI로
    # 바로 들어갔으므로 폴러는 종료 후 복귀(return_to_launcher) 때 시작된다.
    if not up:
        api.start_poller()
    webview.start()


if __name__ == "__main__":
    main()
