"""부팅 전 판정 — 무엇으로 띄울 수 있는가 (E6-2).

이 파일이 지키는 핵심 계약은 **부팅 판정 ≠ 런타임 교체 판정**이다. 레퍼런스 wav 부재는
교체(E3 `availability()` ④-a)에선 차단이지만 부팅에선 warning만이고 데몬은 뜬다.
두 판정을 같은 것으로 착각해 합치면 런처가 "뜰 수 있는 조합을 못 뜬다고" 말하게 된다.

전부 tmp_path — torch·마이크·네트워크·포트 불요.
"""

from pathlib import Path

import yaml

from navi.preflight import TEXT_ONLY, evaluate

_CONFIG = {
    "brain": {"vendor": "echo", "models": {"echo": "echo"}},
    "mouth": {
        "vendor": "supertonic",
        "voice": {"name": "navi", "speed": 1.0},
        "supertonic": {"voice_id": "F1", "lang": "ko"},
        "gptsovits": {"repo_path": "gptsovits-repo"},
    },
    "db": {"path": "navi.db"},
    "memory": {"recent_turns": 6},
    "persona": {"card_path": "personas/navi.yaml"},
    "ear": {"wakeword": {"openwakeword": {"model_path": "assets/wakeword/navi_ko.onnx"}}},
}

_PROFILE = {
    "name": "기본",
    "min_intimacy": 0.0,
    "background": "배경",
    "traits": "성격",
    "example_dialogues": [{"user": "안녕", "assistant": "안녕!"}],
}


def _card(character: str, voice: dict | None) -> dict:
    card = {"character": character, "profiles": [_PROFILE]}
    if voice is not None:
        card["voice"] = voice
    return card


def _write_root(tmp_path: Path, cards: dict[str, dict], *, base_ckpts: bool = True) -> Path:
    """config.yaml + 카드들 + (선택) gptsovits base 가중치를 깐 리포 루트를 만든다."""
    (tmp_path / "config.yaml").write_text(yaml.safe_dump(_CONFIG), encoding="utf-8")
    (tmp_path / "personas").mkdir()
    for name, card in cards.items():
        (tmp_path / "personas" / f"{name}.yaml").write_text(
            yaml.safe_dump(card, allow_unicode=True), encoding="utf-8"
        )
    # 웨이크워드 모델(E6-1 커밋 자산) 자리
    ww = tmp_path / "assets" / "wakeword"
    ww.mkdir(parents=True)
    (ww / "navi_ko.onnx").write_bytes(b"onnx")
    # gptsovits repo + base 가중치
    pre = tmp_path / "gptsovits-repo" / "GPT_SoVITS" / "pretrained_models"
    (pre / "v2Pro").mkdir(parents=True)
    (pre / "sv").mkdir(parents=True)
    if base_ckpts:
        (pre / "s1v3.ckpt").write_bytes(b"x")
        (pre / "v2Pro" / "s2Gv2ProPlus.pth").write_bytes(b"x")
        (pre / "sv" / "pretrained_eres2netv2w24s4ep4.ckpt").write_bytes(b"x")
    return tmp_path


def _engine(report, engine_id):
    return next(e for e in report.engines if e.id == engine_id)


def _card_opt(report, engine_id, card_id):
    return next(c for c in _engine(report, engine_id).cards if c.id == card_id)


# --- 엔진 매핑 -----------------------------------------------------------


def test_card_lands_on_the_engine_its_bundle_declares(tmp_path):
    """엔진은 카드가 정한다 — supertonic 카드와 gptsovits 카드가 서로 다른 목록에 선다.

    preflight가 엔진을 직접 계산하지 않고 load_config(persona_card=)를 쓰는 이유가
    이것이다: 데몬이 실제로 부팅할 엔진과 같아야 한다.
    """
    _write_root(tmp_path, {
        "navi": _card("나비", {"name": "navi", "supertonic": {"tones": [{"name": "기본", "voice_id": "F1"}]}}),
        "rei": _card("레이", {"name": "rei", "gptsovits": {"ref_lang": "ja", "gen_lang": "ja"}}),
    })
    report = evaluate(tmp_path)
    assert [c.id for c in _engine(report, "supertonic").cards] == ["navi"]
    assert [c.id for c in _engine(report, "gptsovits").cards] == ["rei"]


def test_supertonic_card_boots_with_zero_files(tmp_path):
    """supertonic voice_id는 프리셋명이라 파일이 필요 없다 — 프레시 클론이 도는 근거."""
    _write_root(tmp_path, {
        "navi": _card("나비", {"name": "navi", "supertonic": {"tones": [{"name": "기본", "voice_id": "F1"}]}}),
    })
    assert _card_opt(evaluate(tmp_path), "supertonic", "navi").bootable is True


