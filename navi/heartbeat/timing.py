"""2층: 능동 발화 타이밍 (arch 4.4). "지금 먼저 말 걸 때인가"의 확률적 판정.

1층(mode.py)이 "먼저 말해도 되는 시간대인가"(취침창·DND 게이트)를 결정론으로
가른다면, 여기는 그 게이트를 통과한 뒤 "그래서 지금 걸까"를 정한다. 마지막
상호작용 이후 흐른 시간을 시간대 가중치와 jitter로 흔들어 유효 간격을 만들고,
그만큼 지났으면 True.

⚠ 이 파일의 값(base_interval·time_weights·jitter)은 **대충 정한 배선용 기본값**이다.
좋은 타이밍은 종이로 못 정한다(진행 원칙 2) — interaction_log가 쌓인 뒤 응답률·
무시율을 보고 튜닝하는 게 후속 작업이고, 지금 목표는 "함수가 존재하고 값을
반환하며 로그가 남는다"까지다. 여기서 눈치를 궁리하지 않는다.

daily_cap(하루 상한)은 DB 카운트가 필요해 이 순수 함수 밖(DaemonCore)에서 건다 —
여기는 시계만 있으면 테스트되는 순수 함수로 유지한다.
"""

from __future__ import annotations

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


def draw_jitter(
    jitter_range: tuple[float, float], rng: random.Random | None = None
) -> float:
    """유효 간격에 곱할 난수 배수 — 규칙적 발화의 기계적 느낌을 흐트러뜨린다."""
    lo, hi = jitter_range
    return (rng or random).uniform(lo, hi)


def should_initiate(
    now: datetime,
    last_interaction_at: datetime | None,
    time_weights: dict[str, float],
    jitter: float,
    *,
    base_interval_s: float,
    min_gap_s: float,
) -> bool:
    """arch 4.4 2층 계약. jitter는 호출자가 draw_jitter로 뽑아 넣는다(테스트 결정성).

    규칙(배선용 최소): 마지막 상호작용 후 elapsed 초. min_gap 안이면 무조건 침묵.
    유효 간격 = base_interval / 시간대가중치 * jitter — elapsed가 이를 넘으면 발화.
    가중치가 클수록(=활발한 시간대) 유효 간격이 짧아져 더 자주 건다.
    """
    if last_interaction_at is None:
        return False  # 상호작용 이력이 없으면 먼저 말 걸 근거가 없다(콜드 오픈 금지)
    elapsed = (now - last_interaction_at).total_seconds()
    if elapsed < min_gap_s:
        return False
    weight = time_weights.get(time_of_day(now), 1.0)
    effective_interval = base_interval_s / max(weight, 0.01) * jitter
    return elapsed >= effective_interval
