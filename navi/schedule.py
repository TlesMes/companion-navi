"""스케줄 동기화 스텁 (arch 4.9) — 데몬은 스케줄을 소유하지 않고 밖에서 빌려온다.

빌려오는 기술 경로(캘린더 API? 컴패니언 앱?)는 보류된 결정 D11. 그때까지는
config.yaml의 고정값이 유일한 원천이다 — D11이 확정되면 이 어댑터만 교체하고
ModeMachine은 그대로 둔다(벤더 종속 금지 원칙과 같은 꼴).
"""

from __future__ import annotations

from datetime import date, time

from navi.heartbeat.mode import SleepWindow


class ConfigSchedule:
    """config 고정값 스케줄. 계약(arch 4.9) 중 취침창만 실값, 나머지는 D11까지 빈 값."""

    def __init__(self, sleep_start: time, sleep_end: time) -> None:
        self._window = SleepWindow(start=sleep_start, end=sleep_end)

    def get_sleep_window(self) -> SleepWindow:
        return self._window

    def get_wake_time(self, day: date) -> time | None:
        """외부 알람 연동은 D11 — 지금은 취침창 end가 사실상의 기상 시각."""
        return None

    def get_busy_blocks(self, day: date) -> list:
        """캘린더 일정 → 자동 DND 후보는 D11 — 지금은 수동 DND 명령뿐."""
        return []
