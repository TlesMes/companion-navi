"""이벤트 버스 — 데몬 안의 발행자(귀·시계)와 구독자(대화·GUI·Heartbeat)를 잇는다 (arch 4.11).

프로세스 안 pub/sub: 구독자마다 유한 asyncio.Queue를 하나씩 갖고, publish는 전 구독자
큐에 논블로킹으로 넣는다. 큐가 가득 찬 구독자는 가장 오래된 이벤트를 버리고 최신을
넣는다(관찰자에겐 최신 상태가 중요) — **발행자는 어떤 경우에도 대기하지 않는다.**
느린 구독자(향후 GUI WS 등)가 마이크→STT→TTS 핫패스를 막지 못하게 하는 핵심 보장.

EventKind는 데몬 전역 이벤트 어휘다. 청취축의 ListenEvent(navi/ear/listening.py)는
이 중 WAKE/UTTERANCE/SLEEP의 원천이고, 데몬이 Event로 감싸 발행한다 — ListenSession은
버스를 모른다(계층 분리).
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from enum import Enum, auto
from typing import Any

log = logging.getLogger(__name__)


class EventKind(Enum):
    WAKE = auto()          # 호출어 감지 → ACTIVE (payload 없음)
    UTTERANCE = auto()     # 발화 1건 종료 (payload=Utterance)
    SLEEP = auto()         # ACTIVE → SLEEP 복귀 (payload=SleepReason)
    TICK = auto()          # 주기 시계 이벤트 — Heartbeat·모드 판정의 원료 (payload 없음)
    TURN_STARTED = auto()  # 턴 처리 시작 (payload=트리거 텍스트)
    TURN_ENDED = auto()    # 턴 처리 종료 (payload=트리거 텍스트)
    MODE_CHANGED = auto()  # 능동축 모드 전이 (payload=(이전, 이후) 모드 문자열) — Stage 14
    STAGE = auto()         # 턴 파이프라인 단계 계측 (payload=(stage, phase, detail)) — Stage 15
    #   stage ∈ {stt, gate, brain, tts}, phase ∈ {start, done}, detail=소요 ms·게이트 결과 등.
    #   GUI 노드 점등 재료이자 구간별 지연의 상시 기록(D8 재측정 재료).
    SHUTDOWN = auto()      # 데몬 종료 — 전 구독자에게 마지막 인사


@dataclass(frozen=True)
class Event:
    kind: EventKind
    ts: float  # time.monotonic 기준
    payload: Any = None


class EventBus:
    """구독자별 유한 큐 팬아웃. publish는 논블로킹 — 가득 찬 구독자는 오래된 것부터 drop."""

    def __init__(self) -> None:
        self._queues: dict[str, asyncio.Queue[Event]] = {}

    def subscribe(self, name: str, maxsize: int = 64) -> asyncio.Queue[Event]:
        if name in self._queues:
            raise ValueError(f"구독자 이름 중복: {name!r}")
        queue: asyncio.Queue[Event] = asyncio.Queue(maxsize=maxsize)
        self._queues[name] = queue
        return queue

    def unsubscribe(self, name: str) -> None:
        self._queues.pop(name, None)

    def publish(self, event: Event) -> None:
        for name, queue in self._queues.items():
            try:
                queue.put_nowait(event)
            except asyncio.QueueFull:
                # 가장 오래된 것을 버리고 최신을 넣는다 — 발행자는 절대 기다리지 않는다.
                try:
                    dropped = queue.get_nowait()
                    log.warning(
                        "구독자 %r 큐 포화 — %s drop (최신 %s 우선)",
                        name,
                        dropped.kind.name,
                        event.kind.name,
                    )
                except asyncio.QueueEmpty:
                    pass
                try:
                    queue.put_nowait(event)
                except asyncio.QueueFull:
                    log.warning("구독자 %r 큐 재포화 — %s 포기", name, event.kind.name)
