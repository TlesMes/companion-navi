"""페르소나 목소리 번들 — 음색(fine-tune)·톤(레퍼런스)은 페르소나 소유 (2026.07.10 결정).

페르소나 = 카드(성격) + 음색(가중치) + 톤 목록(그 음색의 레퍼런스 wav). 톤 레퍼런스는
해당 fine-tune 화자의 녹음이라 다른 가중치에 교차 적용할 수 없다 — 목소리 연속성
원칙(설계 원칙 2)의 데이터 모델 표현이 이 번들이다(gui.md PR ② 개정).

카드(card.py)는 인격 직렬화만 담당하므로 벤더별 목소리 스키마는 이 모듈이 맡는다.
스키마는 config의 벤더명-하위-섹션 관례(_load_mouth)와 동일:

    voice:
      name: aris
      speed: 1.0
      gptsovits:
        gpt_ckpt: ...          # 음색 가중치 — 부팅 로드 + 페르소나 교체 시 런타임 핫스왑
        sovits_ckpt: ...
        ref_lang: ja           # 가중치와 한 몸 — 핫스왑 시 함께 교체(빈 값 = 현재 유지)
        gen_lang: ja
        tones:                 # 첫 항목이 기본 톤
          - { name: 기본, icon: mood-smile, voice_id: <ref wav>, ref_text: "..." }

경로 규칙: gptsovits의 wav·ckpt는 리포 루트 기준 상대경로로 적고 parse(root=)가 즉시
절대경로로 고정한다 — gptsovits 엔진 웜업이 os.chdir(repo)를 하므로(gptsovits.py:108)
"나중에 CWD 기준" 해석은 깨진다. supertonic의 voice_id는 프리셋명이라 그대로 통과.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from navi.models import VoiceProfile

log = logging.getLogger(__name__)

# 예약 키 — 이 외의 최상위 키는 전부 벤더명 하위 섹션으로 취급한다.
_RESERVED = {"name", "speed"}
# voice_id를 경로로 해석해야 하는 벤더 (supertonic은 프리셋명)
_PATH_VOICE_ID_VENDORS = {"gptsovits"}
# 가중치·언어 kwarg를 생성자로 받는 벤더 — 다른 벤더에 넘기면 TypeError다.
# (SupertonicMouth는 model/lang/total_steps만 받는다)
_CKPT_VENDORS = {"gptsovits"}


def _abs(root: Path | None, value: str) -> str:
    """루트 기준 절대경로로. root 미지정(순수 파싱)이나 빈값이면 원문 유지."""
    if not root or not value:
        return value
    return str((root / value).resolve())  # 이미 절대경로면 pathlib이 그대로 둔다


@dataclass(frozen=True)
class ToneSpec:
    """톤 하나 — 같은 음색(가중치)의 레퍼런스 하나. GUI 톤 칩 1개에 대응."""

    name: str
    icon: str = ""
    voice_id: str = ""  # gptsovits=레퍼런스 wav 경로 / supertonic=프리셋명
    ref_text: str = ""  # voice_id(wav)의 전사 — gptsovits 전용, wav와 한 쌍


@dataclass(frozen=True)
class VendorVoice:
    """한 벤더에서의 이 목소리 구현 — 가중치 + 톤 목록(첫 항목이 기본 톤)."""

    gpt_ckpt: str = ""
    sovits_ckpt: str = ""
    ref_lang: str = ""  # 빈 값 = 현재 언어 유지 (부팅·핫스왑 공통 규칙)
    gen_lang: str = ""
    tones: tuple[ToneSpec, ...] = ()

    @property
    def ckpts(self) -> tuple[str, str]:
        """가중치 식별자 — 페르소나 교체 시 '같은 음색인가' 비교 키(SwapRuntime)."""
        return (self.gpt_ckpt, self.sovits_ckpt)


@dataclass(frozen=True)
class PersonaVoice:
    """페르소나가 소유한 목소리 — 논리적 정체성(name) + 벤더별 구현."""

    name: str
    speed: float = 1.0
    vendors: dict[str, VendorVoice] = field(default_factory=dict)

    @classmethod
    def parse(cls, raw: dict[str, Any], *, root: Path | None = None) -> PersonaVoice:
        vendors: dict[str, VendorVoice] = {}
        for key, section in raw.items():
            if key in _RESERVED or not isinstance(section, dict):
                continue
            as_path = key in _PATH_VOICE_ID_VENDORS
            tones = tuple(
                ToneSpec(
                    name=t["name"],
                    icon=t.get("icon", ""),
                    voice_id=_abs(root, t.get("voice_id", ""))
                    if as_path
                    else t.get("voice_id", ""),
                    ref_text=t.get("ref_text", ""),
                )
                for t in section.get("tones") or ()
            )
            vendors[key] = VendorVoice(
                gpt_ckpt=_abs(root, section.get("gpt_ckpt", "")),
                sovits_ckpt=_abs(root, section.get("sovits_ckpt", "")),
                ref_lang=section.get("ref_lang", ""),
                gen_lang=section.get("gen_lang", ""),
                tones=tones,
            )
        return cls(
            name=raw.get("name", ""),
            speed=float(raw.get("speed", 1.0)),
            vendors=vendors,
        )

    def vendor(self, vendor: str) -> VendorVoice | None:
        return self.vendors.get(vendor)

    def default_tone(self, vendor: str) -> ToneSpec | None:
        """활성 벤더의 기본 톤(첫 항목) — 부팅·페르소나 교체 시 초기 목소리."""
        vv = self.vendors.get(vendor)
        return vv.tones[0] if vv and vv.tones else None

    def profile(self, tone: ToneSpec) -> VoiceProfile:
        """톤 → 파이프라인에 넘길 VoiceProfile."""
        return VoiceProfile(
            name=self.name,
            vendor_voice_id=tone.voice_id,
            speed=self.speed,
            ref_text=tone.ref_text,
        )


@dataclass(frozen=True)
class MissingAssets:
    """카드가 가리키는데 실물이 없는 자산 — 게이팅(E3)·부팅 안내의 단일 판정 결과."""

    ckpts: tuple[str, ...] = ()  # 없는 가중치 파일 경로
    tones: tuple[str, ...] = ()  # 레퍼런스 wav가 없는 톤 이름 (카드 선언 순서 유지)

    def __bool__(self) -> bool:
        return bool(self.ckpts or self.tones)


def missing_assets(vendor: str, vendor_voice: VendorVoice | None) -> MissingAssets:
    """카드 지정 자산의 존재 검사 — 파일 시스템만 본다(엔진·torch 불필요).

    빈 값은 검사 대상이 아니다: 빈 ckpt = base(zero-shot) 의도(엔진이 base 경로를
    따로 검사한다 — gptsovits._resolve_base_ckpts), 빈 voice_id = 레퍼런스 없음.
    경로가 아닌 벤더(supertonic의 voice_id는 프리셋명)는 톤을 검사하지 않는다.

    톤 순서를 보존하는 이유: 첫 항목(기본 톤)이 없으면 페르소나 교체 자체가 실패하고,
    그 외 톤은 그 칩만 못 쓴다 — 호출부가 `tones[0].name`과 대조해 구분한다.
    """
    if vendor_voice is None:
        return MissingAssets()
    ckpts: tuple[str, ...] = ()
    if vendor in _CKPT_VENDORS:
        ckpts = tuple(
            p
            for p in (vendor_voice.gpt_ckpt, vendor_voice.sovits_ckpt)
            if p and not Path(p).is_file()
        )
    tones: tuple[str, ...] = ()
    if vendor in _PATH_VOICE_ID_VENDORS:
        tones = tuple(
            t.name
            for t in vendor_voice.tones
            if t.voice_id and not Path(t.voice_id).is_file()
        )
    return MissingAssets(ckpts=ckpts, tones=tones)


def select_vendor(voice: PersonaVoice | None, *, config_default: str) -> str:
    """부팅 시 쓸 TTS 벤더 — 카드가 번들을 소유하면 카드가 정한다(2026.07.10 결정).

    config는 번들 없는 카드(하위호환)의 폴백이자, 카드가 여러 벤더를 선언했을 때의
    타이브레이커. CLI --mouth는 이 함수에 도달하기 전에 처리된다(load_config).

    **절대 raise 하지 않는다** — 카드 하나가 부팅을 벽돌로 만드는 게 지금 고치려는
    실패 유형이다. 판단이 애매하면 고르고 로그로 알린다.
    """
    declared = list(voice.vendors) if voice else []
    if not declared:
        return config_default  # 번들 없음 = config의 ckpt 폴백이 계속 권위
    if config_default in declared:
        # 단독 선언이든 다중 선언이든, config와 일치하면 이견이 없다
        return config_default
    picked = declared[0]  # dict는 YAML 선언 순서를 보존한다
    if len(declared) > 1:
        log.warning(
            "카드가 벤더 %s를 선언했는데 config 기본(%s)이 없음 — %s 선택",
            declared, config_default, picked,
        )
    else:
        log.info("카드 목소리 번들이 벤더를 결정 — %s (config 기본: %s)", picked, config_default)
    return picked


def mouth_options(
    vendor: str, config_options: dict[str, Any], vendor_voice: VendorVoice | None
) -> dict[str, Any]:
    """create_mouth에 넘길 kwargs — config 옵션 위에 카드 번들을 덮는다.

    가중치·언어는 그 kwarg를 받는 벤더에만 넘긴다. 예전에는 벤더와 무관하게 주입해
    `SupertonicMouth(gpt_ckpt=…)` TypeError로 --voice 부팅이 항상 죽었다.

    빈 ckpt는 "base(zero-shot) 의도"라 config 폴백(아리스 fine-tune)을 **덮어 비운다** —
    카드가 번들을 소유하면 빈 값도 카드의 권위다. 언어는 빈 값이면 config를 유지한다
    (가중치와 달리 "미지정=현재 유지"가 부팅·핫스왑 공통 규칙, VendorVoice 참조).
    """
    options = dict(config_options)
    if vendor_voice is None or vendor not in _CKPT_VENDORS:
        return options
    options["gpt_ckpt"] = vendor_voice.gpt_ckpt
    options["sovits_ckpt"] = vendor_voice.sovits_ckpt
    for key, value in (("ref_lang", vendor_voice.ref_lang), ("gen_lang", vendor_voice.gen_lang)):
        if value:
            options[key] = value
    return options
