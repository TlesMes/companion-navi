"""귀(Ear) — 마이크 입력을 발화 단위로 잘라 후단(STT)에 넘긴다 (arch 4.1·4.2 전방경로).

구성: Vad(프레임 음성 판정) → Endpointer(발화 경계) → MicListener(I/O 글루).
VAD 교체는 create_vad 한 줄 — silero·webrtc 등은 같은 Vad 계약 뒤에서 갈아끼운다.

범위: 지금은 듣기 전방경로(말하면 발화 1건을 방출)만. AEC·웨이크워드·barge-in은 미구현.
"""

from __future__ import annotations

from navi.ear.endpointer import Endpointer, Utterance
from navi.ear.vad import EnergyVad, Vad

__all__ = ["Vad", "EnergyVad", "Endpointer", "Utterance", "create_vad"]


def create_vad(kind: str = "energy", **kwargs) -> Vad:
    """VAD 어댑터 팩토리 — 벤더 종속 금지. 기본은 무의존 EnergyVad."""
    if kind == "energy":
        return EnergyVad(**kwargs)
    if kind in ("silero", "webrtc"):
        raise NotImplementedError(
            f"VAD {kind!r}는 정밀도 필요 시 도입합니다 — 현재는 무의존 'energy'로 루프를 닫습니다."
        )
    raise ValueError(f"알 수 없는 vad kind: {kind!r} (energy | silero | webrtc)")
