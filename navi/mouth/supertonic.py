"""Supertonic 실어댑터 — 두뇌 토큰 스트림을 로컬에서 합성·재생한다 (D3 잠정 TTS).

Supertonic은 배치 엔진이다(전체 텍스트 → wav 한 방). 그대로 쓰면 마지막 토큰까지
기다렸다 말하기 시작해 첫 오디오가 늦는다. 그래서 토큰을 **문장 경계로 끊어** 청크별로
합성하고, 첫 문장이 나오는 즉시 재생을 시작한다 — 목표는 첫 오디오 ~1초(설계 원칙 4).

파이프라인(3단, 한 번의 speak_stream 안에서):
  1. 합성 워커: 토큰을 모아 문장 단위로 잘라 합성 → 오디오 큐에 적재 (asyncio.to_thread)
  2. 재생 루프: 큐에서 꺼내 순차 재생 (sounddevice, blocking → to_thread)
  합성(N+1)이 재생(N)과 겹쳐 돌아 문장 사이 끊김을 줄인다.

stop()은 barge-in 필수 계약 — sounddevice 재생을 즉시 끊고(`sd.stop()`), 합성 워커는
다음 문장 경계에서 협조적으로 멈춘다(FakeMouth·EchoBrain과 같은 중단 규약).

목소리는 VoiceProfile이 소유한다: vendor_voice_id가 Supertonic 음색 이름(F1 등),
speed가 말 속도. 벤더를 갈아껴도 name이 같으면 "같은 목소리"(설계 원칙 2).
"""

from __future__ import annotations

import asyncio
import logging
import re
from collections.abc import AsyncIterator
from typing import TYPE_CHECKING, Any

from navi.models import VoiceProfile
from navi.mouth.base import MouthAdapter

if TYPE_CHECKING:
    import numpy as np

logger = logging.getLogger(__name__)

# 문장 종결 부호 뒤에서 자른다 — 한국어/영어 공통(마침표·물음·느낌·말줄임·줄바꿈).
# 닫는 따옴표·괄호가 뒤따르면 함께 포함한다. 소수점(3.5)에서 끊기지 않도록 뒤에 공백/끝을 요구.
_SENTENCE_END = re.compile(r'.*?[.!?。…\n]+["”\')\]]*(?=\s|$)', re.DOTALL)


class SupertonicMouth(MouthAdapter):
    def __init__(
        self,
        *,
        model: str = "supertonic-3",
        lang: str = "ko",
        total_steps: int = 8,
        tts: Any = None,
    ) -> None:
        self._model = model
        self._lang = lang
        self._total_steps = total_steps
        self._tts = tts  # 무거운 엔진 — 첫 발화 때 지연 로드(또는 테스트 주입)
        self._style_cache: dict[str, Any] = {}
        self._playing = False
        self._stopped = False

    # --- 엔진 지연 로드 -------------------------------------------------
    def _ensure_engine(self) -> Any:
        if self._tts is None:
            from supertonic import TTS  # 무거운 import는 첫 사용 시점으로

            logger.info("Supertonic 엔진 로드 중 (model=%s)", self._model)
            self._tts = TTS(model=self._model, auto_download=True)
        return self._tts

    def _style(self, voice_id: str) -> Any:
        if voice_id not in self._style_cache:
            self._style_cache[voice_id] = self._tts.get_voice_style(voice_name=voice_id)
        return self._style_cache[voice_id]

    # --- 합성·재생 (블로킹 — to_thread로 감싸 호출) -----------------------
    def _synth(self, text: str, voice: VoiceProfile) -> "np.ndarray":
        style = self._style(voice.vendor_voice_id)
        wav, _ = self._tts.synthesize(
            text=text,
            voice_style=style,
            total_steps=self._total_steps,
            speed=voice.speed,
            lang=self._lang,
        )
        return wav  # shape (1, num_samples), float32

    def _play(self, wav: "np.ndarray") -> None:
        import sounddevice as sd

        # synthesize는 (1, N) — sounddevice는 (N,) 또는 (N, ch)를 원한다.
        sd.play(wav.reshape(-1), samplerate=self._tts.sample_rate)
        sd.wait()  # stop()이 sd.stop()을 부르면 즉시 반환된다

    # --- 계약 ---------------------------------------------------------
    async def speak_stream(
        self, tokens: AsyncIterator[str], voice: VoiceProfile
    ) -> None:
        await asyncio.to_thread(self._ensure_engine)
        self._stopped = False
        self._playing = True
        audio_q: asyncio.Queue["np.ndarray | None"] = asyncio.Queue()
        synth = asyncio.create_task(self._synth_worker(tokens, voice, audio_q))
        try:
            while True:
                wav = await audio_q.get()
                if wav is None:  # 합성 워커 종료 신호
                    break
                if self._stopped:
                    break
                await asyncio.to_thread(self._play, wav)
                if self._stopped:
                    break
        finally:
            synth.cancel()
            try:
                await synth
            except asyncio.CancelledError:
                pass
            self._playing = False

    async def _synth_worker(
        self,
        tokens: AsyncIterator[str],
        voice: VoiceProfile,
        audio_q: "asyncio.Queue[np.ndarray | None]",
    ) -> None:
        """토큰을 모아 문장 경계마다 합성해 큐에 넣는다. 끝나면 None 센티넬."""
        buf = ""
        try:
            async for token in tokens:
                if self._stopped:
                    break
                buf += token
                # 버퍼에서 완성된 문장을 가능한 만큼 꺼내 합성한다
                while (m := _SENTENCE_END.match(buf)) is not None:
                    sentence = m.group().strip()
                    buf = buf[m.end():]
                    if sentence:
                        wav = await asyncio.to_thread(self._synth, sentence, voice)
                        await audio_q.put(wav)
                    if self._stopped:
                        break
            # 종결 부호 없이 끝난 꼬리말도 합성한다
            tail = buf.strip()
            if tail and not self._stopped:
                wav = await asyncio.to_thread(self._synth, tail, voice)
                await audio_q.put(wav)
        finally:
            await audio_q.put(None)

    def stop(self) -> None:
        self._stopped = True
        self._playing = False
        try:
            import sounddevice as sd

            sd.stop()  # 재생 중인 sd.wait()를 즉시 풀어 barge-in
        except Exception:  # 재생 장치 문제로 중단이 실패해도 플래그는 이미 내려둠
            logger.debug("sd.stop() 실패(무시)", exc_info=True)

    def is_playing(self) -> bool:
        return self._playing
