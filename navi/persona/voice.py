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
        gpt_ckpt: ...          # 가중치 — 런타임 교체는 후속 PR, 지금은 부팅 시 사용
        sovits_ckpt: ...
        ref_lang: ja           # 전방호환 — 파싱만, 적용은 가중치 교체 후속 PR
        gen_lang: ja
        tones:                 # 첫 항목이 기본 톤
          - { name: 기본, icon: mood-smile, voice_id: <ref wav>, ref_text: "..." }

경로 규칙: gptsovits의 wav·ckpt는 리포 루트 기준 상대경로로 적고 parse(root=)가 즉시
절대경로로 고정한다 — gptsovits 엔진 웜업이 os.chdir(repo)를 하므로(gptsovits.py:108)
"나중에 CWD 기준" 해석은 깨진다. supertonic의 voice_id는 프리셋명이라 그대로 통과.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from navi.models import VoiceProfile

# 예약 키 — 이 외의 최상위 키는 전부 벤더명 하위 섹션으로 취급한다.
_RESERVED = {"name", "speed"}
# voice_id를 경로로 해석해야 하는 벤더 (supertonic은 프리셋명)
_PATH_VOICE_ID_VENDORS = {"gptsovits"}


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
    ref_lang: str = ""  # 전방호환 — 이번엔 파싱만, 런타임 적용은 가중치 교체 후속 PR
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
