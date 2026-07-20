"""런처 argv 조립 (E6-4 부품 2).

Popen 자체는 프로세스를 띄워 테스트하기 어렵지만, "엔진 → run_navi.ps1 인자"는 순수
매핑이라 떼어내 검증한다. 핵심 계약: 목소리 엔진은 -Persona로 넘기고(엔진은 카드가
정하므로 -Mouth 안 씀), 목소리 없이는 -Mode text다.
"""

from pathlib import Path

from navi.gui.__main__ import build_launch_argv

_SCRIPT = Path("scripts/run_navi.ps1")


def _argv(engine, persona):
    return build_launch_argv(_SCRIPT, engine, persona)


def test_voice_engine_passes_persona():
    """gptsovits·supertonic은 -Persona로 — 엔진은 그 카드가 정한다(-Mouth 없음)."""
    argv = _argv("supertonic", "navi")
    assert "-Persona" in argv and argv[argv.index("-Persona") + 1] == "navi"
    assert "-Mouth" not in argv
    assert "-Mode" not in argv  # 목소리 엔진은 run_navi 기본 Mode(voice)


def test_text_only_uses_mode_text_not_persona():
    """목소리 없이는 -Mode text — persona가 있어도 무시(축이 다르다)."""
    argv = _argv("none", None)
    assert "-Mode" in argv and argv[argv.index("-Mode") + 1] == "text"
    assert "-Persona" not in argv


def test_script_is_invoked_via_file_with_bypass():
    """-File로 스크립트를 부르고 Restricted 정책을 이번 실행만 통과(-ExecutionPolicy Bypass)."""
    argv = _argv("supertonic", "navi")
    assert argv[0] == "powershell.exe"
    assert "-ExecutionPolicy" in argv and "Bypass" in argv
    assert "-File" in argv and argv[argv.index("-File") + 1] == str(_SCRIPT)


def test_persona_omitted_when_none_for_voice_engine():
    """부팅 가능한 카드가 없어 persona=None이면 -Persona를 안 붙인다(빈 값 전달 방지)."""
    argv = _argv("gptsovits", None)
    assert "-Persona" not in argv
