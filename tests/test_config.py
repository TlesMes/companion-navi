"""설정 로더 — 특히 부팅 TTS 벤더 해석(카드 번들 소유).

벤더만 맞고 옵션이 틀리면 그게 곧 부팅 실패였으므로(SupertonicMouth(gpt_ckpt=…)),
벤더와 options를 항상 함께 단언한다.
"""

from pathlib import Path

import yaml

from navi.config import load_config

_CONFIG = {
    "brain": {"vendor": "echo", "models": {"echo": "echo"}},
    "mouth": {
        "vendor": "supertonic",
        "voice": {"name": "navi", "speed": 1.0},
        "supertonic": {"voice_id": "F1", "model": "supertonic-3", "lang": "ko", "total_steps": 8},
        "gptsovits": {"repo_path": "C:/gptsovits", "gpt_ckpt": "voice_ref/a.ckpt"},
    },
    "db": {"path": "navi.db"},
    "memory": {"recent_turns": 6},
    "persona": {"card_path": "personas/card.yaml"},
}

_CARD = {
    "character": "테스트",
    "profiles": [
        {
            "name": "기본",
            "min_intimacy": 0.0,
            "background": "배경",
            "traits": "성격",
            "example_dialogues": [{"user": "안녕", "assistant": "안녕!"}],
        }
    ],
}


def _write(root: Path, *, card_voice: dict | None, config_vendor: str = "supertonic") -> None:
    """config.yaml + 카드 한 쌍을 tmp_path에 깐다. card_voice=None이면 voice 섹션 없음."""
    config = {**_CONFIG, "mouth": {**_CONFIG["mouth"], "vendor": config_vendor}}
    (root / "config.yaml").write_text(yaml.safe_dump(config), encoding="utf-8")
    (root / "personas").mkdir(exist_ok=True)
    card = dict(_CARD)
    if card_voice is not None:
        card["voice"] = card_voice
    (root / "personas" / "card.yaml").write_text(yaml.safe_dump(card), encoding="utf-8")


def test_load_config_resolves_mouth_vendor_from_persona_card(tmp_path):
    """카드가 gptsovits 번들만 선언 — config가 supertonic이어도 카드가 이긴다.

    벤더뿐 아니라 options까지 그 벤더 것으로 바뀌어야 한다. 벤더만 맞고 옵션이
    supertonic 것이면 생성자에서 그대로 죽는다.
    """
    _write(tmp_path, card_voice={"name": "aris", "gptsovits": {"ref_lang": "ja"}})
    config = load_config(tmp_path)
    assert config.mouth.vendor == "gptsovits"
    assert "repo_path" in config.mouth.options
    for key in ("model", "lang", "total_steps"):
        assert key not in config.mouth.options


def test_load_config_cli_mouth_override_beats_card_bundle(tmp_path):
    """--mouth는 카드보다 위 — 텍스트 스모크에 fake를 강제하는 유일한 수단."""
    _write(tmp_path, card_voice={"name": "aris", "gptsovits": {"ref_lang": "ja"}})
    config = load_config(tmp_path, mouth_vendor="fake")
    assert config.mouth.vendor == "fake"
    assert config.mouth.options == {}


def test_load_config_keeps_config_vendor_when_card_has_no_voice_section(tmp_path):
    """voice 섹션 없는 카드(하위호환) — config 벤더와 ckpt 폴백이 계속 권위."""
    _write(tmp_path, card_voice=None, config_vendor="gptsovits")
    config = load_config(tmp_path)
    assert config.mouth.vendor == "gptsovits"
    assert config.mouth.options["gpt_ckpt"].endswith("a.ckpt")


def test_load_config_falls_back_when_card_unreadable_for_vendor(tmp_path):
    """깨진 카드에도 벤더 해석은 config 기본으로 떨어진다 — 여기가 새 실패 지점이 되지 않는다.

    **부팅이 구제된다는 뜻이 아니다.** daemon._run이 곧바로 같은 파일을 CharacterCard로
    읽다 죽는다(페르소나 없이 도는 데몬은 없으므로 의도된 fail-fast). 이 테스트 이름이
    "survives"였을 때 그 보장을 하는 것처럼 읽혀 실제로 오해를 낳았다(2026.07.19 리뷰).
    """
    _write(tmp_path, card_voice=None)
    (tmp_path / "personas" / "card.yaml").write_text("{{ 깨진 yaml", encoding="utf-8")
    config = load_config(tmp_path)
    assert config.mouth.vendor == "supertonic"


def test_load_config_resolves_vendor_even_if_card_profiles_broken(tmp_path):
    """voice 섹션만 멀쩡하면 벤더는 해석된다 — 카드 전체를 짓지 않기 때문."""
    _write(tmp_path, card_voice={"name": "aris", "gptsovits": {"ref_lang": "ja"}})
    card = tmp_path / "personas" / "card.yaml"
    card.write_text(
        yaml.safe_dump(
            {"character": "테스트", "profiles": [], "voice": {"name": "aris", "gptsovits": {}}}
        ),
        encoding="utf-8",
    )
    assert load_config(tmp_path).mouth.vendor == "gptsovits"
