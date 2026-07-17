"""페르소나·톤 런타임 교체 파사드 (Stage 15-② — gui.md PR ②).

DaemonCore는 conductor·pipeline을 들지 않는다(전부 _run 지역변수) — 컨트롤 플레인의
교체 명령이 닿을 손잡이가 없어, 생성자 비대화 대신 이 파사드가 손잡이를 모아 든다.
도메인 로직(스캔·교체·재생 중 판정)은 여기, server.py는 HTTP 번역만.

목소리 연속성 원칙(설계 원칙 2)의 런타임 표현: 톤 레퍼런스는 그 fine-tune 화자의
녹음이라 다른 가중치에 교차 적용할 수 없다. 그래서 두 주인을 분리 추적한다 —
persona_id(카드 주인)와 voice_persona_id(톤 세트·VoiceProfile 주인). 가중치가 다르면
새 가중치를 엔진에 올린 뒤(핫스왑) 톤을 걸어 둘을 다시 합친다 — 핫스왑을 지원하지
않는 엔진에서만 분열이 남는다(카드만 교체, voice_swapped=false).

동시성: 컨트롤 서버와 턴은 같은 이벤트 루프다. 톤 교체는 is_playing 판정→set_voice
사이에 await가 없어 원자적이라 락이 없다. 가중치 교체는 모델 로드를 스레드로 넘기며
루프를 놓으므로 TurnPipeline의 턴 락이 그 구간을 지킨다(is_playing이 True를 반환해
동시 교체 요청은 409).
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING

from navi.persona import CharacterCard, PersonaVoice

if TYPE_CHECKING:
    from navi.conductor import Conductor
    from navi.pipeline import TurnPipeline

log = logging.getLogger(__name__)


class SwapBusy(RuntimeError):
    """재생 중 교체 시도 — HTTP 409로 번역된다."""


class SwapRuntime:
    def __init__(
        self,
        *,
        conductor: Conductor,
        pipeline: TurnPipeline | None,  # None = 텍스트 모드(--voice 없음)
        personas_dir: Path,
        root: Path,
        vendor: str,
        persona_id: str,
        loaded_ckpts: tuple[str, str],  # 부팅 시 로드된 가중치 — "같은 음색" 비교 키
    ) -> None:
        self._conductor = conductor
        self._pipeline = pipeline
        self._personas_dir = personas_dir
        self._root = root
        self._vendor = vendor
        self._persona_id = persona_id
        self._loaded_ckpts = loaded_ckpts
        # 톤 세트의 주인 — 부팅 페르소나의 번들(voice 섹션 없으면 None = 톤 목록 빈 배열)
        self._voice_persona_id = persona_id
        self._voice: PersonaVoice | None = conductor.card.voice

    @property
    def character(self) -> str:
        """현재 카드의 캐릭터명 — 데몬 프롬프트(run_turn)의 동적 참조용."""
        return self._conductor.card.character

    # --- 페르소나 ------------------------------------------------------

    def list_personas(self) -> list[dict]:
        """personas/*.yaml 스캔 — 매 요청 재스캔(캐시 없음, 파일 추가 즉시 반영)."""
        out = []
        for path in sorted(self._personas_dir.glob("*.yaml")):
            try:
                card = CharacterCard.load(path, root=self._root)
            except Exception:
                log.warning("페르소나 카드 파싱 실패 — 건너뜀: %s", path, exc_info=True)
                continue
            out.append(
                {
                    "id": path.stem,
                    "character": card.character,
                    "current": path.stem == self._persona_id,
                }
            )
        return out

    async def swap_persona(self, persona_id: str) -> dict:
        """번들 교체 진입점 — 카드는 즉시, 목소리는 가중치까지 따라간다.

        가중치가 다르면 엔진에 새 가중치를 올린 뒤(핫스왑) 톤을 건다 — 이로써 카드 주인과
        목소리 주인의 분열(위 docstring)이 해소된다. 엔진이 핫스왑을 지원하지 않으면
        (프리셋 기반 등) 카드만 교체하고 목소리는 유지한다.
        """
        self._guard_not_playing()
        path = self._personas_dir / f"{persona_id}.yaml"
        if not path.exists():
            raise LookupError(f"페르소나 없음: {persona_id!r}")
        card = CharacterCard.load(path, root=self._root)
        self._conductor.set_card(card)
        self._persona_id = persona_id

        voice_swapped = False
        vendor_voice = card.voice.vendor(self._vendor) if card.voice else None
        tone = card.voice.default_tone(self._vendor) if card.voice else None
        if self._pipeline is not None and vendor_voice is not None and tone is not None:
            if vendor_voice.ckpts != self._loaded_ckpts:
                voice_swapped = await self._swap_weights(vendor_voice)
            else:
                voice_swapped = True  # 같은 음색 — 레퍼런스만 갈면 된다
            if voice_swapped:
                self._pipeline.set_voice(card.voice.profile(tone))
                self._voice = card.voice
                self._voice_persona_id = persona_id
        return {
            "id": persona_id,
            "character": card.character,
            "voice_swapped": voice_swapped,
        }

    async def _swap_weights(self, vendor_voice) -> bool:
        """새 음색 가중치를 엔진에 올린다. 성공 시 True — 미지원 엔진이면 False(카드만 교체).

        언어(ref/gen)는 가중치와 한 몸이라 함께 넘긴다 — 카드 번들의 원자 교체.
        """
        try:
            await self._pipeline.swap_weights(
                vendor_voice.gpt_ckpt,
                vendor_voice.sovits_ckpt,
                # 빈 값 = 현재 언어 유지 — 부팅 배선(daemon._run)과 같은 규칙
                ref_lang=vendor_voice.ref_lang,
                gen_lang=vendor_voice.gen_lang,
            )
        except NotImplementedError:
            log.info(
                "%s 엔진은 가중치 핫스왑 미지원 — 카드만 교체(목소리 유지)", self._vendor
            )
            return False
        self._loaded_ckpts = vendor_voice.ckpts
        return True

    # --- 톤 (현재 목소리 주인의 톤 세트 안에서만) ----------------------

    def list_voices(self) -> list[dict]:
        pipeline = self._require_pipeline()
        vendor_voice = self._voice.vendor(self._vendor) if self._voice else None
        if vendor_voice is None:
            return []  # 번들 없는 부팅(구 config 폴백) — 톤 교체 대상 없음
        current_id = pipeline.current_voice.vendor_voice_id
        return [
            {"name": t.name, "icon": t.icon, "current": t.voice_id == current_id}
            for t in vendor_voice.tones
        ]

    def set_voice(self, name: str) -> dict:
        pipeline = self._require_pipeline()
        self._guard_not_playing()
        vendor_voice = self._voice.vendor(self._vendor) if self._voice else None
        tone = None
        if vendor_voice is not None:
            tone = next((t for t in vendor_voice.tones if t.name == name), None)
        if tone is None:
            raise LookupError(f"톤 없음: {name!r}")
        pipeline.set_voice(self._voice.profile(tone))
        return {"name": name, "applied": "next_turn"}

    def tone_file(self, name: str) -> Path | None:
        """톤 레퍼런스 wav 경로 — GUI 시청취용. 파일이 아니면(프리셋명 등) None.

        재생은 GUI(WebView <audio>)가 한다 — 데몬 오디오 핫패스와 무관한 정적 전송.
        """
        vendor_voice = self._voice.vendor(self._vendor) if self._voice else None
        if vendor_voice is None:
            return None
        tone = next((t for t in vendor_voice.tones if t.name == name), None)
        if tone is None or not tone.voice_id:
            return None
        path = Path(tone.voice_id)
        return path if path.is_file() else None

    # --- 가드 -----------------------------------------------------------

    def _require_pipeline(self) -> TurnPipeline:
        if self._pipeline is None:
            raise RuntimeError("음성 파이프라인 없음 — --voice 없이 기동됨")
        return self._pipeline

    def _guard_not_playing(self) -> None:
        if self._pipeline is not None and self._pipeline.is_playing():
            raise SwapBusy("재생 중 — 발화가 끝난 뒤 다시 시도")
