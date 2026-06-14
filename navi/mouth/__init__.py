"""Mouth(TTS) 팩토리 — vendor 문자열만으로 목소리 엔진을 교체한다 (벤더 종속 설계 금지).

supertonic은 로컬 잠정 TTS(D3 청취 비교 중 채택, 음색 F1). cartesia·typecast 등 클라우드
폴백 어댑터는 로컬 품질 미달 시 같은 계약 뒤에 끼운다. 음색=제품 정체성이라 벤더를 갈아껴도
VoiceProfile.name이 같으면 "같은 목소리"로 취급한다(설계 원칙 2).
"""

from __future__ import annotations

from navi.mouth.base import MouthAdapter

__all__ = ["MouthAdapter", "create_mouth"]

_PENDING_D3 = ("cartesia", "typecast")  # 클라우드 폴백 — 로컬 미달 시 구현


def create_mouth(vendor: str = "fake") -> MouthAdapter:
    if vendor == "fake":
        from navi.mouth.fake import FakeMouth

        return FakeMouth()
    if vendor == "supertonic":
        from navi.mouth.supertonic import SupertonicMouth

        return SupertonicMouth()
    if vendor in _PENDING_D3:
        raise NotImplementedError(
            f"TTS 벤더 {vendor!r}는 로컬(supertonic) 품질 미달 시 폴백으로 구현합니다. "
            "지금은 vendor를 'supertonic'(로컬) 또는 'fake'(엔진 없이)로 두세요."
        )
    raise ValueError(
        f"알 수 없는 mouth.vendor: {vendor!r} "
        f"(fake | supertonic | {' | '.join(_PENDING_D3)})"
    )
