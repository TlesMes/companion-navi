"""2층: 능동 발화 타이밍 (arch 4.4). "지금 먼저 말 걸 때인가"의 확률적 판정.

1층(mode.py)이 "먼저 말해도 되는 시간대인가"(취침창·DND 게이트)를 결정론으로
가른다면, 여기는 그 게이트를 통과한 뒤 "그래서 지금 걸까"를 정한다.

**산식 = tick 기반 hazard(변동확률).** 마지막 상호작용 후 흐른 시간 t가 길수록 매 tick의
발화 확률이 오른다 — "오래 안 봤을수록 말 걸 확률이 높아진다". 고정 임계값이 아니라
생존함수 S(t)=exp(-(t/λ)^k)를 깔고, tick 구간 [t, t+dt]의 조건부 발화 확률
p = 1 - S(t+dt)/S(t)를 매번 굴린다.

**왜 이 꼴인가 — tick 빈도 중립성이 공짜로 따라온다.** 매 tick의 생존 확률을 곱하면
S(t+dt)/S(t) 가 telescoping되어 전체 생존 확률이 정확히 S(t)로 떨어진다. 즉 tick이
10초든 1초든 **발화 시점의 분포가 같다**(근사가 아니라 항등). 이전 산식은 이 성질이
없어서 tick마다 jitter를 새로 뽑는 순간 반복 샘플링에 무너졌다 — elapsed가 jitter
하한에 닿자마자 10초마다 주사위를 다시 굴리니 사실상 항상 하한에서 발화했고, 의도한
±20% 산포는 존재하지 않았다. 무작위성을 산식 밖(곱할 난수)이 아니라 **안(hazard)**에
두는 게 이 교체의 핵심이고, 그래서 jitter_range 파라미터는 사라졌다.

λ(scale) = base_interval_s / 시간대가중치 — 가중치가 클수록(=활발한 시간대) 척도가
짧아져 더 자주 건다(이전 산식의 방향 보존). k(shape) > 1이면 hazard가 시간에 따라
상승한다(k=2 → 선형 상승). k=1이면 무기억 지수분포 = "언제든 같은 확률".

⚠ 값(base_interval·time_weights·shape_k)은 여전히 **배선용 대충값**이다. 바뀐 건 산식의
꼴이지 튜닝이 아니다 — 좋은 타이밍은 종이로 못 정한다(진행 원칙 2). interaction_log가
쌓인 뒤 응답률·무시율을 보고 정하는 게 후속(B2).

daily_cap(하루 상한)은 DB 카운트가 필요해 이 순수 함수 밖(DaemonCore)에서 건다 —
여기는 시계만 있으면 테스트되는 순수 함수로 유지한다. 같은 이유로 확률 계산
(initiation_probability)과 주사위(should_initiate)를 갈라 놨다: 산식 전체가 RNG 없이
결정론으로 검증되고, 난수는 경계 한 곳에만 산다.
"""

from __future__ import annotations

import math
import random
from datetime import datetime


def time_of_day(now: datetime) -> str:
    """시각 → 시간대 버킷. 타이밍 가중치와 주제 힌트(topic.py)가 공유하는 라벨."""
    h = now.hour
    if 5 <= h < 12:
        return "morning"
    if 12 <= h < 17:
        return "afternoon"
    if 17 <= h < 22:
        return "evening"
    return "night"


def initiation_probability(
    now: datetime,
    last_interaction_at: datetime | None,
    time_weights: dict[str, float],
    *,
    base_interval_s: float,
    min_gap_s: float,
    tick_interval_s: float,
    shape_k: float,
) -> float:
    """이번 tick에 먼저 말 걸 확률 [0, 1]. 순수 함수 — 난수 없음(테스트 결정성).

    Weibull 생존함수 S(t)=exp(-(t/λ)^k)의 tick 구간 조건부 발화 확률:
        p = 1 - S(t+dt)/S(t) = 1 - exp((t/λ)^k - ((t+dt)/λ)^k)
    min_gap 안에서는 0 — 이 구간을 건너뛴 효과는 "min_gap 생존 조건부 분포"로,
    위 telescoping 성질을 그대로 보존한다(잘라낸 확률 질량이 뒤로 재분배됨).
    """
    if last_interaction_at is None:
        return 0.0  # 상호작용 이력이 없으면 먼저 말 걸 근거가 없다(콜드 오픈 금지)
    elapsed = (now - last_interaction_at).total_seconds()
    if elapsed < min_gap_s:
        return 0.0  # 하드 바닥 — 확률 이전에 무조건 침묵
    weight = time_weights.get(time_of_day(now), 1.0)
    scale = base_interval_s / max(weight, 0.01)
    if scale <= 0 or tick_interval_s <= 0:
        return 0.0
    exponent = (elapsed / scale) ** shape_k - ((elapsed + tick_interval_s) / scale) ** shape_k
    return 1.0 - math.exp(exponent)


def should_initiate(
    now: datetime,
    last_interaction_at: datetime | None,
    time_weights: dict[str, float],
    *,
    base_interval_s: float,
    min_gap_s: float,
    tick_interval_s: float,
    shape_k: float,
    rng: random.Random | None = None,
) -> bool:
    """arch 4.4 2층 계약. 확률을 뽑아 주사위 한 번 — 게이트·로깅은 이 bool만 본다.

    산식을 또 갈아끼워도(예: 응답률 피드백 반영) 이 bool 경계는 유지된다.
    """
    p = initiation_probability(
        now,
        last_interaction_at,
        time_weights,
        base_interval_s=base_interval_s,
        min_gap_s=min_gap_s,
        tick_interval_s=tick_interval_s,
        shape_k=shape_k,
    )
    if p <= 0.0:
        return False
    return (rng or random).random() < p
