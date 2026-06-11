"""Brain 팩토리 — config의 vendor 문자열만으로 두뇌를 교체한다 (벤더 종속 설계 금지)."""

from __future__ import annotations

from navi.brain.base import BrainAdapter
from navi.config import Config

__all__ = ["BrainAdapter", "create_brain"]


def create_brain(config: Config) -> BrainAdapter:
    vendor = config.brain.vendor
    if vendor == "gemini":
        from navi.brain.gemini import GeminiBrain

        if not config.gemini_api_key:
            raise RuntimeError(
                "GEMINI_API_KEY가 없습니다 — .env.example을 .env로 복사해 키를 채우거나, "
                "키 없이 시험하려면 brain.vendor를 echo로 바꾸세요."
            )
        return GeminiBrain(api_key=config.gemini_api_key)
    if vendor == "anthropic":
        from navi.brain.anthropic import AnthropicBrain

        if not config.anthropic_api_key:
            raise RuntimeError("ANTHROPIC_API_KEY가 없습니다 — .env에 키를 채우세요.")
        return AnthropicBrain(api_key=config.anthropic_api_key)
    if vendor == "echo":
        from navi.brain.echo import EchoBrain

        return EchoBrain()
    raise ValueError(f"알 수 없는 brain.vendor: {vendor!r} (gemini | anthropic | echo)")
