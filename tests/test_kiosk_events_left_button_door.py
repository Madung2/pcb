"""status 폴링에서 왼쪽 버튼(0→눌림)이 잡히면 도어 오픈으로 이어지는지 검증."""

from __future__ import annotations

import unittest
from unittest.mock import MagicMock

from kiosk_module.kiosk_events import KioskMonitorHandlers
from kiosk_module.protocol import ButtonPressEvent, StatusResponse
from kiosk_module.status_monitor import StatusMonitor


def _status(*, left: int, right: int, person: int = 0) -> StatusResponse:
    return StatusResponse(
        ac_light_status1=0,
        ac_light_status2=0,
        dc_light_status1=0,
        dc_light_status2=0,
        dc_light_brightness1=0,
        dc_light_brightness2=0,
        door_status=0,
        speaker_status=0,
        person_detected=person,
        button_left_status=left,
        button_right_status=right,
    )


class _TrackingController:
    def __init__(self) -> None:
        self.open_door_calls = 0
        self.close_door_calls = 0

    def open_door(self) -> None:
        self.open_door_calls += 1

    def close_door(self) -> None:
        self.close_door_calls += 1

    def set_speaker(self, _on: bool) -> None:
        return None


class _FakeInputTracker:
    def seconds_since_activity(self) -> float:
        return 0.0


class TestLeftButtonOpensDoor(unittest.TestCase):
    def test_handler_left_only_calls_open_door(self) -> None:
        controller = _TrackingController()
        handlers = KioskMonitorHandlers(
            controller,
            monitor=object(),
            input_tracker=_FakeInputTracker(),
        )
        handlers.on_button_pressed(
            ButtonPressEvent(
                left_pressed=True,
                right_pressed=False,
                left_just_pressed=True,
                right_just_pressed=False,
            )
        )
        self.assertEqual(controller.open_door_calls, 1)
        self.assertEqual(controller.close_door_calls, 0)

    def test_status_monitor_left_edge_triggers_open_door(self) -> None:
        """폴링 직전 상태가 좌=0·우=0 이고, 다음 status에서 좌만 눌리면 도어 오픈."""
        monitor = StatusMonitor(MagicMock(), button_combo_window_seconds=0)
        controller = _TrackingController()
        handlers = KioskMonitorHandlers(
            controller,
            monitor=monitor,
            input_tracker=_FakeInputTracker(),
        )
        monitor.on_button_pressed = handlers.on_button_pressed

        monitor._process_status(_status(left=0, right=0))
        monitor._process_status(_status(left=1, right=0))

        self.assertEqual(controller.open_door_calls, 1)
        self.assertEqual(controller.close_door_calls, 0)

    def test_left_and_right_simultaneous_does_not_open_door(self) -> None:
        """양쪽 동시 눌림은 오픈이 아니라 클로즈 경로."""
        controller = _TrackingController()
        handlers = KioskMonitorHandlers(
            controller,
            monitor=object(),
            input_tracker=_FakeInputTracker(),
        )
        handlers.on_button_pressed(
            ButtonPressEvent(
                left_pressed=True,
                right_pressed=True,
                left_just_pressed=True,
                right_just_pressed=True,
            )
        )
        self.assertEqual(controller.open_door_calls, 0)
        self.assertEqual(controller.close_door_calls, 1)


if __name__ == "__main__":
    unittest.main()
