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
