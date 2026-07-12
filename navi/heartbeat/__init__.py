"""능동성 엔진(Heartbeat, arch 4.4) — 3층 구조.

1층 mode.py    : 모드 게이트(결정론). "먼저 말해도 되는 시간대인가" — 취침창·DND.
2층 timing.py  : 타이밍(가중치+jitter). "그래서 지금 걸까".
3층 topic.py   : 주제 도출. "무엇을 먼저 말할까"(트리거 힌트).

배선은 DaemonCore 루프 tick(arch 4.11)이 1→2→3 순으로 굴린다.
"""

from navi.heartbeat.mode import Mode, ModeCommand, ModeMachine, SleepWindow
from navi.heartbeat.timing import draw_jitter, should_initiate, time_of_day
from navi.heartbeat.topic import pick_topic

__all__ = [
    "Mode",
    "ModeCommand",
    "ModeMachine",
    "SleepWindow",
    "should_initiate",
    "draw_jitter",
    "time_of_day",
    "pick_topic",
]
