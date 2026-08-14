import logging

from core.events import (
    FATAL_CRASH,
    FATAL_PORT_IN_USE,
    ClientCount,
    EventBus,
    Fatal,
    LogLine,
    Ready,
    TelemetryState,
)


def test_emit_then_drain_preserves_order():
    bus = EventBus()
    bus.emit(ClientCount(1))
    bus.emit(ClientCount(2))
    events, dropped = bus.drain()
    assert [e.n for e in events] == [1, 2]
    assert dropped == 0


def test_drain_is_bounded_and_leaves_the_rest():
    bus = EventBus()
    for i in range(10):
        bus.emit(ClientCount(i))
    first, _ = bus.drain(limit=4)
    assert [e.n for e in first] == [0, 1, 2, 3]
    rest, _ = bus.drain(limit=100)
    assert [e.n for e in rest] == [4, 5, 6, 7, 8, 9]


def test_drain_of_empty_bus_returns_nothing():
    bus = EventBus()
    assert bus.drain() == ([], 0)


def test_emit_never_blocks_when_full_and_reports_the_drop():
    bus = EventBus(maxsize=2)
    assert bus.emit(ClientCount(1)) is True
    assert bus.emit(ClientCount(2)) is True
    assert bus.emit(ClientCount(3)) is False  # dropped, did not block
    events, dropped = bus.drain()
    assert [e.n for e in events] == [1, 2]
    assert dropped == 1


def test_drop_count_resets_after_being_reported():
    bus = EventBus(maxsize=1)
    bus.emit(ClientCount(1))
    bus.emit(ClientCount(2))
    bus.emit(ClientCount(3))
    _, dropped = bus.drain()
    assert dropped == 2
    _, dropped_again = bus.drain()
    assert dropped_again == 0


def test_events_carry_their_documented_fields():
    ready = Ready(
        ips=("192.168.1.42",), port=8765, token="abc",
        cert_fp="A1:B2", pairing_uri="starcompanion://pair?x=1",
    )
    assert ready.ips == ("192.168.1.42",)
    assert ready.port == 8765
    assert TelemetryState(found=False, path=None, feeds=0).found is False
    assert LogLine(ts="21:04:11", level=logging.WARNING, text="hi").level == logging.WARNING
    assert Fatal(kind=FATAL_PORT_IN_USE, message="in use").kind == "PORT_IN_USE"
    assert FATAL_CRASH == "CRASH"


def test_events_are_immutable():
    import dataclasses
    import pytest

    event = ClientCount(1)
    with pytest.raises(dataclasses.FrozenInstanceError):
        event.n = 2
