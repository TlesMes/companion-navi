"""VAD(음성 활동 감지) 계약 + 에너지 기반 구현 (arch 4.1 입력 깔때기).

프레임 1개가 "사람 목소리인가"를 판정한다. 벤더(silero·webrtc…)는 이 계약 뒤에 숨는다 —
무엇으로 갈아끼든 Endpointer는 is_speech(bool)로만 대화한다(벤더 종속 설계 금지).

EnergyVad는 외부 의존 없는 1순위 구현 — 조용한 방에서 RMS 임계로 발화/침묵을 가른다.
정밀도가 필요해지면 같은 Vad 계약 뒤에서 silero-vad 등으로 교체한다(임계 튜닝은 D12 영역).
"""

from __future__ import annotations

import array
import math
import sys
from abc import ABC, abstractmethod

from navi.models import AudioChunk


class Vad(ABC):
    @abstractmethod
    def is_speech(self, chunk: AudioChunk) -> bool:
        """이 프레임이 사람 목소리면 True. 프레임 길이는 호출부(Endpointer)가 고정한다."""


def _rms(pcm: bytes) -> float:
    """16-bit PCM 프레임의 RMS 진폭(0~32767). 외부 의존 없이 stdlib array로 계산."""
    if not pcm:
        return 0.0
    samples = array.array("h")
    samples.frombytes(pcm[: len(pcm) // 2 * 2])  # 홀수 바이트 방어
    if not samples:
        return 0.0
    if sys.byteorder == "big":  # PCM은 little-endian — 빅엔디안 머신에서만 뒤집는다
        samples.byteswap()
    return math.sqrt(sum(s * s for s in samples) / len(samples))


class EnergyVad(Vad):
    """RMS 에너지 임계 VAD. threshold 위면 음성으로 본다.

    threshold는 마이크 게인·소음 환경에 따라 달라지는 튜닝값(D12). 기본 500은 조용한
    실내 근접 마이크 기준 보수적 값 — 생활소음을 음성으로 오인하면 올린다.
    """

    def __init__(self, threshold: float = 500.0) -> None:
        self._threshold = threshold

    def is_speech(self, chunk: AudioChunk) -> bool:
        return _rms(chunk.pcm) >= self._threshold
