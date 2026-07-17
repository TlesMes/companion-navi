"""2층 타이밍(arch 4.4) 검증 — 산식이 tick 기반 hazard(변동확률)로 교체된 뒤.

확률 계산(initiation_probability)이 난수 없는 순수 함수라 **산식 전체가 결정론으로**
검증된다. 주사위(should_initiate)는 rng 주입으로 경계만 확인 — 이전 산식의 "jitter를
호출자가 뽑아 넣는다"는 우회가 필요 없어졌다.

범위 유지: 좋은 임계값을 여기서 검증하지 않는다(진행 원칙 2). 산식의 **꼴**만 고정 —
게이트(min_gap·콜드 오픈), 방향(가중치·경과), 그리고 이번 교체의 존재 이유인
**tick 빈도 중립성**.
"""

import math
import random
from datetime import datetime, timedelta

import pytest

from navi.heartbeat.timing import initiation_probability, should_initiate, time_of_day

WEIGHTS = {"morning": 1.2, "afternoon": 1.0, "evening": 1.1, "night": 0.5}
PARAMS = dict(base_interval_s=3600, min_gap_s=1800, tick_interval_s=10.0, shape_k=2.0)


def test_time_of_day_buckets():
    assert time_of_day(datetime(2026, 7, 12, 8)) == "morning"
    assert time_of_day(datetime(2026, 7, 12, 14)) == "afternoon"
    assert time_of_day(datetime(2026, 7, 12, 20)) == "evening"
    assert time_of_day(datetime(2026, 7, 12, 2)) == "night"


def test_no_history_never_initiates():
    # 상호작용 이력이 없으면 콜드 오픈하지 않는다
    assert initiation_probability(datetime(2026, 7, 12, 10), None, WEIGHTS, **PARAMS) == 0.0


def test_min_gap_blocks_regardless_of_weight():
    now = datetime(2026, 7, 12, 10)
    last = now - timedelta(seconds=600)  # 10분 전 — min_gap(30분) 안
    assert initiation_probability(now, last, WEIGHTS, **PARAMS) == 0.0
    # 하드 바닥이라 주사위를 아무리 굴려도 안 나간다
    assert should_initiate(now, last, WEIGHTS, rng=random.Random(0), **PARAMS) is False


def test_probability_rises_with_elapsed():
    """이번 교체의 핵심 — 오래 안 봤을수록 매 tick 발화 확률이 오른다(k>1)."""
    now = datetime(2026, 7, 12, 14)
    probs = [
        initiation_probability(now, now - timedelta(seconds=e), WEIGHTS, **PARAMS)
        for e in (1900, 3600, 7200, 14400)
    ]
    assert probs == sorted(probs)
    assert probs[0] < probs[-1]  # 단조 증가가 상수 나열이 아님을 확인


def test_shape_k_one_is_memoryless():
    """k=1이면 hazard 상수 — 경과와 무관하게 같은 확률(무기억 지수분포)."""
    now = datetime(2026, 7, 12, 14)
    params = {**PARAMS, "shape_k": 1.0}
    p_early = initiation_probability(now, now - timedelta(seconds=1900), WEIGHTS, **params)
    p_late = initiation_probability(now, now - timedelta(seconds=14400), WEIGHTS, **params)
    assert p_early == pytest.approx(p_late)


def test_higher_weight_raises_probability():
    # 같은 elapsed에서 가중치 큰 시간대가 더 높은 확률 (척도 λ가 짧아짐)
    now = datetime(2026, 7, 12, 10)
    last = now - timedelta(seconds=3600)
    active = initiation_probability(now, last, {"morning": 1.2}, **PARAMS)
    quiet = initiation_probability(now, last, {"morning": 0.5}, **PARAMS)
    assert active > quiet


def test_tick_frequency_neutrality():
    """tick이 잦아도 발화 시점 분포가 같다 — 이전 jitter 산식이 못 가졌던 성질.

    구간 [min_gap, T]를 dt로 잘게 쪼개 매 tick 생존확률을 곱하면, dt와 무관하게
    참 Weibull 생존확률 S(T)/S(min_gap)로 떨어져야 한다(telescoping 항등).
    이전 산식은 여기서 무너졌다: tick마다 jitter를 새로 뽑으니 반복 샘플링에
    사실상 항상 jitter 하한에서 발화 → 의도한 산포가 소멸.
    """
    now = datetime(2026, 7, 12, 14)
    start, end = 1800.0, 5400.0
    scale = 3600 / 1.0  # afternoon weight 1.0

    def survival_product(dt: float) -> float:
        p_survive = 1.0
        t = start
        while t < end - 1e-9:
            step = min(dt, end - t)
            prob = initiation_probability(
                now,
                now - timedelta(seconds=t),
                WEIGHTS,
                **{**PARAMS, "tick_interval_s": step},
            )
            p_survive *= 1.0 - prob
            t += step
        return p_survive

    expected = math.exp((start / scale) ** 2 - (end / scale) ** 2)
    assert survival_product(10.0) == pytest.approx(expected, rel=1e-9)
    assert survival_product(1.0) == pytest.approx(expected, rel=1e-9)
    assert survival_product(600.0) == pytest.approx(expected, rel=1e-9)


def test_per_tick_probability_stays_small():
    """tick 확률은 hazard×dt로 묶인다 — 경과가 아무리 길어도 한 tick에 몰빵되지 않는다.

    dt(10s)가 척도 λ(3600s)에 비해 작아 tick당 확률은 낮게 유지되고, 발화는 여러 tick에
    걸친 누적으로 일어난다. "오래됐으니 즉시 발화"가 아니라 "슬슬 말 걸 기운이 오른다"가
    이 산식의 의도라 이 성질은 버그가 아니라 요구사항이다.
    """
    now = datetime(2026, 7, 12, 14)
    p = initiation_probability(now, now - timedelta(seconds=100000), WEIGHTS, **PARAMS)
    assert 0.0 < p < 0.3


def test_should_initiate_samples_probability():
    """주사위 경계만 확인 — 확률 산식은 위에서 이미 결정론으로 고정했다."""
    now = datetime(2026, 7, 12, 14)
    last = now - timedelta(seconds=7200)
    p = initiation_probability(now, last, WEIGHTS, **PARAMS)
    assert 0.0 < p < 1.0  # 경계 양쪽이 의미 있으려면 확률이 열려 있어야 한다

    def _rng(value: float) -> random.Random:
        rng = random.Random()
        rng.random = lambda: value  # type: ignore[method-assign]
        return rng

    assert should_initiate(now, last, WEIGHTS, rng=_rng(p * 0.5), **PARAMS) is True
    assert should_initiate(now, last, WEIGHTS, rng=_rng(p + (1.0 - p) * 0.5), **PARAMS) is False
