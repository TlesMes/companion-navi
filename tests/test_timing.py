"""2층 타이밍(arch 4.4) 검증 — 순수 함수라 시계·jitter 주입만으로 결정론 테스트.

범위 유지: "판단 함수가 존재하고 값을 반환한다"까지가 목표(진행 원칙 2).
좋은 임계값을 여기서 검증하지 않는다 — min_gap 게이트·가중치 방향·jitter 주입만 본다.
"""

from datetime import datetime, timedelta

from navi.heartbeat.timing import draw_jitter, should_initiate, time_of_day

WEIGHTS = {"morning": 1.2, "afternoon": 1.0, "evening": 1.1, "night": 0.5}


def test_time_of_day_buckets():
    assert time_of_day(datetime(2026, 7, 12, 8)) == "morning"
    assert time_of_day(datetime(2026, 7, 12, 14)) == "afternoon"
    assert time_of_day(datetime(2026, 7, 12, 20)) == "evening"
    assert time_of_day(datetime(2026, 7, 12, 2)) == "night"


def test_no_history_never_initiates():
    # 상호작용 이력이 없으면 콜드 오픈하지 않는다
    assert should_initiate(
        datetime(2026, 7, 12, 10), None, WEIGHTS, 1.0,
        base_interval_s=3600, min_gap_s=1800,
    ) is False


def test_min_gap_blocks_regardless_of_weight():
    now = datetime(2026, 7, 12, 10)
    last = now - timedelta(seconds=600)  # 10분 전 — min_gap(30분) 안
    assert should_initiate(
        now, last, WEIGHTS, 1.0, base_interval_s=3600, min_gap_s=1800
    ) is False


def test_fires_once_effective_interval_elapsed():
    now = datetime(2026, 7, 12, 10)  # morning, weight 1.2 → 유효간격 3600/1.2=3000s
    last = now - timedelta(seconds=3100)
    assert should_initiate(
        now, last, WEIGHTS, 1.0, base_interval_s=3600, min_gap_s=1800
    ) is True


def test_higher_weight_fires_sooner():
    # 같은 elapsed·jitter에서 가중치 큰 시간대가 먼저 발화 (유효 간격이 짧아짐)
    now = datetime(2026, 7, 12, 10)
    last = now - timedelta(seconds=3200)
    active = {"morning": 1.2}   # 유효 3000s → elapsed 3200 넘김
    quiet = {"morning": 0.5}    # 유효 7200s → 아직
    assert should_initiate(now, last, active, 1.0, base_interval_s=3600, min_gap_s=1800)
    assert not should_initiate(now, last, quiet, 1.0, base_interval_s=3600, min_gap_s=1800)


def test_jitter_shifts_threshold():
    now = datetime(2026, 7, 12, 14)  # afternoon, weight 1.0 → 기준 유효간격 3600s
    last = now - timedelta(seconds=3800)
    # jitter<1이면 유효간격 축소 → 발화, jitter>1이면 확대 → 침묵
    assert should_initiate(now, last, WEIGHTS, 0.9, base_interval_s=3600, min_gap_s=1800)
    assert not should_initiate(now, last, WEIGHTS, 1.2, base_interval_s=3600, min_gap_s=1800)


def test_draw_jitter_within_range():
    import random

    rng = random.Random(0)
    for _ in range(50):
        j = draw_jitter((0.8, 1.2), rng)
        assert 0.8 <= j <= 1.2
