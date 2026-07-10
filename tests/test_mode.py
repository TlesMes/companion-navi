"""능동축 모드 상태머신 검증 — fake clock으로 전 전이를 결정론 재현 (arch 5장, Stage 14).

시계는 주입식이라 실제 시간 대기 없이 창 진입·만료·우선순위를 전부 고정한다.
취침창 23:00~07:00(자정 넘김)을 기준 픽스처로 쓴다.
"""

from datetime import datetime, time

from navi.heartbeat import Mode, ModeCommand, ModeMachine, SleepWindow

WINDOW = SleepWindow(start=time(23, 0), end=time(7, 0))


class Clock:
    """조작 가능한 시계 — machine 내부 now()와 명시 인자 양쪽에 쓴다."""

    def __init__(self, at: datetime):
        self.at = at

    def __call__(self) -> datetime:
        return self.at


def _dt(day: int, hour: int, minute: int = 0) -> datetime:
    return datetime(2026, 7, day, hour, minute)


def _machine(at: datetime, snooze_minutes: int = 30) -> tuple[ModeMachine, Clock]:
    clock = Clock(at)
    return ModeMachine(WINDOW, snooze_minutes, now=clock), clock


# --- SleepWindow: 자정 넘김 판정과 기상 시각 계산 ---

def test_window_contains_midnight_wrap():
    assert WINDOW.contains(time(23, 0))    # 진입 경계 포함
    assert WINDOW.contains(time(23, 30))
    assert WINDOW.contains(time(3, 0))
    assert not WINDOW.contains(time(7, 0))  # 기상 경계 제외
    assert not WINDOW.contains(time(12, 0))
    assert not WINDOW.contains(time(22, 59))


def test_window_contains_same_day():
    nap = SleepWindow(start=time(13, 0), end=time(15, 0))
    assert nap.contains(time(14, 0))
    assert not nap.contains(time(12, 0))
    assert not nap.contains(time(15, 0))


def test_window_next_end():
    assert WINDOW.next_end(_dt(9, 22, 0)) == _dt(10, 7, 0)   # 창 밖 저녁 → 익일 기상
    assert WINDOW.next_end(_dt(9, 23, 30)) == _dt(10, 7, 0)  # 창 안 밤 → 익일 기상
    assert WINDOW.next_end(_dt(10, 6, 0)) == _dt(10, 7, 0)   # 창 안 새벽 → 당일 기상


# --- 기본: 낮은 ACTIVE, 창은 SLEEP (검문②) ---

def test_daytime_is_active_and_can_speak():
    machine, _ = _machine(_dt(9, 12, 0))
    assert machine.current_mode() is Mode.ACTIVE
    assert machine.can_speak_now


def test_window_entry_and_exit_via_tick():
    machine, clock = _machine(_dt(9, 22, 59))
    assert machine.tick(_dt(9, 22, 59)) is Mode.ACTIVE
    assert machine.tick(_dt(9, 23, 0)) is Mode.SLEEP    # 취침창 진입
    clock.at = _dt(9, 23, 30)
    assert not machine.can_speak_now                     # 자는 시간 — 절대 선제 발화 금지
    assert machine.tick(_dt(10, 7, 0)) is Mode.ACTIVE   # 기상


# --- 수면 명령: 창 밖이어도 다음 기상까지 잔다 ---

def test_manual_sleep_holds_until_next_wake():
    machine, _ = _machine(_dt(9, 22, 0))
    assert machine.command(ModeCommand.SLEEP, _dt(9, 22, 0)) is Mode.SLEEP
    assert machine.tick(_dt(9, 22, 30)) is Mode.SLEEP   # 창 밖인데도 SLEEP(오버라이드)
    assert machine.tick(_dt(10, 3, 0)) is Mode.SLEEP    # 창 구간과 자연 합류
    assert machine.tick(_dt(10, 7, 5)) is Mode.ACTIVE   # 기상 시각에 만료


