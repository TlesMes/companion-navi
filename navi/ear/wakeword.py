"""웨이크워드 계약 + 어댑터 (arch 4.1 입력 깔때기 · D7).

호출어("야 일어나")를 잡아 청취축(마이크→STT→LLM 문)을 여는 입구다(arch 5.1·D16). 벤더는
이 계약 뒤에 숨는다 — 청취 루프는 detect(bool)로만 대화한다(Vad와 동일 규약, 벤더 종속 금지).

호출어 감지에는 두 갈래가 있고, 계약은 둘 다 수용한다:
  - **음향 KWS** (PorcupineWakeWord): 파형 → "호출어 점수" 직접 출력. 텍스트 없음. SLEEP에서
    ASR을 안 돌리는 진짜 spotting. 단 Porcupine은 콘솔 가입(회사 이메일) 장벽으로 보류.
  - **ASR 기반 스팟팅** (VoskWakeWord, 채택): 작은 ASR로 전사 → 그 텍스트에서 호출어를 찾는다.
    텍스트가 나온다(검문①과 같은 방식, 모델만 경량). 따라서 채택안에서 SLEEP은 'STT 꺼짐'이
    아니라 '무거운 STT(whisper) 꺼짐 + 경량 ASR(Vosk) 켜짐'의 2단이다.

프레임 크기는 어댑터에서 슬라이싱하지 않고 통일한다: 엔진이 frame_length(샘플 수)를 선언하면
마이크 blocksize·Endpointer frame_ms를 거기 맞춘다. 우리 1순위 VAD(EnergyVad)는 순수 RMS라
프레임 크기를 안 가리므로, 같은 프레임을 WakeWord·Endpointer가 함께 소비한다.
"""

from __future__ import annotations

import array
import json
import logging
import sys
from abc import ABC, abstractmethod
from collections.abc import Callable

from navi.models import AudioChunk

log = logging.getLogger(__name__)


def _strip_spaces(text: str) -> str:
    """공백 제거 — STT/KWS의 들쭉날쭉한 한국어 단어 경계를 흡수(gatekeeper와 동일 취지)."""
    return "".join(text.split())


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


class VoskWakeWord(WakeWord):
    """Vosk(Kaldi) 키워드 스팟팅 — D7 채택 엔진. 학습·키·계정 불필요, 순수 CPU.

    전체 인식 + 호출어 포함 매칭으로 호출어를 잡는다 — 우리 검문①(작은 인식+텍스트 매칭)과
    동일 방식. 한국어 모델 디렉터리(model_path)와 호출어 문구(keywords)만 있으면 된다.

    왜 grammar 제한이 아니라 전체 인식인가: Vosk grammar의 `[unk]`(비호출어 흡수용 토큰)를
    small-ko 모델이 어휘에 안 갖고 있어 무시된다 → grammar가 호출어 하나로 좁혀져 아무 말이나
    호출어로 강제 매칭(오수락 폭주)된다. 전체 인식은 임의 발화를 정상 전사하고 그 안에서 호출어를
    찾으므로 이 함정이 없다. 비호출어 발화의 전사는 그냥 버린다(SLEEP에선 LLM으로 안 보냄).

    부분결과(partial)까지 본다: 발화 끝(AcceptWaveform=True)을 기다리지 않고, 말하는 도중
    인식 가설에 호출어가 뜨는 순간 잡는다 → recall↑·지연↓(발화가 깔끔히 안 끊겨도 잡힘).
    매칭 후 Reset으로 같은 부분결과의 연속 재발화를 막는다. 전사는 DEBUG 로그로 남겨 -vv로
    호출어가 실제 뭘로 들리는지 보고 keywords 변이형을 채울 수 있게 한다(인식률 튜닝).

    vosk는 .venv-voice에만 설치되므로 지연 임포트한다(sounddevice·STT와 동일 규약).
    """

    def __init__(
        self,
        *,
        model_path: str,
        keywords: list[str] | tuple[str, ...],
        sample_rate: int = 16000,
        frame_length: int = 512,
    ) -> None:
        from vosk import KaldiRecognizer, Model

        if not keywords:
            raise ValueError("VoskWakeWord: keywords가 비어 있습니다")
        self._sr = sample_rate
        self._frame_length = frame_length
        self._targets = tuple(_strip_spaces(k) for k in keywords)
        self._rec = KaldiRecognizer(Model(model_path), sample_rate)

    @property
    def frame_length(self) -> int:
        return self._frame_length

    @property
    def sample_rate(self) -> int:
        return self._sr

    def detect(self, chunk: AudioChunk) -> bool:
        if self._rec.AcceptWaveform(chunk.pcm):  # 발화 한 구간 종료 → 최종 전사
            text = _strip_spaces(json.loads(self._rec.Result()).get("text", ""))
            if text:
                log.debug("Vosk 전사: %r", text)  # 튜닝용 — keywords 변이형 채우기
        else:  # 발화 중 — 부분 가설에서 미리 잡는다
            text = _strip_spaces(json.loads(self._rec.PartialResult()).get("partial", ""))
        if text and any(t in text for t in self._targets):
            log.debug("호출어 일치: %r", text)
            self._rec.Reset()  # 같은 부분결과로 다음 프레임에 또 발화하지 않게 초기화
            return True
        return False


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
