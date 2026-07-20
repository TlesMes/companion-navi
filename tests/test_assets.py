"""커밋 자산 무결성 — 클론한 사람이 받는 것이 실제로 도는가 (E6-1).

여기만 tmp_path 픽스처가 아니라 **진짜 리포 루트**를 본다. 이 파일의 목적이
"이 리포를 클론하면 무엇이 딸려 오는가"라서, 합성한 트리로는 검증이 성립하지 않는다.

웨이크워드 모델이 없으면 `--wakeword` 기동은 데몬이 뜨는 게 아니라 죽는다
(navi/daemon.py의 `config.wakeword.ready` 검사 → print 후 return). E6-4의 실행 버튼은
detached+DEVNULL이라 그 print마저 사라지므로, "모델이 리포에 있다"는 계약을 여기서 박아
누가 다시 gitignore하거나 경로를 바꾸면 테스트가 먼저 잡게 한다.
"""

from pathlib import Path

from navi.config import load_config

_ROOT = Path(__file__).resolve().parents[1]
_WAKEWORD_MODEL = _ROOT / "assets" / "wakeword" / "navi_ko.onnx"


def test_wakeword_model_is_committed_asset():
    """모델이 커밋 자산으로 실재한다 — secrets/(커밋 금지)가 아니라 assets/에."""
    assert _WAKEWORD_MODEL.is_file(), (
        f"웨이크워드 모델 없음: {_WAKEWORD_MODEL} — "
        "assets/wakeword/README.md 참조(secrets/로 되돌리면 클론 기동이 깨진다)"
    )


def test_config_points_at_the_bundled_wakeword_model():
    """config.yaml의 경로가 그 실물을 가리킨다 — 자산과 설정이 어긋나면 둘 다 무의미하다."""
    config = load_config(_ROOT)
    assert config.wakeword.owww_model_path is not None
    assert Path(config.wakeword.owww_model_path) == _WAKEWORD_MODEL.resolve()


def test_wakeword_is_ready_on_a_fresh_clone():
    """`ready`가 참 — "클론 직후 --wakeword가 살아남는다"의 직접 표현.

    판정은 WakeWordConfig.ready를 그대로 쓴다(데몬이 부팅에 쓰는 바로 그 함수) —
    여기서 파일 존재를 따로 재구현하면 데몬과 어긋날 수 있다.
    """
    assert load_config(_ROOT).wakeword.ready is True
