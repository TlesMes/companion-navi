"""Mouth(TTS) 팩토리 — vendor 문자열만으로 목소리 엔진을 교체한다 (벤더 종속 설계 금지).

D3 후보(로컬 보이스 클로닝): f5tts | cosyvoice | gptsovits
D3 잠정(기존): supertonic (고정 프리셋, Stage 1 청취 비교 전까지 기본값 유지)
클라우드 폴백(로컬 미달 시): cartesia | typecast

음색=제품 정체성이라 벤더를 갈아껴도 VoiceProfile.name이 같으면 "같은 목소리"로
취급한다(설계 원칙 2). 보이스 클로닝 어댑터는 ref_text 등 추가 kwargs를 생성자로 받는다.
"""

from __future__ import annotations

from navi.mouth.base import MouthAdapter

__all__ = ["MouthAdapter", "create_mouth"]

_PENDING_D3_CLOUD = ("cartesia", "typecast")  # 클라우드 폴백 — 로컬 미달 시 구현


def create_mouth(vendor: str = "fake", **kwargs: object) -> MouthAdapter:
    """vendor 이름으로 Mouth 어댑터를 생성한다.

    보이스 클로닝 어댑터는 추가 kwargs를 생성자로 전달:
      create_mouth("f5tts",     ref_text="레퍼런스 전사")
      create_mouth("cosyvoice", ref_text="레퍼런스 전사")
      create_mouth("gptsovits", ref_text="레퍼런스 전사", repo_path="/opt/gptsovits")
    """
    if vendor == "fake":
        from navi.mouth.fake import FakeMouth

        return FakeMouth()
    if vendor == "supertonic":
        from navi.mouth.supertonic import SupertonicMouth

        return SupertonicMouth(**kwargs)  # type: ignore[arg-type]
    if vendor == "f5tts":
        from navi.mouth.f5tts import F5TTSMouth

        return F5TTSMouth(**kwargs)  # type: ignore[arg-type]
    if vendor == "cosyvoice":
        from navi.mouth.cosyvoice import CosyVoiceMouth

        return CosyVoiceMouth(**kwargs)  # type: ignore[arg-type]
    if vendor == "gptsovits":
        from navi.mouth.gptsovits import GPTSoVITSMouth

        return GPTSoVITSMouth(**kwargs)  # type: ignore[arg-type]
    if vendor in _PENDING_D3_CLOUD:
        raise NotImplementedError(
            f"TTS 벤더 {vendor!r}는 로컬(f5tts/cosyvoice) 품질 미달 시 클라우드 폴백으로 구현합니다."
        )
    raise ValueError(
        f"알 수 없는 mouth.vendor: {vendor!r} "
        f"(fake | supertonic | f5tts | cosyvoice | gptsovits | "
        f"{' | '.join(_PENDING_D3_CLOUD)})"
    )
