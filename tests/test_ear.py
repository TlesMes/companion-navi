"""Ear 계약 검증 — VAD 판정과 발화 경계 판정을 마이크 없이 고정한다 (arch 4.1·4.2).

마이크 I/O(mic.py)는 하드웨어가 필요해 단위 테스트 밖 — 판정 로직(endpointer)만 검증한다.
"""

import pytest

from navi.ear import Endpointer, EnergyVad, Utterance, create_vad
from navi.ear.vad import Vad
from navi.models import AudioChunk

SR = 16000
FRAME_MS = 20
FRAME_SAMPLES = SR * FRAME_MS // 1000  # 320


def _frame(amplitude: int) -> AudioChunk:
    """진폭 일정한 16-bit PCM 프레임 1개 (amplitude=0이면 무음)."""
    import struct

    pcm = struct.pack("<h", amplitude) * FRAME_SAMPLES
    return AudioChunk(pcm=pcm, sample_rate=SR)


SPEECH = _frame(10000)  # RMS 10000 — 발화
SILENCE = _frame(0)  # RMS 0 — 침묵


# --- EnergyVad (4.1) ---


def test_energyvad_classifies_loud_as_speech_silent_as_not():
    vad = EnergyVad(threshold=500)
    assert vad.is_speech(SPEECH)
    assert not vad.is_speech(SILENCE)


def test_energyvad_threshold_boundary():
    # 진폭 300 < 기본 500 → 침묵으로 본다(생활소음 거르기)
    assert not EnergyVad(threshold=500).is_speech(_frame(300))
    assert EnergyVad(threshold=200).is_speech(_frame(300))


# --- Endpointer (4.2 전방경로) ---


def _endpointer(**kw) -> Endpointer:
    # start=40ms(2프레임), endpoint=60ms(3프레임), preroll=40ms(2프레임)
    return Endpointer(
        EnergyVad(threshold=500),
        frame_ms=FRAME_MS,
        start_speech_ms=40,
        endpoint_silence_ms=60,
        preroll_ms=40,
        **kw,
    )


def _push_all(ep: Endpointer, frames: list[AudioChunk]) -> list[Utterance]:
    return [u for f in frames if (u := ep.push(f)) is not None]


def test_endpointer_emits_utterance_after_trailing_silence():
    ep = _endpointer()
    # 음성 4프레임 → 침묵 3프레임(=endpoint)에서 발화 종료
    frames = [SPEECH] * 4 + [SILENCE] * 3
    utts = _push_all(ep, frames)
    assert len(utts) == 1
    # 발화엔 음성 프레임이 들어있다(preroll 포함, 트레일링 침묵도 일부 포함될 수 있음)
    assert utts[0].pcm.count(b"\x00") < len(utts[0].pcm)  # 전부 무음은 아님


def test_endpointer_ignores_short_blip_below_start_threshold():
    ep = _endpointer()
    # 음성 1프레임(<start 2프레임)뿐 → 발화 시작 안 됨 → 종료 이벤트 없음
    frames = [SILENCE, SPEECH, SILENCE, SILENCE, SILENCE]
    assert _push_all(ep, frames) == []


def test_endpointer_resets_for_next_utterance():
    ep = _endpointer()
    first = _push_all(ep, [SPEECH] * 3 + [SILENCE] * 3)
    second = _push_all(ep, [SPEECH] * 3 + [SILENCE] * 3)
    assert len(first) == 1 and len(second) == 1


def test_endpointer_mid_utterance_silence_does_not_end_early():
    ep = _endpointer()
    # 음성 — 짧은 침묵(2<3) — 다시 음성: 한 발화로 이어진다
    frames = [SPEECH] * 3 + [SILENCE] * 2 + [SPEECH] * 3 + [SILENCE] * 3
    utts = _push_all(ep, frames)
    assert len(utts) == 1


def test_utterance_pcm_and_duration():
    utt = Utterance(chunks=[SPEECH, SPEECH])
    assert utt.pcm == SPEECH.pcm * 2
    assert utt.sample_rate == SR
    assert utt.duration_ms == pytest.approx(FRAME_MS * 2)


# --- 팩토리 ---


def test_create_vad_default_is_energy():
    assert isinstance(create_vad(), EnergyVad)
    assert isinstance(create_vad(), Vad)


def test_create_vad_pending_kinds_raise():
    with pytest.raises(NotImplementedError):
        create_vad("silero")
    with pytest.raises(ValueError):
        create_vad("nope")
