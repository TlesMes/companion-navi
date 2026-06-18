"""설정 로더 — config.yaml(튜닝값) + .env(비밀)를 합쳐 불변 Config로."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml
from dotenv import load_dotenv

from navi.models import VoiceProfile


@dataclass(frozen=True)
class BrainConfig:
    vendor: str  # gemini | anthropic | echo
    models: dict[str, str]  # vendor → model id

    @property
    def model(self) -> str:
        """현재 vendor에 맞는 모델 — 교체 시 vendor 한 줄만 바꾸면 되게."""
        return self.models[self.vendor]


@dataclass(frozen=True)
class MouthConfig:
    """TTS 어댑터 선택 + 나비의 목소리(VoiceProfile) + 벤더별 추가 kwargs.

    options는 create_mouth(vendor, **options)로 그대로 전달된다 — gptsovits의 repo/ckpt
    경로 등. 경로 옵션은 load_config에서 프로젝트 루트 기준 절대경로로 풀어둔다.
    """

    vendor: str  # fake | supertonic | gptsovits
    voice: VoiceProfile
    options: dict[str, Any]


@dataclass(frozen=True)
class Config:
    brain: BrainConfig
    mouth: MouthConfig
    db_path: Path
    recent_turns: int
    persona_card_path: Path
    gemini_api_key: str | None
    anthropic_api_key: str | None


def _resolve(root: Path, value: str) -> str:
    """경로 옵션을 루트 기준 절대경로로. 이미 절대경로(예: C:\\gptsovits)면 그대로 둔다."""
    return str((root / value).resolve()) if value else value


def _load_mouth(root: Path, raw: dict[str, Any]) -> MouthConfig:
    raw_mouth = raw.get("mouth", {})
    vendor = raw_mouth.get("vendor", "fake")
    mv = raw_mouth.get("voice", {})
    voice = VoiceProfile(
        name=mv.get("name", "navi"),
        # gptsovits는 vendor_voice_id가 레퍼런스 wav 경로 → 절대경로로 풀어둔다.
        # supertonic은 음색 이름(F1 등)이라 _resolve가 그대로 통과시킨다(파일이 아니어도 무해).
        vendor_voice_id=mv.get("vendor_voice_id", ""),
        speed=float(mv.get("speed", 1.0)),
    )
    if vendor == "gptsovits" and voice.vendor_voice_id:
        voice = VoiceProfile(
            name=voice.name,
            vendor_voice_id=_resolve(root, voice.vendor_voice_id),
            speed=voice.speed,
        )
    # 벤더 이름과 같은 하위 섹션을 추가 kwargs로 읽는다(gptsovits → repo/ckpt 경로 등).
    options = dict(raw_mouth.get(vendor, {}))
    for key in ("repo_path", "gpt_ckpt", "sovits_ckpt"):
        if options.get(key):
            options[key] = _resolve(root, options[key])
    return MouthConfig(vendor=vendor, voice=voice, options=options)


def load_config(root: Path | None = None) -> Config:
    root = root or Path.cwd()
    load_dotenv(root / ".env")
    raw = yaml.safe_load((root / "config.yaml").read_text(encoding="utf-8"))
    return Config(
        brain=BrainConfig(
            vendor=raw["brain"]["vendor"],
            models=dict(raw["brain"]["models"]),
        ),
        mouth=_load_mouth(root, raw),
        db_path=root / raw["db"]["path"],
        recent_turns=int(raw["memory"]["recent_turns"]),
        persona_card_path=root / raw["persona"]["card_path"],
        gemini_api_key=os.getenv("GEMINI_API_KEY") or None,
        anthropic_api_key=os.getenv("ANTHROPIC_API_KEY") or None,
    )
