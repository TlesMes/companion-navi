"""웨이크워드 계약 검증 — Porcupine 없이 고정한다 (arch 4.1 · D7).

실 엔진(PorcupineWakeWord)은 access_key·.ppn·pvporcupine가 필요해 단위 테스트 밖 — 키 없이
검증 가능한 것만: PCM→int16 변환(가장 버그나기 쉬운 부분), FakeWakeWord 트리거, 팩토리 분기.
PorcupineWakeWord는 지연 임포트라 pvporcupine 미설치에서도 모듈 임포트는 깨지지 않아야 한다.
"""

import struct

import pytest

from navi.ear import FakeWakeWord, WakeWord, create_wakeword
from navi.ear.wakeword import (
    PorcupineWakeWord,
    VoskWakeWord,
    _pcm_to_samples,
    _strip_spaces,
)
from navi.models import AudioChunk

SR = 16000


def _frame(samples: list[int]) -> AudioChunk:
    pcm = b"".join(struct.pack("<h", s) for s in samples)
    return AudioChunk(pcm=pcm, sample_rate=SR)


# --- PCM→int16 변환 ---


def test_pcm_to_samples_roundtrip():
    samples = [0, 1, -1, 12345, -12345, 32767, -32768]
    out = _pcm_to_samples(_frame(samples).pcm)
    assert list(out) == samples


def test_pcm_to_samples_drops_odd_trailing_byte():
    # 홀수 바이트(3) → 마지막 1바이트는 버리고 1샘플만
    out = _pcm_to_samples(b"\x01\x00\x7f")
    assert list(out) == [1]


def test_pcm_to_samples_is_little_endian():
    # 0x0100 LE = 1 — 빅엔디안 머신에서도 동일 결과여야(byteswap이 보정)
    assert list(_pcm_to_samples(b"\x01\x00")) == [1]


# --- FakeWakeWord ---


def test_fake_detect_at_fires_on_nth_call():
    ww = FakeWakeWord(detect_at=3)
    chunk = _frame([0])
    assert [ww.detect(chunk) for _ in range(5)] == [False, False, True, False, False]


def test_fake_trigger_callback_takes_priority():
    # trigger가 있으면 detect_at 무시하고 콜백 결과를 따른다
    ww = FakeWakeWord(detect_at=1, trigger=lambda c: c.sample_rate == SR)
    assert ww.detect(_frame([0])) is True
    assert ww.detect(AudioChunk(pcm=b"", sample_rate=8000)) is False


def test_fake_no_trigger_never_fires():
    ww = FakeWakeWord()
    assert not any(ww.detect(_frame([0])) for _ in range(10))


def test_fake_declares_frame_length_and_sample_rate():
    ww = FakeWakeWord(frame_length=512, sample_rate=SR)
    assert ww.frame_length == 512
    assert ww.sample_rate == SR
    assert isinstance(ww, WakeWord)


def test_fake_close_is_noop():
    FakeWakeWord().close()  # 예외 없이 통과


# --- 팩토리 ---


def test_create_wakeword_fake():
    ww = create_wakeword("fake", detect_at=1)
    assert isinstance(ww, FakeWakeWord)
    assert isinstance(ww, WakeWord)


def test_create_wakeword_unknown_raises():
    with pytest.raises(ValueError):
        create_wakeword("nope")


def test_create_wakeword_porcupine_lazy_imports_engine():
    # pvporcupine 미설치 환경: 인스턴스화 시점에야 import 시도(지연 임포트) →
    # 모듈/팩토리 임포트 자체는 깨지지 않고, create 호출에서만 ImportError가 난다.
    try:
        import pvporcupine  # noqa: F401
    except ImportError:
        with pytest.raises(ImportError):
            create_wakeword(
                "porcupine", access_key="x", keyword_path="x.ppn"
            )
    else:
        pytest.skip("pvporcupine 설치됨 — 실 키 없이 create는 검증하지 않는다")


def test_porcupine_is_wakeword_subclass():
    # 인스턴스화 없이 계약 상속만 확인(추상 메서드 구현 여부)
    assert issubclass(PorcupineWakeWord, WakeWord)


# --- Vosk (채택 엔진) ---


def test_strip_spaces_absorbs_korean_word_boundaries():
    # KWS/STT의 들쭉날쭉한 띄어쓰기를 흡수 — "야 일어나"도 "야일어나"도 같은 키로
    assert _strip_spaces("야 일어나") == _strip_spaces("야일어나") == "야일어나"


def test_create_wakeword_vosk_lazy_imports_engine():
    # vosk 미설치 환경: 인스턴스화 시점에야 import 시도(지연 임포트) → 팩토리 임포트는 안 깨짐.
    try:
        import vosk  # noqa: F401
    except ImportError:
        with pytest.raises(ImportError):
            create_wakeword("vosk", model_path="x", keywords=["야 일어나"])
    else:
        pytest.skip("vosk 설치됨 — 실 모델 없이 create는 검증하지 않는다")


def test_vosk_is_wakeword_subclass():
    assert issubclass(VoskWakeWord, WakeWord)
