"""config.local.yaml 오버레이 계층 (E6-3).

머신 전용값(마이크 임계 등)이 커밋 파일에서 분리되도록 하는 계층이다. 가장 조심할 지점은
**얕은 병합으로 `ear:` 한 줄을 덮으려다 그 아래 wakeword 설정을 통째로 날리는 사고** —
그래서 `_deep_merge`가 dict끼리는 재귀한다. 그 계약을 여기서 계약화한다.
"""

from pathlib import Path

import yaml

from navi.config import _deep_merge, _load_raw, load_config

_CONFIG = {
    "brain": {"vendor": "echo", "models": {"echo": "echo"}},
    "mouth": {
        "vendor": "supertonic",
        "voice": {"name": "navi", "speed": 1.0},
        "supertonic": {"voice_id": "F1", "lang": "ko"},
    },
    "db": {"path": "navi.db"},
    "memory": {"recent_turns": 6},
    "persona": {"card_path": "personas/navi.yaml"},
    "ear": {
        "wakeword": {
            "engine": "openwakeword",
            "openwakeword": {"model_path": "assets/wakeword/navi_ko.onnx", "threshold": 0.5},
        },
    },
}

_CARD = {
    "character": "테스트",
    "profiles": [{
        "name": "기본", "min_intimacy": 0.0, "background": "b", "traits": "t",
        "example_dialogues": [{"user": "a", "assistant": "b"}],
    }],
    "voice": {"name": "navi", "supertonic": {"tones": [{"name": "기본", "voice_id": "F1"}]}},
}


def _write_repo(tmp_path: Path, *, local: dict | str | None = None) -> Path:
    """config.yaml + 카드 + (선택) config.local.yaml을 깐 리포 루트."""
    (tmp_path / "config.yaml").write_text(yaml.safe_dump(_CONFIG), encoding="utf-8")
    (tmp_path / "personas").mkdir()
    (tmp_path / "personas" / "navi.yaml").write_text(
        yaml.safe_dump(_CARD, allow_unicode=True), encoding="utf-8"
    )
    if local is not None:
        text = local if isinstance(local, str) else yaml.safe_dump(local)
        (tmp_path / "config.local.yaml").write_text(text, encoding="utf-8")
    return tmp_path


# --- _deep_merge 단위 ----------------------------------------------------


def test_deep_merge_recurses_into_dicts():
    """중첩 dict는 파고들어야 한다 — 이 성질이 없으면 `ear:` 한 줄이 wakeword를 통째로 날린다."""
    base = {"ear": {"wakeword": {"threshold": 0.5, "engine": "owww"}}}
    overlay = {"ear": {"wakeword": {"threshold": 0.7}}}
    assert _deep_merge(base, overlay) == {
        "ear": {"wakeword": {"threshold": 0.7, "engine": "owww"}}
    }


def test_deep_merge_replaces_lists_wholesale():
    """리스트는 통째 교체 — 원소 단위 병합은 의도가 모호하다(추가? 위치 매칭?)."""
    assert _deep_merge({"xs": [1, 2, 3]}, {"xs": [9]}) == {"xs": [9]}


def test_deep_merge_replaces_when_types_differ():
    """base가 dict가 아니거나 overlay가 dict가 아니면 재귀 안 함 — 통째 교체."""
    assert _deep_merge({"x": 1}, {"x": {"y": 2}}) == {"x": {"y": 2}}
    assert _deep_merge({"x": {"y": 2}}, {"x": 1}) == {"x": 1}


# --- _load_raw 파일 통합 --------------------------------------------------


def test_load_raw_returns_base_when_no_local(tmp_path):
    """가장 흔한 경우 — config.local.yaml 없음. base 그대로."""
    _write_repo(tmp_path)
    assert _load_raw(tmp_path)["brain"]["vendor"] == "echo"


