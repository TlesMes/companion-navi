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

import asyncio
import logging
import time
from collections.abc import AsyncIterator, Callable

from navi.brain.base import BrainAdapter
from navi.conductor import Conductor
from navi.mouth.mood import peel_mood, strip_mood_tag
from navi.models import BrainResult, VoiceProfile
from navi.mouth.base import MouthAdapter

log = logging.getLogger(__name__)


class TurnPipeline:
    def __init__(
        self,
        *,
        brain: BrainAdapter,
        mouth: MouthAdapter | None,
        conductor: Conductor,
        voice: VoiceProfile,
        on_stage: Callable[[str, str, dict | None], None] | None = None,
        mood_voice: Callable[[str], VoiceProfile | None] | None = None,
    ) -> None:
        # mouth=None은 텍스트 모드 — 합성만 건너뛰고 나머지 턴 경로(요청 조립·무드·기억
        # 정리)는 음성과 공유한다. 무거운 TTS 의존성은 mouth 인스턴스에만 있어 텍스트
        # 모드는 여전히 가볍다. has_mouth가 "음성 세션인가"의 단일 판정(SwapRuntime가 씀).
        self._brain = brain
        self._mouth = mouth
        self._conductor = conductor
        self._voice = voice
        # 무드→이번 턴 목소리 해석기. None이면 무드 무시(self._voice 고정).
        # 소유는 SwapRuntime — 페르소나·기본 톤을 쥔 쪽이 self._voice.profile로 해석한다.
        self._mood_voice = mood_voice
        # 턴과 가중치 교체의 상호배제 — 교체는 torch.load(~2-5s) 때문에 스레드로 나가며
        # 그동안 루프를 놓는다. 락이 없으면 그 틈에 tick의 선제 발화가 시작돼 교체 중인
        # 모델로 합성한다. 락을 잡은 쪽이 끝나야 상대가 진행한다(is_playing이 이를 표면화).
        self._turn_lock = asyncio.Lock()
        # 단계 계측 콜백 (stage, phase, detail) — 파이프라인은 버스를 모른다(계층 분리),
        # 데몬이 bus.publish로 연결한다(Stage 15). 1차는 brain 첫 토큰·tts 진입/종료만,
        # TTFA(첫 오디오)는 어댑터 확장이 필요해 후속.
        self._on_stage = on_stage or (lambda stage, phase, detail=None: None)

    @property
    def has_mouth(self) -> bool:
        """음성 세션 여부 — mouth가 붙어 있으면 True. 텍스트 모드는 False.

        SwapRuntime이 목소리 연속성 판정(페르소나·톤 교체 가능 여부)을 이걸로 가른다.
        """
        return self._mouth is not None

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
        async with self._turn_lock:  # 가중치 교체 중이면 끝날 때까지 기다린다
            request = self._conductor.build_request(
                trigger_text, user_id=user_id, session_id=session_id
            )
            self._on_stage("brain", "start", None)
            tts_t0 = time.perf_counter()
            # 무드 선행 태그를 합성 전에 떼어 이번 턴 목소리를 고른다. 태그는
            # peel_mood가 흡수하므로 echo(_tee)·TTS로 새지 않는다. 폴백은 전부 self._voice.
            mood, body = await peel_mood(self._brain.generate_stream(request))
            voice = self._voice
            if self._mood_voice is not None:
                voice = self._mood_voice(mood) or self._voice
            if mood != "neutral" or voice is not self._voice:
                log.info("무드 %s → 목소리 %s", mood, voice.name)
            tokens = self._tee(body, echo, brain_t0=tts_t0)
            if self._mouth is not None:
                # 이번 턴 톤을 GUI에 알린다 — 자동 점등(gui.md Phase 3-5). voice_id로 칩을
                # 특정한다(/voices의 voice_id와 join). STAGE 채널 재사용이라 로그에도 남는다.
                self._on_stage(
                    "mood", "picked", {"mood": mood, "voice_id": voice.vendor_voice_id}
                )
                # brain 생성과 tts 합성·재생은 스트리밍으로 겹친다 — tts 구간은 전체를 덮는다.
                self._on_stage("tts", "start", None)
                await self._mouth.speak_stream(tokens, voice)
                tts_ms = (time.perf_counter() - tts_t0) * 1000
                self._on_stage("tts", "done", {"ms": round(tts_ms)})
                log.info("TTS(합성+재생) %.0fms", tts_ms)
            else:
                # 텍스트 모드 — 합성 없이 스트림을 소진해 echo(화면)·last_result를 확정한다.
                # 소진하지 않으면 brain이 끝까지 안 흘러 full_text가 비고 echo도 안 나온다.
                async for _ in tokens:
                    pass
            return self._clean_result(self._brain.last_result)

    @staticmethod
    def _clean_result(result: BrainResult | None) -> BrainResult | None:
        """확정 전문에서 무드 태그를 제거한 결과 — 기억 오염 방지.

        peel_mood는 합성 경로만 막는다. 두뇌 어댑터의 full_text엔 태그가 남아, 벗기지
        않으면 단기기억에 저장돼 다음 턴 맥락으로 되먹여진다(LLM이 태그를 대사로 학습).
        """
        if result is None:
            return None
        cleaned = strip_mood_tag(result.full_text)
        if cleaned == result.full_text:
            return result
        return BrainResult(full_text=cleaned, usage=result.usage)

    def set_mood_resolver(
        self, resolver: Callable[[str], VoiceProfile | None] | None
    ) -> None:
        """무드→목소리 해석기 주입(SwapRuntime 소유). None이면 무드 무시."""
        self._mood_voice = resolver

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

    async def swap_weights(
        self, gpt_ckpt: str, sovits_ckpt: str, *, ref_lang: str = "", gen_lang: str = ""
    ) -> None:
        """음색 가중치 교체 — 다음 턴부터 적용. 엔진은 그대로다(엔진 핫스왑 아님).

        모델 로드는 동기 블로킹(torch.load ~2-5s)이라 스레드로 넘긴다 — 안 그러면
        이벤트 루프가 통째로 멎어 GUI·컨트롤 플레인이 응답하지 않는다. 교체 구간엔
        턴 락을 쥐고 있어 그 사이 발화가 끼어들지 못한다(is_playing이 True를 반환).
        """
        if self._mouth is None:
            raise RuntimeError("텍스트 모드(mouth 없음) — 가중치 교체 대상이 없습니다")
        async with self._turn_lock:
            log.info("음색 가중치 교체 시작 — %s", gpt_ckpt or "(base)")
            await asyncio.to_thread(
                self._mouth.set_weights,
                gpt_ckpt,
                sovits_ckpt,
                ref_lang=ref_lang,
                gen_lang=gen_lang,
            )

    @property
    def current_voice(self) -> VoiceProfile:
        return self._voice

    def is_playing(self) -> bool:
        """재생 중(또는 턴·가중치 교체 진행 중) 여부 — 컨트롤 플레인의 교체 가드용.

        턴 락이 잡혀 있으면 발화 준비 중(두뇌 생성)이거나 가중치 교체 중이라 새 교체를
        받으면 안 된다 — 재생 플래그만으로는 이 구간이 비어 보인다.
        """
        playing = self._mouth.is_playing() if self._mouth is not None else False
        return playing or self._turn_lock.locked()

    def interrupt(self) -> None:
        """barge-in — 재생 즉시 중단 + LLM 생성 취소를 동시에(kill switch)."""
        log.info("barge-in — 재생 중단 + 생성 취소")
        if self._mouth is not None:
            self._mouth.stop()
        self._brain.cancel()
