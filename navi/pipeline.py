"""발화 한 턴의 오케스트레이션 — 트리거 텍스트를 받아 Brain→Mouth로 흘린다.

데몬 코어(arch 4.11)의 작은 선행 구현. 지금은 한 턴(trigger_text → 음성 답변)만 묶는다.
실시간 이벤트 루프·능동 발화·턴테이킹은 이후 Phase에서 이 자리를 키운다.

핵심: Brain.generate_stream과 Mouth.speak_stream이 둘 다 AsyncIterator[str] 계약이라
변환 없이 토큰 스트림을 그대로 넘긴다(_tee가 중간에서 화면 출력만 곁들임). N/N+1 문장
오버래핑(첫 오디오 ~1초)은 Mouth 어댑터 내부 큐가 이미 담당한다 — 여기서 큐를 또 만들지 않는다.

barge-in(kill switch): interrupt()가 재생 하드스톱(mouth.stop)과 LLM 생성 취소(brain.cancel)를
동시에 친다(arch 4.2 / 데이터흐름도 3번). 텍스트·오디오 큐는 Mouth가 턴 경계에서 새로
만들므로 stop()의 중단 플래그만으로 비워진다 — 별도 clear가 필요 없다.
"""

from __future__ import annotations

import logging
import time
from collections.abc import AsyncIterator, Callable

from navi.brain.base import BrainAdapter
from navi.conductor import Conductor
from navi.models import BrainResult, VoiceProfile
from navi.mouth.base import MouthAdapter

log = logging.getLogger(__name__)


class TurnPipeline:
    def __init__(
        self,
        *,
        brain: BrainAdapter,
        mouth: MouthAdapter,
        conductor: Conductor,
        voice: VoiceProfile,
        on_stage: Callable[[str, str, dict | None], None] | None = None,
    ) -> None:
        self._brain = brain
        self._mouth = mouth
        self._conductor = conductor
        self._voice = voice
        # 단계 계측 콜백 (stage, phase, detail) — 파이프라인은 버스를 모른다(계층 분리),
        # 데몬이 bus.publish로 연결한다(Stage 15). 1차는 brain 첫 토큰·tts 진입/종료만,
        # TTFA(첫 오디오)는 어댑터 확장이 필요해 후속.
        self._on_stage = on_stage or (lambda stage, phase, detail=None: None)

    async def run_turn(
        self,
        trigger_text: str,
        *,
        user_id: int,
        session_id: str,
        echo: Callable[[str], None] | None = None,
    ) -> BrainResult | None:
        """trigger_text로 한 턴을 돈다 — 요청 조립 → Brain 토큰 → Mouth 음성.

        echo는 토큰을 받는 콜백(예: 화면 print). 재생이 끝날 때까지 await한다.
        반환은 Brain의 last_result(전문·usage) — 호출부가 기억 적재에 쓴다.
        """
        request = self._conductor.build_request(
            trigger_text, user_id=user_id, session_id=session_id
        )
        self._on_stage("brain", "start", None)
        tts_t0 = time.perf_counter()
        tokens = self._tee(self._brain.generate_stream(request), echo, brain_t0=tts_t0)
        # brain 생성과 tts 합성·재생은 스트리밍으로 겹친다 — tts 구간은 전체를 덮는다.
        self._on_stage("tts", "start", None)
        await self._mouth.speak_stream(tokens, self._voice)
        tts_ms = (time.perf_counter() - tts_t0) * 1000
        self._on_stage("tts", "done", {"ms": round(tts_ms)})
        log.info("TTS(합성+재생) %.0fms", tts_ms)
        return self._brain.last_result

    async def _tee(
        self,
        source: AsyncIterator[str],
        sink: Callable[[str], None] | None,
        *,
        brain_t0: float,
    ) -> AsyncIterator[str]:
        """토큰을 Mouth로 흘리며 동시에 sink(화면)에도 내보낸다 — 변환 없이 통과만.

        첫 토큰에서 brain done을 계측한다(TTFT) — 이후 토큰은 tts 구간과 겹쳐 흐른다.
        """
        first = True
        async for token in source:
            if first:
                first = False
                ttft_ms = (time.perf_counter() - brain_t0) * 1000
                self._on_stage("brain", "done", {"ttft_ms": round(ttft_ms)})
            if sink is not None:
                sink(token)
            yield token

    def set_voice(self, voice: VoiceProfile) -> None:
        """목소리(톤) 교체 — 다음 턴부터 적용된다. 진행 중인 턴은 건드리지 않는다.

        재생 중 거부(409)는 호출부(컨트롤 플레인)의 몫 — 여기서는 교체만 한다.
        """
        log.info("목소리 교체: %s → %s", self._voice.name, voice.name)
        self._voice = voice

    @property
    def current_voice(self) -> VoiceProfile:
        return self._voice

    def is_playing(self) -> bool:
        """재생 중 여부 — mouth 위임 (컨트롤 플레인의 교체 가드용)."""
        return self._mouth.is_playing()

    def interrupt(self) -> None:
        """barge-in — 재생 즉시 중단 + LLM 생성 취소를 동시에(kill switch)."""
        log.info("barge-in — 재생 중단 + 생성 취소")
        self._mouth.stop()
        self._brain.cancel()
