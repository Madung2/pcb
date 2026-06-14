"""person_detected 상승 엣지에서만 환영 동작이 실행되는지 검증."""

from __future__ import annotations

import unittest

from kiosk_module.kiosk_events import KioskMonitorHandlers
from kiosk_module.protocol import StatusResponse


class _FakeController:
    def __init__(self) -> None:
        self.speaker_on_calls = 0

    def close_door(self) -> None:
        return None

    def set_speaker(self, on: bool) -> None:
        if on:
            self.speaker_on_calls += 1

    def open_door(self) -> None:
        return None


class _FakeInputTracker:
    def seconds_since_activity(self) -> float:
        return 999.0


def _status(person: int) -> StatusResponse:
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
        button_left_status=0,
        button_right_status=0,
    )


class TestPersonDetectedRisingEdge(unittest.TestCase):
    def test_welcome_only_when_false_to_true(self) -> None:
        controller = _FakeController()
        handlers = KioskMonitorHandlers(
            controller,
            monitor=object(),
            input_tracker=_FakeInputTracker(),
        )
        with patch.object(
            handlers,
            "_welcome_on_person_detected_rising_edge",
            wraps=handlers._welcome_on_person_detected_rising_edge,
        ) as mock_welcome:
            handlers.on_status_received(_status(0))
            handlers.on_status_received(_status(1))
            handlers.on_status_received(_status(1))

        self.assertEqual(mock_welcome.call_count, 1)
        self.assertEqual(controller.speaker_on_calls, 1)

    def test_repeat_after_leave(self) -> None:
        controller = _FakeController()
        handlers = KioskMonitorHandlers(
            controller,
            monitor=object(),
            input_tracker=_FakeInputTracker(),
        )
        handlers.on_status_received(_status(0))
        handlers.on_status_received(_status(1))
        handlers.on_status_received(_status(0))
        handlers.on_status_received(_status(1))
        self.assertEqual(controller.speaker_on_calls, 2)


if __name__ == "__main__":
    unittest.main()
