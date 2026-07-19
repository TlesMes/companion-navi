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

from navi.persona import CharacterCard, PersonaVoice, missing_assets

if TYPE_CHECKING:
    from navi.conductor import Conductor
    from navi.pipeline import TurnPipeline

log = logging.getLogger(__name__)


class SwapBusy(RuntimeError):
    """재생 중 교체 시도 — HTTP 409로 번역된다."""


class PersonaUnavailable(RuntimeError):
    """이 세션에서 쓸 수 없는 페르소나·톤 — HTTP 422로 번역된다.

    파일 부재(FileNotFoundError)만이 아니라 벤더 불일치 같은 구조적 불가도 포함한다.
    """


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
            # 평가 시점을 응답 생성 시로 둔 이유: 이 스캔이 이미 캐시 없이 매 요청
            # 전수 재파싱이라 isfile 몇 번이 추가 비용의 전부고, 세션 중 ckpt를 갖추면
            # 다음 요청에 자동 반영된다. 부팅 시 1회 캐시면 영영 회색으로 남는다.
            available, reason = self.availability(path.stem, card.voice)
            out.append(
                {
                    "id": path.stem,
                    "character": card.character,
                    "current": path.stem == self._persona_id,
                    "available": available,
                    "reason": reason,
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
        # 판정은 카드 교체(set_card) *이전*에 — 통과 뒤 터지면 인격만 바뀌고 목소리는
        # 옛것인 반쪽 정체성이 남는다. GUI가 회색 처리해도 이 가드는 필요하다:
        # GUI를 우회하는 호출자(직접 HTTP·스크립트)에게도 같은 규칙이 걸려야 한다.
        available, reason = self.availability(persona_id, card.voice)
        if not available:
            raise PersonaUnavailable(f"{card.character}로 바꿀 수 없어요 — {reason}")
        vendor_voice = card.voice.vendor(self._vendor) if card.voice else None
        tone = card.voice.default_tone(self._vendor) if card.voice else None

        self._conductor.set_card(card)
        self._persona_id = persona_id

        voice_swapped = False
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

    def availability(self, persona_id: str, voice: PersonaVoice | None) -> tuple[bool, str]:
        """이 세션에서 이 페르소나로 갈아탈 수 있는가 + 못 하면 왜 (E3 차단 조건 4개).

        **조회(GET /personas)와 실행(POST /persona)이 같은 함수를 쓴다** — 두 벌로 짜면
        회색이 아닌데 막히거나 그 반대인 어긋남이 생긴다. 판정은 데몬 소유고 GUI는
        이 결과를 그리기만 한다(GUI를 우회하는 호출자도 막혀야 하므로).

        막는 기준은 하나: **목소리가 카드를 따라가지 못하면 막는다.** 인격만 바뀌고
        목소리는 그대로인 반쪽 정체성이 연속성 원칙(설계 원칙 2) 위반이라서다.
        사유에 해법까지 담는다 — 비활성화만 하면 "그럼 영영 못 쓰나"가 된다.

        텍스트 모드(pipeline=None)는 애초에 목소리를 안 건드리므로 전부 허용이다.
        순진하게 짜면 여기서 **전 카드가 비활성화**된다.
        """
        if self._pipeline is None:
            return True, ""
        restart = f"`run_navi.ps1 -Persona {persona_id}`로 재기동하면 쓸 수 있어요"
        if voice is None:  # ③ voice 섹션 없음 (example_kr 등)
            return False, (
                "이 카드엔 목소리 번들이 없어서 음성 세션에서 바꾸면 인격만 바뀌어요 — "
                f"{restart}"
            )
        vendor_voice = voice.vendor(self._vendor)
        if vendor_voice is None:  # ① 벤더 불일치 — 엔진 교체는 구조적 불가
            declared = ", ".join(voice.vendors) or "없음"
            return False, (
                f"이 세션은 {self._vendor} 엔진이라 이 목소리({declared})를 쓸 수 없어요 — "
                f"{restart}"
            )
        missing = missing_assets(self._vendor, vendor_voice)
        if missing.ckpts:  # ② 같은 벤더인데 ckpt 파일 부재
            return False, (
                "음색 가중치 파일이 없어요: "
                + ", ".join(Path(p).name for p in missing.ckpts)
                + " — 카드의 경로를 확인하거나 파일을 갖춰주세요"
            )
        tones = vendor_voice.tones
        if tones and tones[0].name in missing.tones:  # ④-a 기본 톤 레퍼런스 부재
            return False, (
                f"기본 톤「{tones[0].name}」의 레퍼런스 wav가 없어요: "
                f"{Path(tones[0].voice_id).name} — 파일을 갖춰주세요"
            )
        return True, ""  # ④-b(그 외 톤 부재)는 그 칩만 막는다 — list_voices 참조

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
        # ④-b: 레퍼런스 wav가 없는 톤은 그 칩만 막는다(페르소나는 정상) — 안 막으면
        # 교체는 성공한 듯 보이고 **말을 시켜야** 터진다(실패 타이밍이 최악).
        missing = missing_assets(self._vendor, vendor_voice)
        return [
            {
                "name": t.name,
                "icon": t.icon,
                "current": t.voice_id == current_id,
                "available": t.name not in missing.tones,
            }
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
        if tone.name in missing_assets(self._vendor, vendor_voice).tones:
            raise PersonaUnavailable(
                f"톤「{name}」의 레퍼런스 wav가 없어요: {Path(tone.voice_id).name}"
            )
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
