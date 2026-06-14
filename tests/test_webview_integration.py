import unittest
from unittest.mock import MagicMock, patch

import kiosk_module.kiosk_events as kiosk_events_mod
from kiosk_module.kiosk_events import KioskMonitorHandlers
from kiosk_module.protocol import ButtonPressEvent, StatusResponse
from kiosk_module.status_monitor import StatusMonitor
from kiosk_module.webview_services import append_device_id_query


class _FakeController:
    def close_door(self) -> None:
        return None

    def set_speaker(self, _on: bool) -> None:
        return None

    def open_door(self) -> None:
        return None


class _FakeInputTracker:
    def seconds_since_activity(self) -> float:
        return 0.0


class _FakeWebViewController:
    def __init__(self) -> None:
        self.show_meet_web_calls = 0
        self.restore_default_screen_calls = 0

    def show_meet_web(self, url: str | None = None) -> None:
        self.show_meet_web_calls += 1

    def restore_default_screen(self) -> None:
        self.restore_default_screen_calls += 1


class TestWebViewIntegration(unittest.TestCase):
    def _make_status(
        self,
        *,
        person_detected: int,
        left_button: int = 0,
        right_button: int = 0,
    ) -> StatusResponse:
        return StatusResponse(
            ac_light_status1=0,
            ac_light_status2=0,
            dc_light_status1=0,
            dc_light_status2=0,
            dc_light_brightness1=0,
            dc_light_brightness2=0,
            door_status=0,
            speaker_status=0,
            person_detected=person_detected,
            button_left_status=left_button,
            button_right_status=right_button,
        )

    @patch.object(kiosk_events_mod, "launch_background_browser")
    def test_right_button_shows_meet_in_integrated_webview(
        self,
        mock_launch_background: MagicMock,
    ) -> None:
        webview_controller = _FakeWebViewController()
        handlers = KioskMonitorHandlers(
            _FakeController(),
            monitor=object(),
            input_tracker=_FakeInputTracker(),
            webview_controller=webview_controller,
        )
        with patch.object(
            kiosk_events_mod.config,
            "meet_web_url",
            "https://meet.example/kiosk",
        ):
            handlers.on_button_pressed(
                ButtonPressEvent(
                    left_pressed=False,
                    right_pressed=True,
                    left_just_pressed=False,
                    right_just_pressed=True,
                )
            )
        mock_launch_background.assert_not_called()
        self.assertEqual(webview_controller.show_meet_web_calls, 1)

    @patch.object(kiosk_events_mod, "launch_background_browser")
    def test_first_status_person_and_right_button_still_opens_meet(
        self,
        mock_launch_background: MagicMock,
    ) -> None:
        monitor = StatusMonitor(MagicMock(), button_combo_window_seconds=0)
        webview_controller = _FakeWebViewController()
        handlers = KioskMonitorHandlers(
            _FakeController(),
            monitor=monitor,
            input_tracker=_FakeInputTracker(),
            webview_controller=webview_controller,
        )
        monitor.on_status_received = handlers.on_status_received
        monitor.on_button_pressed = handlers.on_button_pressed

        with patch.object(
            kiosk_events_mod.config,
            "meet_web_url",
            "https://meet.example/kiosk",
        ):
            monitor._process_status(
                self._make_status(person_detected=1, right_button=1)
            )

        mock_launch_background.assert_not_called()
        self.assertEqual(webview_controller.show_meet_web_calls, 1)

    @patch.object(kiosk_events_mod, "launch_background_browser")
    def test_repeated_right_button_press_after_release_opens_meet_each_time(
        self,
        mock_launch_background: MagicMock,
    ) -> None:
        monitor = StatusMonitor(MagicMock(), button_combo_window_seconds=0)
        webview_controller = _FakeWebViewController()
        handlers = KioskMonitorHandlers(
            _FakeController(),
            monitor=monitor,
            input_tracker=_FakeInputTracker(),
            webview_controller=webview_controller,
        )
        monitor.on_status_received = handlers.on_status_received
        monitor.on_button_pressed = handlers.on_button_pressed

        with patch.object(
            kiosk_events_mod.config,
            "meet_web_url",
            "https://meet.example/kiosk",
        ):
            monitor._process_status(
                self._make_status(person_detected=0, right_button=1)
            )
            monitor._process_status(
                self._make_status(person_detected=0, right_button=0)
            )
            monitor._process_status(
                self._make_status(person_detected=0, right_button=1)
            )

        mock_launch_background.assert_not_called()
        self.assertEqual(webview_controller.show_meet_web_calls, 2)

    @patch.object(kiosk_events_mod, "launch_background_browser")
    def test_meet_falls_back_to_background_browser_without_webview(
        self,
        mock_launch_background: MagicMock,
    ) -> None:
        handlers = KioskMonitorHandlers(
            _FakeController(),
            monitor=object(),
            input_tracker=_FakeInputTracker(),
        )
        with patch.object(
            kiosk_events_mod.config,
            "meet_web_url",
            "https://meet.example/kiosk",
        ):
            handlers.on_button_pressed(
                ButtonPressEvent(
                    left_pressed=False,
                    right_pressed=True,
                    left_just_pressed=False,
                    right_just_pressed=True,
                )
            )
        mock_launch_background.assert_called_once()

    def test_absence_does_not_restore_webview_or_kill_meet(self) -> None:
        """사람 없음 폴링만으로는 내장 웹뷰 복귀·Meet 백그라운드 종료를 하지 않음."""
        controller = _FakeController()
        webview_controller = _FakeWebViewController()
        handlers = KioskMonitorHandlers(
            controller,
            monitor=object(),
            input_tracker=_FakeInputTracker(),
            webview_controller=webview_controller,
        )

        status = self._make_status(person_detected=0)
        handlers.on_status_received(status)

        self.assertEqual(webview_controller.restore_default_screen_calls, 0)


class TestAppendDeviceId(unittest.TestCase):
    def test_skips_when_device_id_present(self) -> None:
        uid = "dde95ec2-9236-4dfd-aa4e-2fdd8206b22e"
        base = f"https://kiosk.jdone.co.kr/?device_id={uid}"
        self.assertEqual(append_device_id_query(base, uid), base)

    def test_appends_when_no_device_id(self) -> None:
        self.assertEqual(
            append_device_id_query("https://k.example.com/view", "device-1"),
            "https://k.example.com/view?device_id=device-1",
        )


if __name__ == "__main__":
    unittest.main()