def test_manual_sleep_beats_dnd():
    # 우선순위 최상단 — DND 중에도 수면 명령은 재운다
    machine, _ = _machine(_dt(9, 21, 0))
    machine.command(ModeCommand.DND, _dt(9, 21, 0))
    assert machine.command(ModeCommand.SLEEP, _dt(9, 22, 0)) is Mode.SLEEP


# --- 강제기상(WAKE): 사용자 오버라이드가 자동 판단(창)을 이긴다 ---

def test_wake_during_window_wins_until_window_end():
    machine, _ = _machine(_dt(9, 23, 30))
    assert machine.tick(_dt(9, 23, 30)) is Mode.SLEEP
    assert machine.command(ModeCommand.WAKE, _dt(9, 23, 40)) is Mode.ACTIVE
    assert machine.tick(_dt(10, 3, 0)) is Mode.ACTIVE    # 창 안인데도 ACTIVE 유지
    assert machine.tick(_dt(10, 8, 0)) is Mode.ACTIVE    # 창 끝 이후 기본 ACTIVE
    assert machine.tick(_dt(10, 23, 0)) is Mode.SLEEP    # 다음 창은 정상 진입


def test_wake_clears_snooze_outside_window():
    machine, _ = _machine(_dt(10, 7, 5))
    machine.command(ModeCommand.SNOOZE, _dt(10, 7, 5))
    assert machine.command(ModeCommand.WAKE, _dt(10, 7, 10)) is Mode.ACTIVE
    assert machine.tick(_dt(10, 7, 20)) is Mode.ACTIVE   # 스누즈 잔여분 무효


def test_wake_does_not_clear_dnd():
    # DND는 명시 해제만 — 강제기상 구절로는 안 풀린다
    machine, _ = _machine(_dt(9, 12, 0))
    machine.command(ModeCommand.DND, _dt(9, 12, 0))
    assert machine.command(ModeCommand.WAKE, _dt(9, 12, 5)) is Mode.DND


# --- 스누즈: 30분 유예, 재발화 시 연장 ---

def test_snooze_expires_after_grace():
    machine, _ = _machine(_dt(10, 7, 5))
    assert machine.command(ModeCommand.SNOOZE, _dt(10, 7, 5)) is Mode.SNOOZE
    assert machine.tick(_dt(10, 7, 30)) is Mode.SNOOZE
    assert machine.tick(_dt(10, 7, 36)) is Mode.ACTIVE   # 만료 → 기본 판정


def test_snooze_repeat_extends():
    machine, _ = _machine(_dt(10, 7, 5))
    machine.command(ModeCommand.SNOOZE, _dt(10, 7, 5))    # ~07:35
    machine.command(ModeCommand.SNOOZE, _dt(10, 7, 30))   # ~08:00로 갱신
    assert machine.tick(_dt(10, 7, 45)) is Mode.SNOOZE
    assert machine.tick(_dt(10, 8, 1)) is Mode.ACTIVE


def test_snooze_near_wake_extends_past_window():
    # 기상 직전 "더 잘래" — 창SLEEP이 우선 표시되다가 창 끝나면 SNOOZE로 이어진다
    machine, _ = _machine(_dt(10, 6, 50))
    assert machine.command(ModeCommand.SNOOZE, _dt(10, 6, 50)) is Mode.SLEEP  # 창 우선
    assert machine.tick(_dt(10, 7, 5)) is Mode.SNOOZE    # 창 끝, 유예는 계속(~07:20)
    assert machine.tick(_dt(10, 7, 25)) is Mode.ACTIVE


# --- DND: 자동 만료 없음, 창 진입이 낮의 DND를 소거 ---

