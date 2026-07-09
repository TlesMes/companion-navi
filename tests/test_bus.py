"""EventBus 검증 — 팬아웃·drop 정책(발행자 비대기)·구독 해지."""

import asyncio

import pytest

from navi.bus import Event, EventBus, EventKind


def _ev(kind=EventKind.TICK, payload=None) -> Event:
    return Event(kind=kind, ts=0.0, payload=payload)


@pytest.mark.asyncio
async def test_fanout_to_multiple_subscribers():
    bus = EventBus()
    a = bus.subscribe("a")
    b = bus.subscribe("b")
    event = _ev(EventKind.WAKE)
    bus.publish(event)
    assert a.get_nowait() is event
    assert b.get_nowait() is event


@pytest.mark.asyncio
async def test_full_queue_drops_oldest_keeps_latest():
    # maxsize=2에 3개 발행 → 가장 오래된 1번이 버려지고 2·3번이 남는다(최신 우선)
    bus = EventBus()
    q = bus.subscribe("slow", maxsize=2)
    events = [_ev(payload=i) for i in range(3)]
    for e in events:
        bus.publish(e)  # put_nowait만 쓰므로 어떤 경우에도 블록하지 않는다
    assert q.get_nowait().payload == 1
    assert q.get_nowait().payload == 2
    with pytest.raises(asyncio.QueueEmpty):
        q.get_nowait()


@pytest.mark.asyncio
async def test_slow_subscriber_does_not_affect_others():
    bus = EventBus()
    slow = bus.subscribe("slow", maxsize=1)
    fast = bus.subscribe("fast", maxsize=16)
    for i in range(5):
        bus.publish(_ev(payload=i))
    assert slow.qsize() == 1 and slow.get_nowait().payload == 4  # 최신만 생존
    assert [fast.get_nowait().payload for _ in range(5)] == [0, 1, 2, 3, 4]


@pytest.mark.asyncio
async def test_unsubscribe_stops_delivery():
    bus = EventBus()
    q = bus.subscribe("a")
    bus.unsubscribe("a")
    bus.publish(_ev())  # 예외 없이 무시
    assert q.qsize() == 0
    bus.unsubscribe("없는이름")  # 멱등


@pytest.mark.asyncio
async def test_duplicate_subscriber_name_rejected():
    bus = EventBus()
    bus.subscribe("core")
    with pytest.raises(ValueError):
        bus.subscribe("core")
