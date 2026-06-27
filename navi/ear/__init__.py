"""귀(Ear) — 마이크 입력을 발화 단위로 잘라 후단(STT)에 넘긴다 (arch 4.1·4.2 전방경로).

구성: Vad(프레임 음성 판정) → Endpointer(발화 경계) → MicListener(I/O 글루).
호출어는 WakeWord(파형에서 호출어 감지) — SLEEP에서 청취축을 여는 입구(D7).
어댑터 교체는 create_vad·create_wakeword 한 줄 — 벤더는 같은 계약 뒤에서 갈아끼운다.

범위: 듣기 전방경로 + 웨이크워드 계약·Porcupine 어댑터. AEC·barge-in은 미구현.
"""

from __future__ import annotations

from navi.ear.endpointer import Endpointer, Utterance
from navi.ear.listening import EventKind, ListenEvent, ListenSession, SleepReason
from navi.ear.vad import EnergyVad, Vad
from navi.ear.wakeword import (
    FakeWakeWord,
    OpenWakeWordWakeWord,
    PorcupineWakeWord,
    VoskWakeWord,
    WakeWord,
)

__all__ = [
    "Vad",
    "EnergyVad",
    "Endpointer",
    "Utterance",
    "create_vad",
    "WakeWord",
    "OpenWakeWordWakeWord",
    "VoskWakeWord",
    "PorcupineWakeWord",
    "FakeWakeWord",
    "create_wakeword",
    "ListenSession",
    "ListenEvent",
    "EventKind",
    "SleepReason",
]


def create_vad(kind: str = "energy", **kwargs) -> Vad:
    """VAD 어댑터 팩토리 — 벤더 종속 금지. 기본은 무의존 EnergyVad."""
    if kind == "energy":
        return EnergyVad(**kwargs)
    if kind in ("silero", "webrtc"):
        raise NotImplementedError(
            f"VAD {kind!r}는 정밀도 필요 시 도입합니다 — 현재는 무의존 'energy'로 루프를 닫습니다."
        )
    raise ValueError(f"알 수 없는 vad kind: {kind!r} (energy | silero | webrtc)")


def create_wakeword(kind: str = "openwakeword", **kwargs) -> WakeWord:
    """웨이크워드 어댑터 팩토리 — 벤더 종속 금지.

    openwakeword(채택): 계정·키 없는 오프라인 음향 KWS(.onnx). vosk: ASR 기반 스팟팅(한국어 약함).
    porcupine: 보존(무료 종료·가입 장벽). fake: 무의존(테스트).
    """
    if kind == "openwakeword":
        return OpenWakeWordWakeWord(**kwargs)
    if kind == "vosk":
        return VoskWakeWord(**kwargs)
    if kind == "porcupine":
        return PorcupineWakeWord(**kwargs)
    if kind == "fake":
        return FakeWakeWord(**kwargs)
    raise ValueError(
        f"알 수 없는 wakeword kind: {kind!r} (openwakeword | vosk | porcupine | fake)"
    )