def test_dnd_persists_until_cleared():
    machine, _ = _machine(_dt(9, 12, 0))
    assert machine.command(ModeCommand.DND, _dt(9, 12, 0)) is Mode.DND
    assert machine.tick(_dt(9, 20, 0)) is Mode.DND        # 몇 시간 지나도 유지
    assert machine.command(ModeCommand.DND_CLEAR, _dt(9, 20, 5)) is Mode.ACTIVE


def test_dnd_clear_is_noop_outside_dnd():
    machine, _ = _machine(_dt(10, 7, 5))
    machine.command(ModeCommand.SNOOZE, _dt(10, 7, 5))
    assert machine.command(ModeCommand.DND_CLEAR, _dt(10, 7, 10)) is Mode.SNOOZE


def test_window_entry_clears_daytime_dnd():
    # 자고 나면 리셋 — 낮의 DND가 아침까지 새지 않는다
    machine, _ = _machine(_dt(9, 12, 0))
    machine.command(ModeCommand.DND, _dt(9, 12, 0))
    assert machine.tick(_dt(9, 23, 0)) is Mode.SLEEP      # 창 진입이 DND 소거
    assert machine.tick(_dt(10, 7, 10)) is Mode.ACTIVE    # 아침은 DND 아님


def test_night_issued_dnd_survives_morning():
    # 창 "진입 이후" 내린 DND는 에지가 지났으므로 살아남는다 — 명시 해제만
    machine, _ = _machine(_dt(9, 22, 0))
    machine.tick(_dt(9, 23, 0))                           # 진입 에지 소비
    machine.command(ModeCommand.DND, _dt(9, 23, 30))
    assert machine.tick(_dt(10, 3, 0)) is Mode.SLEEP      # 밤 동안 표시는 창SLEEP
    assert machine.tick(_dt(10, 7, 10)) is Mode.DND       # 아침에 DND로 복귀
    assert machine.command(ModeCommand.DND_CLEAR, _dt(10, 7, 20)) is Mode.ACTIVE


# --- 영속화: (mode, override_until) 왕복 ---

def test_export_restore_roundtrip():
    machine, _ = _machine(_dt(10, 7, 5))
    machine.command(ModeCommand.SNOOZE, _dt(10, 7, 5))
    mode, until = machine.export_state()

    restored, _ = _machine(_dt(10, 7, 10))
    restored.restore_state(mode, until)
    assert restored.current_mode(_dt(10, 7, 10)) is Mode.SNOOZE  # 재기동 후 유예 생존
    assert restored.tick(_dt(10, 7, 40)) is Mode.ACTIVE


def test_restore_expired_override_is_cleaned_by_tick():
    machine, _ = _machine(_dt(10, 7, 5))
    machine.command(ModeCommand.SNOOZE, _dt(10, 7, 5))
    mode, until = machine.export_state()

    restored, _ = _machine(_dt(10, 9, 0))                 # 유예가 한참 지난 뒤 재기동
    restored.restore_state(mode, until)
    assert restored.tick(_dt(10, 9, 0)) is Mode.ACTIVE


def test_restart_inside_window_keeps_night_dnd():
    # 창 안 재기동은 "이미 진입한 것"으로 본다 — 밤중 DND를 재기동이 소거하면 안 된다
    machine, _ = _machine(_dt(10, 3, 0))
    machine.restore_state(Mode.DND.value, None)
    assert machine.tick(_dt(10, 3, 5)) is Mode.SLEEP      # 밤 동안은 창SLEEP
    assert machine.tick(_dt(10, 7, 10)) is Mode.DND       # 아침에 DND 유지


# --- 런타임 창 변경 (GUI 대비) ---

def test_set_window_takes_effect_immediately():
    machine, _ = _machine(_dt(9, 21, 30))
    assert machine.tick(_dt(9, 21, 30)) is Mode.ACTIVE
    machine.set_window(SleepWindow(start=time(21, 0), end=time(6, 0)))
    assert machine.tick(_dt(9, 21, 31)) is Mode.SLEEP     # 새 창 기준으로 즉시 판정
