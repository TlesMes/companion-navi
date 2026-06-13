"""STT 팩토리 — vendor 문자열만으로 듣기 엔진을 교체한다 (벤더 종속 설계 금지).

vito·clova·deepgram 어댑터는 D2(STT 벤더) 결정 후 구현한다 — 한국어 CER 청취 비교가
관문이라, 정하기 전에 짜면 버리는 코드가 된다. 지금은 계약과 fake만 둔다.
"""

from __future__ import annotations

from navi.stt.base import SttAdapter, SttSession

__all__ = ["SttAdapter", "SttSession", "create_stt"]

_PENDING_D2 = ("vito", "clova", "deepgram")


def create_stt(vendor: str = "fake") -> SttAdapter:
    if vendor == "fake":
        from navi.stt.fake import FakeStt

        return FakeStt()
    if vendor in _PENDING_D2:
        raise NotImplementedError(
            f"STT 벤더 {vendor!r}는 D2 결정 후 구현합니다 — 현재 한국어 CER 청취 비교 중. "
            "키 없이 파이프라인을 시험하려면 vendor를 'fake'로 두세요."
        )
    raise ValueError(f"알 수 없는 stt.vendor: {vendor!r} (fake | {' | '.join(_PENDING_D2)})")
