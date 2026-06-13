"""Mouth(TTS) 팩토리 — vendor 문자열만으로 목소리 엔진을 교체한다 (벤더 종속 설계 금지).

supertone·cartesia·typecast 어댑터는 D3(TTS 음색) 결정 후 구현한다 — 음색=제품 정체성이라
스펙이 아니라 귀로 정하는 가장 중요한 결정. 정하기 전에 짜면 버리는 코드가 된다.
지금은 계약과 fake만 둔다.
"""

from __future__ import annotations

from navi.mouth.base import MouthAdapter

__all__ = ["MouthAdapter", "create_mouth"]

_PENDING_D3 = ("supertone", "cartesia", "typecast")


def create_mouth(vendor: str = "fake") -> MouthAdapter:
    if vendor == "fake":
        from navi.mouth.fake import FakeMouth

        return FakeMouth()
    if vendor in _PENDING_D3:
        raise NotImplementedError(
            f"TTS 벤더 {vendor!r}는 D3 결정 후 구현합니다 — 현재 음색 청취 비교 중. "
            "키 없이 파이프라인을 시험하려면 vendor를 'fake'로 두세요."
        )
    raise ValueError(
        f"알 수 없는 mouth.vendor: {vendor!r} (fake | {' | '.join(_PENDING_D3)})"
    )
