"""비밀의 자리 — .env 부분 갱신과 표시용 마스킹.

여기서 못 박는 계약은 하나다: **키 하나 바꾼다고 나머지가 날아가면 안 된다.** .env엔
주석과 다른 벤더의 키가 같이 산다(.env.example가 그 형태다). GUI에서 두뇌를 갈아끼우는
경로가 이 함수를 타므로, 통째로 덮는 구현이 들어오면 사용자의 다른 키가 사라진다.
"""

import pytest

from navi.secrets import mask_key, write_env_key

_EXISTING = """# 실제 키는 이 파일을 .env로 복사해서 채울 것
GEMINI_API_KEY=AIzaSy-gemini-original
ANTHROPIC_API_KEY=sk-ant-old

# 아래는 다른 용도
PICOVOICE_ACCESS_KEY="quoted-value"
"""


def _write(tmp_path, text=_EXISTING):
    (tmp_path / ".env").write_text(text, encoding="utf-8")
    return tmp_path


def test_replaces_only_the_target_line(tmp_path):
    """대상 줄만 교체 — 주석·빈 줄·다른 키·인용부호가 그대로 남아야 한다."""
    _write(tmp_path)
    write_env_key(tmp_path, "ANTHROPIC_API_KEY", "sk-ant-new")

    text = (tmp_path / ".env").read_text(encoding="utf-8")
    assert "ANTHROPIC_API_KEY=sk-ant-new" in text
    assert "sk-ant-old" not in text
    assert "GEMINI_API_KEY=AIzaSy-gemini-original" in text  # 다른 벤더 키 보존
    assert 'PICOVOICE_ACCESS_KEY="quoted-value"' in text    # 인용부호 보존
    assert text.startswith("# 실제 키는")                     # 주석 보존
    assert "\n\n# 아래는 다른 용도" in text                    # 빈 줄 보존


def test_appends_when_key_absent(tmp_path):
    """없던 키는 말미에 추가 — 기존 내용은 손대지 않는다."""
    _write(tmp_path, "# 주석만 있는 파일\n")
    write_env_key(tmp_path, "GEMINI_API_KEY", "AIzaSy-new")

    text = (tmp_path / ".env").read_text(encoding="utf-8")
    assert text == "# 주석만 있는 파일\nGEMINI_API_KEY=AIzaSy-new\n"


def test_creates_file_when_missing(tmp_path):
    """첫 키 입력 — .env가 아직 없는 게 정상이다(gitignore라 클론엔 없다)."""
    path = write_env_key(tmp_path, "GEMINI_API_KEY", "AIzaSy-first")
    assert path.read_text(encoding="utf-8") == "GEMINI_API_KEY=AIzaSy-first\n"


def test_matches_export_prefixed_line(tmp_path):
    """`export NAME=` 도 python-dotenv가 읽는 형태 — 갱신 대상에서 빠지면 중복 줄이 생긴다."""
    _write(tmp_path, "export GEMINI_API_KEY=old\n")
    write_env_key(tmp_path, "GEMINI_API_KEY", "new")

    text = (tmp_path / ".env").read_text(encoding="utf-8")
    assert text.count("GEMINI_API_KEY") == 1
    assert "old" not in text


def test_does_not_match_similar_key_name(tmp_path):
    """접두사가 같은 다른 키를 건드리면 안 된다(GEMINI_API_KEY vs GEMINI_API_KEY_BACKUP)."""
    _write(tmp_path, "GEMINI_API_KEY_BACKUP=keep-me\n")
    write_env_key(tmp_path, "GEMINI_API_KEY", "new")

    text = (tmp_path / ".env").read_text(encoding="utf-8")
    assert "GEMINI_API_KEY_BACKUP=keep-me" in text
    assert "GEMINI_API_KEY=new" in text


def test_rejects_newline_in_value(tmp_path):
    """값에 개행이 통과하면 값 하나로 다른 키 줄을 새로 심을 수 있다."""
    _write(tmp_path)
    with pytest.raises(ValueError):
        write_env_key(tmp_path, "GEMINI_API_KEY", "a\nANTHROPIC_API_KEY=stolen")
    assert "stolen" not in (tmp_path / ".env").read_text(encoding="utf-8")


def test_leaves_no_temp_file(tmp_path):
    """원자 교체용 임시 파일이 남으면 .env 옆에 키 사본이 굴러다닌다."""
    _write(tmp_path)
    write_env_key(tmp_path, "GEMINI_API_KEY", "new")
    assert [p.name for p in tmp_path.iterdir()] == [".env"]


# --- 마스킹 --------------------------------------------------------------


def test_mask_hides_the_secret_middle():
    """앞 6자(발급처 접두사)·뒤 4자만 남는다 — 원문이 응답에 실리면 안 된다."""
    key = "sk-ant-api03-verysecretmiddle-9f3a"
    masked = mask_key(key)
    assert masked == "sk-ant****9f3a"
    assert "verysecretmiddle" not in masked


def test_mask_hides_short_values_entirely():
    """짧은 값은 앞뒤만 남겨도 거의 전부가 노출된다 — 통째로 가린다."""
    assert mask_key("short-key") == "****"


def test_mask_of_missing_key_is_empty():
    assert mask_key(None) == ""
    assert mask_key("") == ""