def test_load_raw_overlay_wins_and_neighbors_survive(tmp_path):
    """오버레이가 threshold를 덮되 model_path 등 이웃은 살아남는가 (핵심 계약)."""
    _write_repo(tmp_path, local={"ear": {"wakeword": {"openwakeword": {"threshold": 0.9}}}})
    owww = _load_raw(tmp_path)["ear"]["wakeword"]["openwakeword"]
    assert owww["threshold"] == 0.9
    assert owww["model_path"] == "assets/wakeword/navi_ko.onnx"


def test_load_raw_ignores_non_mapping_local(tmp_path, caplog):
    """오버레이가 매핑이 아니면 warning 후 무시 — 나쁜 파일 하나로 부팅을 못 하게 되는 게 더 나쁘다."""
    _write_repo(tmp_path, local="- a\n- b\n")  # 리스트
    assert _load_raw(tmp_path)["brain"]["vendor"] == "echo"
    assert any("매핑이 아니" in rec.message for rec in caplog.records)


def test_load_raw_treats_empty_file_as_no_overlay(tmp_path):
    """빈 파일(None)은 조용히 통과 — 아직 안 채운 상태가 워닝일 이유가 없다."""
    _write_repo(tmp_path, local="")
    assert _load_raw(tmp_path)["brain"]["vendor"] == "echo"


# --- load_config 통합 -----------------------------------------------------


def test_load_config_applies_overlay(tmp_path):
    """오버레이가 실제 Config에 반영되는가 — 브랜치 하나만 바꿔 확인(다른 필드 변경은 뒤 커밋)."""
    _write_repo(tmp_path, local={"ear": {"wakeword": {"openwakeword": {"threshold": 0.9}}}})
    assert load_config(tmp_path).wakeword.threshold == 0.9


def test_energy_vad_threshold_default_is_zero(tmp_path):
    """미설정 = 0 = "daemon 기본(150)"으로 폴백 — 이 규약이 안 지켜지면 스크립트 무인자 기동이 깨진다."""
    _write_repo(tmp_path)
    assert load_config(tmp_path).energy_vad_threshold == 0.0


def test_energy_vad_threshold_overlay_beats_base(tmp_path):
    """이 필드가 config.local.yaml로 옮기려는 첫 실사용값 — 오버레이가 실제로 이겨야 의미가 있다."""
    _write_repo(tmp_path, local={"ear": {"energy_vad_threshold": 50}})
    assert load_config(tmp_path).energy_vad_threshold == 50.0


def test_energy_vad_threshold_from_base_yaml(tmp_path):
    """config.yaml에 직접 넣어도 읽힌다(오버레이 없이도 동작)."""
    import yaml as _yaml
    config = dict(_CONFIG)
    config["ear"] = {**_CONFIG["ear"], "energy_vad_threshold": 80}
    (tmp_path / "config.yaml").write_text(_yaml.safe_dump(config), encoding="utf-8")
    (tmp_path / "personas").mkdir()
    (tmp_path / "personas" / "navi.yaml").write_text(
        _yaml.safe_dump(_CARD, allow_unicode=True), encoding="utf-8"
    )
    assert load_config(tmp_path).energy_vad_threshold == 80.0


# --- DB 층 (사용자 오버라이드) ---------------------------------------------
#
# 층위: config.yaml < config.local.yaml < DB < CLI. 여기서 계약화하는 건 DB가 파일 두
# 층을 이기고 CLI에는 진다는 것 — 이 순서가 뒤집히면 "이번 실행만"이 저장된 선택에
# 덮이거나(--brain 무력화), GUI에서 고른 게 재기동마다 사라진다(gui.md:121의 구멍).


def _set_setting(db_path, key, value):
    """setting 테이블에 오버라이드를 심는다 — MemoryStore가 스키마를 만든다."""
    from navi.memory.store import MemoryStore

    store = MemoryStore(db_path)
    store.set_setting(key, value)
    store.close()


