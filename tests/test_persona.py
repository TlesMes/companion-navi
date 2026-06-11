from pathlib import Path

from navi.persona import CharacterCard, PersonaProfile

CARD_PATH = Path(__file__).parents[1] / "personas" / "navi.yaml"


def test_load_real_card():
    card = CharacterCard.load(CARD_PATH)
    assert card.character == "나비"
    profile = card.profiles[0]
    assert profile.background and profile.traits
    assert len(profile.example_dialogues) >= 10  # 카드 스키마: few-shot 10~20개


def test_profile_selected_by_intimacy_threshold():
    def make(name: str, min_intimacy: float) -> PersonaProfile:
        return PersonaProfile(
            name=name,
            min_intimacy=min_intimacy,
            background="b",
            traits="t",
            example_dialogues=(("u", "a"),),
        )

    card = CharacterCard(character="나비", profiles=(make("서먹", 0), make("편함", 50)))
    assert card.profile_for(0).name == "서먹"
    assert card.profile_for(49.9).name == "서먹"
    assert card.profile_for(50).name == "편함"
    assert card.profile_for(100).name == "편함"


def test_system_prompt_is_cache_stable_and_contains_card():
    card = CharacterCard.load(CARD_PATH)
    prompt = card.system_prompt(0)
    # 캐싱 전제: 같은 친밀도 단계면 매 호출 동일한 문자열
    assert prompt == card.system_prompt(0)
    assert "나비" in prompt
    assert "성격과 말투 규칙" in prompt
    assert "말투 예시" in prompt
    # 예시가 가짜 기억으로 오염되지 않도록 경고문이 들어가야 한다
    assert "실제로 있었던 대화가 아니다" in prompt