# --- launch_persona: 런처가 이 엔진으로 띄울 카드 (부품 0, E6-4) -----------


def test_launch_persona_prefers_config_default(tmp_path):
    """config 기본(navi)이 그 엔진에 있고 부팅 가능하면 그것 — 알파벳 첫 카드(소민)가 아니라.

    이게 없으면 supertonic 클릭이 테스트 페르소나 example_kr(소민)을 띄운다(카드 정렬이 알파벳).
    """
    _write_root(tmp_path, {
        "example_kr": _card("소민", None),  # voice 섹션 없음 → supertonic 폴백, 알파벳상 앞
        "navi": _card("나비", {"name": "navi", "supertonic": {"tones": [{"name": "기본", "voice_id": "F1"}]}}),
    })
    assert _engine(evaluate(tmp_path), "supertonic").launch_persona == "navi"


def test_launch_persona_falls_back_to_first_bootable(tmp_path):
    """config 기본이 그 엔진에 없으면(navi=supertonic, gptsovits엔 없음) 첫 부팅가능 카드."""
    _write_root(tmp_path, {
        "navi": _card("나비", {"name": "navi", "supertonic": {"tones": [{"name": "기본", "voice_id": "F1"}]}}),
        "rei": _card("레이", {"name": "rei", "gptsovits": {"ref_lang": "ja", "gen_lang": "ja"}}),
    })
    assert _engine(evaluate(tmp_path), "gptsovits").launch_persona == "rei"


def test_launch_persona_skips_unbootable(tmp_path):
    """부팅 불가 카드는 건너뛴다 — 클릭했을 때 못 뜨는 걸 넘기면 안 된다."""
    _write_root(tmp_path, {
        "aris": _card("아리스", {"name": "aris", "gptsovits": {
            "gpt_ckpt": "voice_ref/none.ckpt", "sovits_ckpt": "voice_ref/none.pth",  # 없음 → 차단
        }}),
        "rei": _card("레이", {"name": "rei", "gptsovits": {"ref_lang": "ja", "gen_lang": "ja"}}),  # base로 부팅
    })
    # aris가 알파벳상 앞이지만 차단이라 rei가 뽑힌다
    assert _engine(evaluate(tmp_path), "gptsovits").launch_persona == "rei"


def test_launch_persona_none_for_text_only(tmp_path):
    """목소리 없이는 -Mode text라 -Persona가 없다 → None."""
    _write_root(tmp_path, {
        "navi": _card("나비", {"name": "navi", "supertonic": {"tones": [{"name": "기본", "voice_id": "F1"}]}}),
    })
    assert _engine(evaluate(tmp_path), "none").launch_persona is None


# --- 차단 vs 경고 (이 모듈의 핵심 정확성) --------------------------------


def test_missing_ckpt_blocks_boot(tmp_path):
    """카드가 가리키는 fine-tune 가중치 부재 = 차단(웜업이 FileNotFoundError로 죽는다)."""
    _write_root(tmp_path, {
        "aris": _card("아리스", {"name": "aris", "gptsovits": {
            "gpt_ckpt": "voice_ref/none.ckpt", "sovits_ckpt": "voice_ref/none.pth",
        }}),
    })
    card = _card_opt(evaluate(tmp_path), "gptsovits", "aris")
    assert card.bootable is False
    assert "가중치" in card.reason


def test_missing_reference_wav_warns_but_still_boots(tmp_path):
    """**부팅 ≠ 교체.** 레퍼런스 wav가 없어도 데몬은 뜬다 — 그 톤으로 말할 때 터질 뿐이다.

    E3의 availability()는 같은 상황(④-a)을 차단한다. 그 판정을 여기 그대로 재사용하면
    런처가 뜰 수 있는 조합을 못 뜬다고 말하게 된다 — 이 테스트가 그 혼동을 막는다.
    """
    _write_root(tmp_path, {
        "rei": _card("레이", {"name": "rei", "gptsovits": {
            "tones": [{"name": "기본", "voice_id": "voice_ref/none.wav"}],
        }}),
    })
    card = _card_opt(evaluate(tmp_path), "gptsovits", "rei")
    assert card.bootable is True
    assert any("레퍼런스" in w for w in card.warnings)


# --- gptsovits 전용 전제 -------------------------------------------------


def test_missing_base_ckpts_block_zero_shot_card(tmp_path):
    """ckpt 미선언 = base(zero-shot) 의도 — base 가중치가 없으면 못 뜬다.

    example_jp(커밋 카드)가 이 경로라 클론 가능성에 직결된다. 판정은 어댑터의
    missing_base_ckpts를 그대로 쓴다(경로 지식 이중화 금지).
    """
    _write_root(tmp_path, {
        "rei": _card("레이", {"name": "rei", "gptsovits": {"ref_lang": "ja"}}),
    }, base_ckpts=False)
    card = _card_opt(evaluate(tmp_path), "gptsovits", "rei")
    assert card.bootable is False
    assert "base" in card.reason


