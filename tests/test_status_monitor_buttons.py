from __future__ import annotations

from unittest.mock import MagicMock

from kiosk_module.protocol import ButtonPressEvent, StatusResponse
from kiosk_module.status_monitor import StatusMonitor


def _status(*, left: int, right: int) -> StatusResponse:
    return StatusResponse(
        ac_light_status1=0,
        ac_light_status2=0,
        dc_light_status1=0,
        dc_light_status2=0,
        dc_light_brightness1=0,
        dc_light_brightness2=0,
        door_status=0,
        speaker_status=0,
        person_detected=0,
        button_left_status=left,
        button_right_status=right,
    )


def test_held_left_button_emits_once_until_release() -> None:
    monitor = StatusMonitor(MagicMock(), button_combo_window_seconds=0)
    events: list[ButtonPressEvent] = []
    monitor.on_button_pressed = events.append

    monitor._process_status(_status(left=1, right=0))
    monitor._process_status(_status(left=1, right=0))
    monitor._process_status(_status(left=1, right=0))

    assert len(events) == 1
    assert events[0].left_pressed is True
    assert events[0].right_pressed is False

    monitor._process_status(_status(left=0, right=0))
    monitor._process_status(_status(left=1, right=0))

    assert len(events) == 2


def test_near_simultaneous_left_then_right_emits_both_combo(monkeypatch) -> None:
    monitor = StatusMonitor(MagicMock(), button_combo_window_seconds=0.15)
    events: list[ButtonPressEvent] = []
    monitor.on_button_pressed = events.append

    times = iter([10.0, 10.05, 10.20])
    monkeypatch.setattr(
        "kiosk_module.status_monitor.time.monotonic",
        lambda: next(times),
    )

    monitor._process_status(_status(left=1, right=0))
    monitor._process_status(_status(left=1, right=1))
    monitor._process_status(_status(left=1, right=1))

    assert len(events) == 1
    assert events[0].left_pressed is True
    assert events[0].right_pressed is True

    monitor._process_status(_status(left=1, right=1))
    assert len(events) == 1
