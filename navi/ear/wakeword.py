"""웨이크워드(KWS) 계약 + Porcupine 구현 (arch 4.1 입력 깔때기 · D7).

파형에서 호출어("야 일어나")를 직접 잡는다 — STT가 꺼진 SLEEP에선 텍스트가 없어 KWS만이
청취축(마이크→STT→LLM 문)을 여는 입구다(arch 5.1·D16). 벤더(Porcupine·openWakeWord…)는
이 계약 뒤에 숨는다 — 청취 루프는 detect(bool)로만 대화한다(Vad와 동일 규약, 벤더 종속 금지).

프레임 크기는 어댑터에서 슬라이싱하지 않고 통일한다: 엔진이 frame_length(샘플 수)를 선언하면
마이크 blocksize·Endpointer frame_ms를 거기 맞춘다. 우리 1순위 VAD(EnergyVad)는 순수 RMS라
프레임 크기를 안 가리므로, 같은 프레임을 WakeWord·Endpointer가 함께 소비한다.
"""

from __future__ import annotations

import array
import sys
from abc import ABC, abstractmethod
from collections.abc import Callable

from navi.models import AudioChunk


def _pcm_to_samples(pcm: bytes) -> array.array:
    """16-bit PCM 바이트를 int16 샘플 배열로 — Porcupine.process가 요구하는 형태.

    vad._rms와 동일 규약: 홀수 바이트 방어 + 리틀엔디안 고정(빅엔디안 머신만 뒤집는다).
    """
    samples = array.array("h")
    samples.frombytes(pcm[: len(pcm) // 2 * 2])
    if sys.byteorder == "big":  # PCM은 little-endian
        samples.byteswap()
    return samples


class WakeWord(ABC):
    """호출어 감지 계약. detect 1회는 frame_length 샘플 1프레임을 받는다(호출부가 크기 고정)."""

    @property
    @abstractmethod
    def frame_length(self) -> int:
        """detect 1회에 요구하는 샘플 수 — 마이크 blocksize·Endpointer frame_ms가 이걸 따른다."""

    @property
    @abstractmethod
    def sample_rate(self) -> int:
        """엔진이 요구하는 샘플레이트(Hz)."""

    @abstractmethod
    def detect(self, chunk: AudioChunk) -> bool:
        """이 프레임에서 호출어가 잡혔으면 True."""

    def close(self) -> None:
        """엔진 리소스 해제 — 기본 no-op, 네이티브 핸들을 쥔 어댑터만 재정의."""


class PorcupineWakeWord(WakeWord):
    """Picovoice Porcupine 어댑터 — 온디바이스 KWS, 한국어 키워드 내장(D7).

    한국어 키워드는 .ppn(keyword_path)과 한국어 모델(model_path=porcupine_params_ko.pv)을 둘 다
    요구한다. access_key·키워드 파일은 비밀 — 경로로 주입하고 커밋하지 않는다.

    pvporcupine는 .venv-voice에만 설치되므로 지연 임포트한다 — 기본 .venv에서 모듈 임포트·
    유닛테스트가 깨지지 않게(sounddevice·STT 어댑터와 동일 규약).
    """

    def __init__(
        self,
        *,
        access_key: str,
        keyword_path: str,
        model_path: str | None = None,
        sensitivity: float = 0.5,
    ) -> None:
        import pvporcupine

        kwargs: dict = {
            "access_key": access_key,
            "keyword_paths": [keyword_path],
            "sensitivities": [sensitivity],
        }
        if model_path:  # 한국어 등 비영어 키워드는 언어 모델 파일 필수
            kwargs["model_path"] = model_path
        self._engine = pvporcupine.create(**kwargs)

    @property
    def frame_length(self) -> int:
        return self._engine.frame_length

    @property
    def sample_rate(self) -> int:
        return self._engine.sample_rate

    def detect(self, chunk: AudioChunk) -> bool:
        # process는 키워드 인덱스(>=0 감지, -1 미감지)를 반환. 프레임 길이는 호출부가 보장.
        return self._engine.process(_pcm_to_samples(chunk.pcm)) >= 0

    def close(self) -> None:
        self._engine.delete()


class FakeWakeWord(WakeWord):
    """외부 의존 0 — 테스트·키 없는 환경용. detect_at(N번째 호출) 또는 trigger(콜백)로 발화.

    상태머신 유닛테스트의 트리거: trigger가 있으면 우선, 없으면 detect_at번째 detect에서 True.
    """

    def __init__(
        self,
        *,
        detect_at: int | None = None,
        trigger: Callable[[AudioChunk], bool] | None = None,
        frame_length: int = 512,
        sample_rate: int = 16000,
    ) -> None:
        self._detect_at = detect_at
        self._trigger = trigger
        self._frame_length = frame_length
        self._sample_rate = sample_rate
        self._calls = 0

    @property
    def frame_length(self) -> int:
        return self._frame_length

    @property
    def sample_rate(self) -> int:
        return self._sample_rate

    def detect(self, chunk: AudioChunk) -> bool:
        self._calls += 1
        if self._trigger is not None:
            return bool(self._trigger(chunk))
        return self._detect_at is not None and self._calls == self._detect_at
