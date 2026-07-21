"""Mouth(TTS) 팩토리 — vendor 문자열만으로 목소리 엔진을 교체한다 (벤더 종속 설계 금지).

D3 확정(2026.06.18): gptsovits — GPT-SoVITS fine-tune (아리스 168클립, Windows native CPU)
D3 잠정(한국어 기본): supertonic (고정 프리셋 F1)
클라우드 폴백(로컬 미달 시): cartesia | typecast

음색=제품 정체성이라 벤더를 갈아껴도 VoiceProfile.name이 같으면 "같은 목소리"로
취급한다(설계 원칙 2). 보이스 클로닝 어댑터는 ref_text 등 추가 kwargs를 생성자로 받는다.

**벤더 지식의 단일 출처(E7):** 각 벤더는 `_VENDORS`에 (빌더, 스펙) 한 항목으로 등록된다.
빌더는 `create_mouth`가, 스펙(navi.mouth.spec.VendorSpec)은 persona·config가 `vendor_spec`으로
조회한다. 둘이 한 항목이라 스펙 없이 벤더를 추가하는 게 불가능하고, 미등록 벤더로 부팅하면
`create_mouth`가 ValueError로 시끄럽게 죽는다(조용히 틀린 음색 대신).
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field

from navi.mouth.base import MouthAdapter
from navi.mouth.spec import VendorSpec

__all__ = ["MouthAdapter", "VendorSpec", "create_mouth", "vendor_spec"]

_PENDING_D3_CLOUD = ("cartesia", "typecast")  # 클라우드 폴백 — 로컬 미달 시 구현

# D3 평가 후보였으나 GPT-SoVITS 확정(2026.06.18)으로 미지원 상태.
# 파일(cosyvoice.py, f5tts.py)은 연구 참고용으로 보존하나 팩토리에서는 제공하지 않는다.
# cosyvoice: WSL2+ROCm 환경 가정 → Windows native 미동작. f5tts: device="cuda" 기본값 → CPU 불가.
_RETIRED_D3_CANDIDATES = ("cosyvoice", "f5tts")


@dataclass(frozen=True)
class _Vendor:
    """빌더 + 스펙 = 벤더 등록 한 항목. 지연 import(무거운 torch)는 빌더 안에 둔다."""

    build: Callable[..., MouthAdapter]
    spec: VendorSpec = field(default_factory=VendorSpec)


def _build_fake(**kwargs: object) -> MouthAdapter:
    from navi.mouth.fake import FakeMouth

    return FakeMouth()


def _build_supertonic(**kwargs: object) -> MouthAdapter:
    from navi.mouth.supertonic import SupertonicMouth

    return SupertonicMouth(**kwargs)  # type: ignore[arg-type]


def _build_gptsovits(**kwargs: object) -> MouthAdapter:
    from navi.mouth.gptsovits import GPTSoVITSMouth

    return GPTSoVITSMouth(**kwargs)  # type: ignore[arg-type]


# 벤더 레지스트리 — create_mouth 디스패치와 vendor_spec 조회의 단일 출처.
_VENDORS: dict[str, _Vendor] = {
    "fake": _Vendor(_build_fake),
    "supertonic": _Vendor(_build_supertonic),  # voice_id=프리셋명, 가중치 kwarg 없음
    "gptsovits": _Vendor(
        _build_gptsovits,
        VendorSpec(
            voice_id_is_path=True,  # voice_id = 레퍼런스 wav 경로
            weight_kwargs=("gpt_ckpt", "sovits_ckpt"),
            lang_kwargs=("ref_lang", "gen_lang"),
            path_option_keys=("repo_path", "gpt_ckpt", "sovits_ckpt"),
        ),
    ),
}


def vendor_spec(vendor: str) -> VendorSpec:
    """벤더의 요구사항 스펙 — 미등록 벤더는 빈 스펙(특수 요구 없음)으로 취급한다.

    persona·config가 "이 벤더가 무슨 kwarg를 받는가"를 하드코딩하는 대신 이걸 조회한다.
    미등록 벤더가 실제로 부팅되는 일은 `create_mouth`가 막는다(ValueError).
    """
    entry = _VENDORS.get(vendor)
    return entry.spec if entry else VendorSpec()


def create_mouth(vendor: str = "fake", **kwargs: object) -> MouthAdapter:
    """vendor 이름으로 Mouth 어댑터를 생성한다.

    D3 확정 어댑터:
      create_mouth("gptsovits", ref_text="레퍼런스 전사", repo_path=r"C:\\gptsovits", ...)
    """
    entry = _VENDORS.get(vendor)
    if entry is not None:
        return entry.build(**kwargs)
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
