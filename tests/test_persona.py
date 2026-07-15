from pathlib import Path

from navi.persona import CharacterCard, PersonaProfile, PersonaVoice

PERSONAS_DIR = Path(__file__).parents[1] / "personas"
CARD_PATH = PERSONAS_DIR / "navi.yaml"


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


# --- voice 번들 (Stage 15-② — 음색·톤은 페르소나 소유) ---


def test_real_navi_card_carries_voice_bundle():
    """공개 카드 navi.yaml — supertonic 프리셋 톤(F1). aris.yaml은 gitignore라 제외."""
    navi = CharacterCard.load(CARD_PATH)
    assert navi.voice is not None and navi.voice.name == "navi"
    assert navi.voice.default_tone("supertonic").voice_id == "F1"


def test_example_card_is_gptsovits_zero_shot_bundle():
    """공개 예시 카드(JP) — fine-tune ckpt 없이 base(zero-shot). aris를 대신하는 재현 자산.

    커밋된 실파일로 gptsovits 번들 로더 경로(ckpt 부재·레퍼런스 경로 절대화)를 커버한다.
    """
    root = PERSONAS_DIR.parent
    card = CharacterCard.load(PERSONAS_DIR / "example_jp.yaml", root=root)
    assert card.voice is not None and card.voice.name == "example"
    vv = card.voice.vendor("gptsovits")
    assert vv.ckpts == ("", "")  # ckpt 생략 = base zero-shot
    # ja = 발화 언어는 레퍼런스 음성 언어에 맞춤(2026.07.14 — 한국어 G2P(eunjeon)
    # Windows 빌드 벽으로 KO 경로 미개통, 검증된 JA 경로 사용)
    assert (vv.ref_lang, vv.gen_lang) == ("ja", "ja")
    tone = card.voice.default_tone("gptsovits")
    assert tone.ref_text  # 레퍼런스 wav와 한 쌍인 전사
    assert Path(tone.voice_id).is_absolute()  # root로 절대화됨
    assert tone.voice_id.endswith("example_ref.wav")


def test_card_without_voice_section_is_none(tmp_path):
    """하위호환 — voice 섹션 없는 카드는 voice=None (config mouth.voice 폴백 경로)."""
    p = tmp_path / "old.yaml"
    p.write_text(
        "character: 옛카드\n"
        "profiles:\n"
        "  - {name: 기본, min_intimacy: 0, background: b, traits: t,\n"
        "     example_dialogues: [{user: u, assistant: a}]}\n",
        encoding="utf-8",
    )
    assert CharacterCard.load(p).voice is None


def test_example_kr_card_has_no_voice_section():
    """공개 예시 카드(KR) — voice 생략 = 부팅 시점 mouth 엔진을 그대로 사용.

    supertonic 부팅 세션 전용(한국어 완전 지원). SwapRuntime은 엔진을 런타임 교체하지
    않으므로 gptsovits 세션에 섞으면 안 된다. 한국어 G2P(eunjeon) 빌드 벽이 풀리기
    전까지 gptsovits voice 섹션을 붙이지 않는다(2026.07.15).
    """
    card = CharacterCard.load(PERSONAS_DIR / "example_kr.yaml")
    assert card.voice is None
    assert card.character == "소민"


def test_voice_parse_resolves_gptsovits_paths_with_root(tmp_path):
    """root를 주면 gptsovits 경로(wav·ckpt)는 절대화, supertonic 프리셋명은 통과."""
    raw = {
        "name": "navi",
        "speed": 1.1,
        "supertonic": {"tones": [{"name": "기본", "voice_id": "F1"}]},
        "gptsovits": {
            "gpt_ckpt": "voice_ref/a.ckpt",
            "sovits_ckpt": "voice_ref/b.pth",
            "tones": [{"name": "기본", "voice_id": "ref/base.wav", "ref_text": "전사"}],
        },
    }
    voice = PersonaVoice.parse(raw, root=tmp_path)
    gv = voice.vendor("gptsovits")
    assert Path(gv.gpt_ckpt).is_absolute() and gv.gpt_ckpt.endswith("a.ckpt")
    assert Path(gv.tones[0].voice_id).is_absolute()
    assert voice.default_tone("supertonic").voice_id == "F1"  # 프리셋명 원문 유지
    # root 없이(순수 파싱)는 원문 유지
    assert PersonaVoice.parse(raw).vendor("gptsovits").gpt_ckpt == "voice_ref/a.ckpt"


def test_voice_profile_built_from_tone():
    """톤 → VoiceProfile 변환 — name/speed는 목소리, voice_id/ref_text는 톤에서."""
    raw = {
        "name": "aris",
        "speed": 0.9,
        "gptsovits": {
            "tones": [
                {"name": "기본", "voice_id": "base.wav", "ref_text": "기본 전사"},
                {"name": "신남", "icon": "mood-happy", "voice_id": "happy.wav",
                 "ref_text": "신나는 전사"},
            ]
        },
    }
    voice = PersonaVoice.parse(raw)
    tones = voice.vendor("gptsovits").tones
    profile = voice.profile(tones[1])
    assert profile.name == "aris" and profile.speed == 0.9
    assert profile.vendor_voice_id == "happy.wav"
    assert profile.ref_text == "신나는 전사"
    assert voice.default_tone("gptsovits") is tones[0]  # 첫 항목이 기본


def test_voice_vendor_without_tones_has_no_default():
    """톤 없는 벤더 섹션 — 기본 톤 없음(부팅 시 config 폴백 경로)."""
    voice = PersonaVoice.parse({"name": "x", "gptsovits": {"gpt_ckpt": "a.ckpt"}})
    assert voice.default_tone("gptsovits") is None
    assert voice.vendor("없는벤더") is None