def test_db_setting_beats_yaml(tmp_path):
    """GUI에서 고른 벤더가 config.yaml(echo)을 이긴다 — 재기동해도 유지되는 근거."""
    _write_repo(tmp_path)
    _set_setting(tmp_path / "navi.db", "brain.vendor", "anthropic")
    assert load_config(tmp_path).brain.vendor == "anthropic"


def test_db_setting_beats_local_overlay(tmp_path):
    """머신 전용 파일보다도 사용자 선택이 위 — 사용자 오버라이드는 자동 판단을 이긴다."""
    _write_repo(tmp_path, local={"brain": {"vendor": "gemini"}})
    _set_setting(tmp_path / "navi.db", "brain.vendor", "anthropic")
    assert load_config(tmp_path).brain.vendor == "anthropic"


def test_cli_beats_db_setting(tmp_path):
    """CLI는 최상위 — "이번 실행만"이 저장된 선택보다 좁고 명시적이다."""
    _write_repo(tmp_path)
    _set_setting(tmp_path / "navi.db", "brain.vendor", "anthropic")
    assert load_config(tmp_path, brain_vendor="gemini").brain.vendor == "gemini"


def test_db_layer_follows_cli_db_path(tmp_path):
    """--db로 다른 DB를 지목하면 DB 층도 그쪽을 읽는다.

    CLI 오버라이드를 load_config 밖에서 replace하면 이게 깨진다 — 층이 yaml의
    db_path(navi.db)를 읽어 엉뚱한 DB의 설정을 적용한다.
    """
    _write_repo(tmp_path)
    _set_setting(tmp_path / "navi.db", "brain.vendor", "anthropic")  # 읽히면 안 되는 쪽
    other = tmp_path / "other.db"
    _set_setting(other, "brain.vendor", "gemini")
    assert load_config(tmp_path, db_path=other).brain.vendor == "gemini"


def test_unknown_setting_key_is_ignored(tmp_path):
    """화이트리스트 밖은 무시 — db.path를 DB에서 읽는 부트스트랩 순환을 막는 가드."""
    _write_repo(tmp_path)
    _set_setting(tmp_path / "navi.db", "db.path", "/etc/passwd")
    assert load_config(tmp_path).db_path == tmp_path / "navi.db"


def test_garbage_vendor_value_is_ignored(tmp_path):
    """DB에 쓰레기가 들어가도 데몬은 뜬다 — 설정 하나로 나비가 안 깨어나면 안 된다."""
    _write_repo(tmp_path)
    _set_setting(tmp_path / "navi.db", "brain.vendor", "openai")
    assert load_config(tmp_path).brain.vendor == "echo"  # config.yaml 값 유지


def test_load_config_does_not_create_db(tmp_path):
    """설정을 읽는 행위가 파일을 만들면 안 된다 — preflight·GUI 런처도 load_config를 부른다."""
    _write_repo(tmp_path)
    assert load_config(tmp_path).brain.vendor == "echo"
    assert not (tmp_path / "navi.db").exists()


def test_db_without_setting_table_is_tolerated(tmp_path):
    """구 DB(테이블 없음)를 만나도 기본 설정으로 진행한다 — 마이그레이션 경로가 없는 리포다."""
    import sqlite3 as _sqlite3

    _write_repo(tmp_path)
    _sqlite3.connect(tmp_path / "navi.db").close()  # 빈 DB 파일
    assert load_config(tmp_path).brain.vendor == "echo"


def test_api_keys_are_not_in_repr(tmp_path):
    """Config repr에 키 원문이 없어야 한다 — 예외 트레이스백의 지역변수 덤프가 이걸 탄다."""
    import os

    _write_repo(tmp_path)
    os.environ["ANTHROPIC_API_KEY"] = "sk-ant-secret-value-1234"
    try:
        config = load_config(tmp_path)
    finally:
        del os.environ["ANTHROPIC_API_KEY"]
    assert config.anthropic_api_key == "sk-ant-secret-value-1234"
    assert "sk-ant-secret" not in repr(config)
