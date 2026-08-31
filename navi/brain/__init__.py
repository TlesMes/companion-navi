"""Brain 팩토리 — config의 vendor 문자열만으로 두뇌를 교체한다 (벤더 종속 설계 금지)."""

from __future__ import annotations

from navi.brain.base import BrainAdapter
from navi.config import Config

__all__ = [
    "BRAIN_ENV_NAMES",
    "BRAIN_KEY_FIELDS",
    "BRAIN_VENDORS",
    "BrainAdapter",
    "create_brain",
]

# 이 팩토리가 아는 벤더 — 표시 순서이기도 하다(GUI 라디오).
BRAIN_VENDORS = ("gemini", "anthropic", "echo")

# 키가 필요한 벤더 → Config 속성명 / .env 변수명. echo는 키가 없어서 양쪽 다 없다.
# **여기가 단일 소유자다** — preflight(부팅 전 점검)와 컨트롤 플레인(런타임 교체)이
# 같은 재료를 봐야 "키 있음으로 보이는데 막히는" 어긋남이 안 생긴다.
BRAIN_KEY_FIELDS = {"anthropic": "anthropic_api_key", "gemini": "gemini_api_key"}
BRAIN_ENV_NAMES = {"anthropic": "ANTHROPIC_API_KEY", "gemini": "GEMINI_API_KEY"}


def create_brain(config: Config, *, vendor: str | None = None) -> BrainAdapter:
    """두뇌 어댑터 하나. vendor 미지정이면 config.brain.vendor(대화용 두뇌).

    vendor를 넘길 수 있는 건 **대화가 아닌 용도**를 위해서다 — 피드 요약기는 저가 티어를
    쓰고 싶을 수 있고(D13/feed.md 3.3), 그때도 키 부재 에러 메시지는 여기 한 곳에 있어야
    한다. 그래서 팩토리를 넓혔지 호출부가 어댑터 클래스를 직접 생성하게 두지 않는다.

    ⚠ 어댑터 하나는 **동시 요청 1건**이 계약이다(base.py) — 용도가 다르면 vendor가 같아도
    인스턴스를 따로 만들어야 한다. 선제 발화 도중 수집이 돌면 last_result가 경합한다.
    """
    vendor = vendor or config.brain.vendor
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
    raise ValueError(f"알 수 없는 brain vendor: {vendor!r} (gemini | anthropic | echo)")
