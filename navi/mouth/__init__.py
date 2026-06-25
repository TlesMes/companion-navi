"""Mouth(TTS) 팩토리 — vendor 문자열만으로 목소리 엔진을 교체한다 (벤더 종속 설계 금지).

D3 확정(2026.06.18): gptsovits — GPT-SoVITS fine-tune (아리스 168클립, Windows native CPU)
D3 잠정(한국어 기본): supertonic (고정 프리셋 F1)
클라우드 폴백(로컬 미달 시): cartesia | typecast

음색=제품 정체성이라 벤더를 갈아껴도 VoiceProfile.name이 같으면 "같은 목소리"로
취급한다(설계 원칙 2). 보이스 클로닝 어댑터는 ref_text 등 추가 kwargs를 생성자로 받는다.
"""

from __future__ import annotations

from navi.mouth.base import MouthAdapter

__all__ = ["MouthAdapter", "create_mouth"]

_PENDING_D3_CLOUD = ("cartesia", "typecast")  # 클라우드 폴백 — 로컬 미달 시 구현

# D3 평가 후보였으나 GPT-SoVITS 확정(2026.06.18)으로 미지원 상태.
# 파일(cosyvoice.py, f5tts.py)은 연구 참고용으로 보존하나 팩토리에서는 제공하지 않는다.
# cosyvoice: WSL2+ROCm 환경 가정 → Windows native 미동작. f5tts: device="cuda" 기본값 → CPU 불가.
_RETIRED_D3_CANDIDATES = ("cosyvoice", "f5tts")


def create_mouth(vendor: str = "fake", **kwargs: object) -> MouthAdapter:
    """vendor 이름으로 Mouth 어댑터를 생성한다.

    D3 확정 어댑터:
      create_mouth("gptsovits", ref_text="레퍼런스 전사", repo_path=r"C:\\gptsovits", ...)
    """
    if vendor == "fake":
        from navi.mouth.fake import FakeMouth

        return FakeMouth()
    if vendor == "supertonic":
        from navi.mouth.supertonic import SupertonicMouth

        return SupertonicMouth(**kwargs)  # type: ignore[arg-type]
    if vendor == "gptsovits":
        from navi.mouth.gptsovits import GPTSoVITSMouth

        return GPTSoVITSMouth(**kwargs)  # type: ignore[arg-type]
    if vendor in _RETIRED_D3_CANDIDATES:
        raise NotImplementedError(
            f"TTS 벤더 {vendor!r}는 D3 평가 후보였으나 GPT-SoVITS fine-tune 확정"
            f"(2026.06.18)으로 미지원 상태입니다. gptsovits 어댑터를 사용하세요."
        )
    if vendor in _PENDING_D3_CLOUD:
        raise NotImplementedError(
            f"TTS 벤더 {vendor!r}는 로컬 품질 미달 시 클라우드 폴백으로 구현합니다."
        )
    raise ValueError(
        f"알 수 없는 mouth.vendor: {vendor!r} "
        f"(fake | supertonic | gptsovits | {' | '.join(_PENDING_D3_CLOUD)})"
    )
