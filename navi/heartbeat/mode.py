"""선톡축 모드 상태머신 — "지금 먼저 말 걸어도 되는가"의 결정론 판정 (arch 5장·4.4 1층, D16).

모드는 두 직교 축이다(D16). 청취축(navi/ear/listening.py)은 마이크→STT 문이고,
여기는 선톡축 — 나비가 *먼저* 말하는지를 가른다. 어떤 선톡 모드에서도 웨이크워드
호출·응답은 막지 않는다(사용자 오버라이드는 항상 자동 판단을 이긴다).

SLEEP/SNOOZE/DND는 게이트 효과가 동일하다(can_speak_now=False) — 셋을 가르는 건
수명(끝나는 방식)뿐이다: SLEEP=시계(기상)/강제기상, SNOOZE=타이머/강제기상,
DND=명시 해제만. 상태 구분은 GUI 표시("왜 조용한가, 언제 풀리는가")와 이후
상태별 뉘앙스의 자리다.

전이는 전부 명시적 규칙 — LLM 개입 0 (설계 원칙 "언제는 규칙, 무엇은 모델").
`command()`는 호출자를 모른다: 음성(검문①)이 1차 생산자지만 GUI 버튼(3단계)도
같은 API를 쓴다. 시계는 주입식(datetime) — fake clock으로 전 전이가 유닛 테스트다.

우선순위(충돌 해소): 수동 SLEEP > 취침창 SLEEP(강제기상이 이번 창에 한해 이김)
> SNOOZE > DND > ACTIVE. 취침창 *진입*은 낮의 DND/SNOOZE를 소거한다(자고 나면
리셋 — DND가 아침까지 새는 것 방지). 밤중에 내린 DND는 진입 이후라 살아남는다.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, time, timedelta
from enum import Enum, auto
from typing import Callable


class Mode(str, Enum):
    """선톡축 4모드. str 상속 — DB(mode_state)·GUI JSON에 값 그대로 실린다."""

    SLEEP = "sleep"
    ACTIVE = "active"
    DND = "dnd"
    SNOOZE = "snooze"


class ModeCommand(Enum):
    """명령 전이 어휘 — 검문①(음성)·GUI가 이걸로 상태머신을 조작한다."""

    SLEEP = auto()      # 수면 — 다음 기상 시각까지
    WAKE = auto()       # 강제기상 — SLEEP/SNOOZE 해제 (DND엔 안 통함)
    SNOOZE = auto()     # 유예 — now+snooze_minutes, 재발화 시 갱신(연장)
    DND = auto()        # 방해 금지 — 해제 명령까지 (자동 만료 없음)
    DND_CLEAR = auto()  # DND 해제


@dataclass(frozen=True)
class SleepWindow:
    """취침창 [start, end). start > end면 자정 넘김(예: 23:00~07:00)."""

    start: time
    end: time

    def contains(self, t: time) -> bool:
        if self.start <= self.end:
            return self.start <= t < self.end
        return t >= self.start or t < self.end  # 자정 넘김

    def next_end(self, now: datetime) -> datetime:
        """now 이후 가장 가까운 기상 시각. 창 안이면 이번 창의 끝이 나온다."""
        candidate = datetime.combine(now.date(), self.end)
        if candidate <= now:
            candidate += timedelta(days=1)
        return candidate


class ModeMachine:
    """선톡축 상태머신. 저장 상태는 (mode, override_until) 두 값 — arch 6 mode_state와 1:1.

    tick()이 시간 전이(창 진입·이탈, 만료)를, command()가 명령 전이를 굴린다.
    겉으로 보이는 모드는 저장 상태 + 시계의 순수 함수(_effective)라 tick 사이에도
    current_mode()는 항상 현재 시각 기준으로 정확하다.
    """

    def __init__(
        self,
        window: SleepWindow,
        snooze_minutes: int = 30,
        *,
        now: Callable[[], datetime] = datetime.now,
    ) -> None:
        self._window = window
        self._snooze = timedelta(minutes=snooze_minutes)
        self._now = now
        self._mode = Mode.ACTIVE
        self._override_until: datetime | None = None
        # 창 진입 에지 감지용 — 기동 시점이 창 안이면 "이미 진입한 것"으로 본다
        # (재기동이 밤중 DND를 소거하지 않게).
        self._was_in_window = window.contains(now().time())

    # ─── 조회 (arch 4.4 1층 계약) ───────────────────────────

    def current_mode(self, now: datetime | None = None) -> Mode:
        return self._effective(now or self._now())

    @property
    def can_speak_now(self) -> bool:
        """검문② — 능동 발화 허용 여부. ACTIVE만 True (arch 5장 규칙 1)."""
        return self.current_mode() is Mode.ACTIVE

    # ─── 전이 ───────────────────────────────────────────────

    def tick(self, now: datetime | None = None) -> Mode:
        """시간 전이 재계산 — 데몬 TICK마다 호출. 만료·창 에지를 저장 상태에 반영한다."""
        now = now or self._now()
        in_window = self._window.contains(now.time())
        if in_window and not self._was_in_window and self._mode in (Mode.DND, Mode.SNOOZE):
            self._clear()  # 창 진입 — 낮의 DND/SNOOZE 소거
        self._was_in_window = in_window
        if self._override_until is not None and now >= self._override_until:
            self._clear()  # 만료 — 기본 판정으로
        return self._effective(now)

    def command(self, cmd: ModeCommand, now: datetime | None = None) -> Mode:
        """명령 전이 — 최신 명령이 이전 오버라이드를 대체한다."""
        now = now or self._now()
        if cmd is ModeCommand.SLEEP:
            # 창 밖(22시)에 자면 다음 기상까지, 창 안이면 이번 기상까지
            self._mode, self._override_until = Mode.SLEEP, self._window.next_end(now)
        elif cmd is ModeCommand.WAKE:
            # 잠·유예에서만 의미 — ACTIVE/DND에선 무시(DND는 명시 해제만)
            if self._effective(now) in (Mode.SLEEP, Mode.SNOOZE):
                if self._window.contains(now.time()):
                    # 이번 창에 한해 창SLEEP을 이긴다 — 창이 끝나면 자연 소멸
                    self._mode, self._override_until = Mode.ACTIVE, self._window.next_end(now)
                else:
                    self._clear()
        elif cmd is ModeCommand.SNOOZE:
            self._mode, self._override_until = Mode.SNOOZE, now + self._snooze
        elif cmd is ModeCommand.DND:
            self._mode, self._override_until = Mode.DND, None
        elif cmd is ModeCommand.DND_CLEAR:
            if self._mode is Mode.DND:
                self._clear()
        return self.tick(now)

    # ─── 영속화 훅 (mode_state 테이블과 왕복) ─────────────────

    def export_state(self) -> tuple[str, str | None]:
        until = self._override_until.isoformat() if self._override_until else None
        return self._mode.value, until

    def restore_state(self, mode: str, override_until: str | None) -> None:
        """재기동 복원 — 만료된 오버라이드는 다음 tick이 자연 정리한다."""
        self._mode = Mode(mode)
        self._override_until = (
            datetime.fromisoformat(override_until) if override_until else None
        )

    # ─── 런타임 설정 (GUI 대비) ──────────────────────────────

    def set_window(self, window: SleepWindow) -> None:
        """취침창 런타임 변경 — config는 기본값, GUI(3단계)가 이걸 부른다."""
        self._window = window

    # ─── 내부 ───────────────────────────────────────────────

    def _clear(self) -> None:
        self._mode, self._override_until = Mode.ACTIVE, None

    def _effective(self, now: datetime) -> Mode:
        """저장 상태 + 시계 → 겉으로 보이는 모드 (우선순위 판정, 부수효과 없음)."""
        in_window = self._window.contains(now.time())
        override_live = self._override_until is not None and now < self._override_until
        if self._mode is Mode.ACTIVE and override_live:
            return Mode.ACTIVE  # 강제기상 — 이번 창에 한해 창SLEEP을 이긴다
        if self._mode is Mode.SLEEP and override_live:
            return Mode.SLEEP  # 수면 명령
        if in_window:
            return Mode.SLEEP  # 취침창
        if self._mode is Mode.SNOOZE and override_live:
            return Mode.SNOOZE
        if self._mode is Mode.DND:
            return Mode.DND
        return Mode.ACTIVE