def test_missing_repo_blocks_gptsovits(tmp_path):
    """엔진 repo 자체가 없으면 base든 fine-tune이든 못 뜬다."""
    root = _write_root(tmp_path, {
        "rei": _card("레이", {"name": "rei", "gptsovits": {"ref_lang": "ja"}}),
    })
    config = yaml.safe_load((root / "config.yaml").read_text(encoding="utf-8"))
    config["mouth"]["gptsovits"]["repo_path"] = "없는-repo"
    (root / "config.yaml").write_text(yaml.safe_dump(config), encoding="utf-8")
    card = _card_opt(evaluate(root), "gptsovits", "rei")
    assert card.bootable is False
    assert "repo" in card.reason


# --- 견고성·전제조건 -----------------------------------------------------


def test_broken_card_is_isolated_not_fatal(tmp_path):
    """카드 하나가 깨져도 나머지 진단은 산다 — 한 장이 전체를 벽돌로 만들지 않는다(E1 교훈)."""
    root = _write_root(tmp_path, {
        "navi": _card("나비", {"name": "navi", "supertonic": {"tones": [{"name": "기본", "voice_id": "F1"}]}}),
    })
    (root / "personas" / "broken.yaml").write_text("character: [깨진\n", encoding="utf-8")
    report = evaluate(root)

    assert _card_opt(report, "supertonic", "navi").bootable is True
    # 깨진 카드는 어느 엔진 목록에도 없고, 목소리 없이에서도 못 쓴다
    assert "broken" not in [c.id for c in _engine(report, "supertonic").cards]
    broken = _card_opt(report, TEXT_ONLY, "broken")
    assert broken.bootable is False
    assert "\n" not in broken.reason  # 사유는 한 줄 — GUI 토스트용


def test_text_only_ignores_voice_assets(tmp_path):
    """목소리 없이(점검용)는 엔진을 안 쓰므로 음색 자산 부재와 무관하게 선다(D17)."""
    _write_root(tmp_path, {
        "aris": _card("아리스", {"name": "aris", "gptsovits": {
            "gpt_ckpt": "voice_ref/none.ckpt", "sovits_ckpt": "voice_ref/none.pth",
        }}),
    })
    report = evaluate(tmp_path)
    assert _card_opt(report, "gptsovits", "aris").bootable is False
    assert _card_opt(report, TEXT_ONLY, "aris").bootable is True
    assert _card_opt(report, TEXT_ONLY, "aris").character == "아리스"  # 이름 재사용


def test_brain_keys_are_reported_per_vendor(monkeypatch, tmp_path):
    """run_navi의 기본값(-Brain anthropic)을 복제하지 않고 벤더별 가용성만 보고한다."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "k")
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    _write_root(tmp_path, {
        "navi": _card("나비", {"name": "navi", "supertonic": {"tones": [{"name": "기본", "voice_id": "F1"}]}}),
    })
    pre = evaluate(tmp_path).prerequisites
    assert pre.brains["anthropic"] is True
    assert pre.brains["gemini"] is False
    assert pre.brains["echo"] is True
    assert pre.any_brain is True


def test_no_api_key_blocks_every_option(monkeypatch, tmp_path):
    """키가 하나도 없으면 부팅이 create_brain에서 즉사한다 — 엔진을 고르기 전에 막는다."""
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    _write_root(tmp_path, {
        "navi": _card("나비", {"name": "navi", "supertonic": {"tones": [{"name": "기본", "voice_id": "F1"}]}}),
    })
    report = evaluate(tmp_path)
    assert report.prerequisites.any_brain is False
    assert _engine(report, "supertonic").bootable is False
    assert _engine(report, TEXT_ONLY).bootable is False
    assert "키" in _engine(report, TEXT_ONLY).reason


def test_wakeword_readiness_comes_from_the_daemon_judgment(tmp_path):
    """웨이크워드 판정은 WakeWordConfig.ready 재사용 — 여기서 파일 존재를 재구현하지 않는다."""
    root = _write_root(tmp_path, {
        "navi": _card("나비", {"name": "navi", "supertonic": {"tones": [{"name": "기본", "voice_id": "F1"}]}}),
    })
    assert evaluate(root).prerequisites.wakeword_ready is True
    (root / "assets" / "wakeword" / "navi_ko.onnx").unlink()
    assert evaluate(root).prerequisites.wakeword_ready is False
