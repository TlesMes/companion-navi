"""벤더 스펙 — TTS 어댑터가 스스로 밝히는 요구사항의 단일 출처 (E7).

예전엔 "gptsovits는 특별하다"는 지식이 세 곳에 각자 인코딩돼 있었다:
persona/voice.py의 `_PATH_VOICE_ID_VENDORS`·`_CKPT_VENDORS`, config.py의
`if vendor == "gptsovits"`. 두 번째 ckpt 벤더를 추가할 때 하나라도 빠뜨리면
**부팅은 되는데 음색만 조용히 틀린** 상태가 됐다(PR #24 리뷰 지적).

이제 각 벤더가 자기 요구사항을 이 스펙 하나로 선언하고, 소비처(persona·config)는
`create_mouth`와 같은 레지스트리(navi.mouth.__init__ `_VENDORS`)에서 조회만 한다.
빌더와 스펙이 한 항목이라 **스펙 없이 벤더를 추가하는 게 구조적으로 불가능**하다.

불변식: 스펙의 kwarg 이름 = VendorVoice 필드명 = create_mouth kwarg명. 셋이 같은
이름이라 소비처가 `getattr(vendor_voice, kwarg)`로 값을 꺼낸다(voice.py 참조).
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class VendorSpec:
    """한 TTS 벤더의 요구사항. 미등록 벤더는 빈 스펙(특수 요구 없음)으로 취급한다."""

    voice_id_is_path: bool = False
    """voice_id가 레퍼런스 wav **경로**인가(True면 루트 기준 절대화). supertonic처럼
    프리셋명이면 False — 파일이 아니라 그대로 통과한다."""

    weight_kwargs: tuple[str, ...] = ()
    """가중치 kwarg 이름 — 이 벤더 생성자만 받는다. 다른 벤더에 넘기면 TypeError.
    (`missing_assets`가 존재 검사할 파일 필드이자 `mouth_options`가 주입할 kwarg.)"""

    lang_kwargs: tuple[str, ...] = ()
    """언어 kwarg 이름 — 가중치와 한 몸이되 "빈 값=현재 유지" 규칙(부팅·핫스왑 공통)."""

    path_option_keys: tuple[str, ...] = ()
    """config `mouth.<vendor>:` 섹션에서 루트 기준 절대화할 옵션 키(repo/ckpt 경로 등)."""
