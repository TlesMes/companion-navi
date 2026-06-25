"""청취축 상태머신 — SLEEP↔ACTIVE를 프레임 스트림 위에서 굴린다 (arch 5.1 · D16 · D7).

청취축(마이크→STT→LLM 문)은 평소 닫혀 있다(SLEEP). SLEEP에선 STT를 끄고 WakeWord로
*파형*에서 호출어만 잡는다. 호출어가 잡히면 ACTIVE로 열려 Endpointer가 발화를 끊어 STT에
넘긴다. ACTIVE는 대화 세션 — 무음이 active_timeout_ms를 넘기거나(타임아웃), 검문①이 수면
명령을 잡으면(request_sleep) SLEEP으로 돌아간다.

마이크 I/O(mic.py)·발화 경계(endpointer.py)와 분리한 순수 오케스트레이션이다 — frames를
주입하면 마이크 없이 단위 테스트가 된다(Endpointer와 동일 규약). now도 주입 가능해 타임아웃을
결정론적으로 검증한다.
"""

from __future__ import annotations

import time
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass
from enum import Enum, auto

from navi.ear.endpointer import Endpointer, Utterance
from navi.ear.vad import EnergyVad, Vad
from navi.ear.wakeword import WakeWord
from navi.models import AudioChunk


class EventKind(Enum):
    WAKE = auto()       # 호출어 감지 → ACTIVE 진입
    UTTERANCE = auto()  # ACTIVE에서 발화 1건 종료
    SLEEP = auto()      # ACTIVE → SLEEP 복귀


class SleepReason(Enum):
    TIMEOUT = auto()  # 무음 타임아웃
    COMMAND = auto()  # 검문① 수면 명령(request_sleep)


@dataclass(frozen=True)
class ListenEvent:
    kind: EventKind
    utterance: Utterance | None = None
    reason: SleepReason | None = None


class ListenSession:
    """웨이크워드로 여닫는 청취축. run(frames)이 이벤트를 yield한다.

    프레임 크기는 WakeWord가 선언한 frame_length를 따른다(마이크 blocksize·Endpointer frame_ms를
    여기서 파생) — 어댑터 내부 재정렬 없이 같은 프레임을 WakeWord·Endpointer가 함께 쓴다.
    """

    def __init__(
        self,
        wakeword: WakeWord,
        *,
        vad: Vad | None = None,
        active_timeout_ms: int = 30000,
        start_speech_ms: int = 200,
        endpoint_silence_ms: int = 800,
        preroll_ms: int = 200,
    ) -> None:
        self._wakeword = wakeword
        self._vad = vad or EnergyVad()
        self._active_timeout_ms = active_timeout_ms
        # 512샘플/16kHz = 32ms. Endpointer 타이밍 나눗셈만 살짝 거칠어질 뿐 무해.
        self._frame_ms = round(1000 * wakeword.frame_length / wakeword.sample_rate)
        self._ep_kwargs = dict(
            frame_ms=self._frame_ms,
            start_speech_ms=start_speech_ms,
            endpoint_silence_ms=endpoint_silence_ms,
            preroll_ms=preroll_ms,
        )
        self._sleep_requested = False

    @property
    def frame_ms(self) -> int:
        return self._frame_ms

    @property
    def sample_rate(self) -> int:
        return self._wakeword.sample_rate

    def request_sleep(self) -> None:
        """소비자(cli)가 검문① SLEEP을 만나면 호출 — 다음 프레임에서 SLEEP으로 돌린다."""
        self._sleep_requested = True

    def _new_endpointer(self) -> Endpointer:
        return Endpointer(self._vad, **self._ep_kwargs)

    async def run(
        self,
        frames: AsyncIterator[AudioChunk],
        *,
        now: Callable[[], float] = time.monotonic,
    ) -> AsyncIterator[ListenEvent]:
        """프레임 스트림을 받아 청취축 이벤트를 yield한다. SLEEP으로 시작한다."""
        awake = False
        endpointer: Endpointer | None = None
        last_activity = 0.0

        async for chunk in frames:
            if not awake:
                # ── SLEEP: 호출어만 청취, STT 미가동 ──
                if self._wakeword.detect(chunk):
                    awake = True
                    self._sleep_requested = False
                    endpointer = self._new_endpointer()  # 깨어날 때마다 깨끗한 상태로
                    last_activity = now()
                    yield ListenEvent(EventKind.WAKE)
                continue

            # ── ACTIVE: 발화 끊어 STT로 ──
            if self._sleep_requested:
                self._sleep_requested = False
                awake = False
                yield ListenEvent(EventKind.SLEEP, reason=SleepReason.COMMAND)
                continue

            utt = endpointer.push(chunk)  # type: ignore[union-attr]
            if utt is not None:
                last_activity = now()
                yield ListenEvent(EventKind.UTTERANCE, utterance=utt)
                continue

            if (now() - last_activity) * 1000 >= self._active_timeout_ms:
                awake = False
                yield ListenEvent(EventKind.SLEEP, reason=SleepReason.TIMEOUT)
