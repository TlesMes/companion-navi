"""설정 로더 — config.yaml(튜닝값) + .env(비밀)를 합쳐 불변 Config로."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

import yaml
from dotenv import load_dotenv


@dataclass(frozen=True)
class BrainConfig:
    vendor: str  # gemini | anthropic | echo
    models: dict[str, str]  # vendor → model id

    @property
    def model(self) -> str:
        """현재 vendor에 맞는 모델 — 교체 시 vendor 한 줄만 바꾸면 되게."""
        return self.models[self.vendor]


@dataclass(frozen=True)
class Config:
    brain: BrainConfig
    db_path: Path
    recent_turns: int
    persona_card_path: Path
    gemini_api_key: str | None
    anthropic_api_key: str | None


def load_config(root: Path | None = None) -> Config:
    root = root or Path.cwd()
    load_dotenv(root / ".env")
    raw = yaml.safe_load((root / "config.yaml").read_text(encoding="utf-8"))
    return Config(
        brain=BrainConfig(
            vendor=raw["brain"]["vendor"],
            models=dict(raw["brain"]["models"]),
        ),
        db_path=root / raw["db"]["path"],
        recent_turns=int(raw["memory"]["recent_turns"]),
        persona_card_path=root / raw["persona"]["card_path"],
        gemini_api_key=os.getenv("GEMINI_API_KEY") or None,
        anthropic_api_key=os.getenv("ANTHROPIC_API_KEY") or None,
    )
