"""오른쪽 버튼(0→눌림) 시 show_meet_web 이 호출되는지 검증.
# ysoh 2026-06-14
"""

from __future__ import annotations

import unittest
from unittest.mock import MagicMock, patch

from kiosk_module.kiosk_events import KioskMonitorHandlers
from kiosk_module.protocol import ButtonPressEvent, StatusResponse
from kiosk_module.status_monitor import StatusMonitor


def _status(*, left: int = 0, right: int = 0, person: int = 0) -> StatusResponse:
    """테스트용 PCB 상태 응답 생성 헬퍼."""  # ysoh 2026-06-14
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


class _DummyController:  # ysoh 2026-06-14
    """제어 호출만 기록하는 더미 컨트롤러."""

    def __init__(self) -> None:
        self.open_door_calls = 0
        self.close_door_calls = 0

    def open_door(self) -> None:
        self.open_door_calls += 1

    def close_door(self) -> None:
        self.close_door_calls += 1

    def set_speaker(self, _on: bool) -> None:
        return None


class _FakeInputTracker:  # ysoh 2026-06-14
    def seconds_since_activity(self) -> float:
        return 0.0


class _MockWebViewController:  # ysoh 2026-06-14
    """show_meet_web / restore_default_screen 호출을 기록하는 Mock."""

    def __init__(self) -> None:
        self.show_meet_web_calls = 0
        self.restore_default_screen_calls = 0

    def show_meet_web(self, url: str | None = None) -> None:
        self.show_meet_web_calls += 1

    def restore_default_screen(self) -> None:
        self.restore_default_screen_calls += 1


class TestMeetOnRightButton(unittest.TestCase):
    """오른쪽 버튼 → show_meet_web 호출 검증."""  # ysoh 2026-06-14

    @patch("kiosk_module.kiosk_events.config")
    def test_right_button_calls_show_meet_web(self, mock_config) -> None:
        """오른쪽 버튼만 눌리면 webview_controller.show_meet_web() 이 호출된다."""
        # ysoh 2026-06-14
        mock_config.meet_web_url = "https://meet.example.com/test"
        mock_config.vacant_idle_close_seconds = 9999
        mock_config.device_id = "test-device"

        controller = _DummyController()
        webview_ctrl = _MockWebViewController()
        handlers = KioskMonitorHandlers(
            controller,
            monitor=object(),
            input_tracker=_FakeInputTracker(),
            webview_controller=webview_ctrl,
        )

        # 오른쪽 버튼만 눌림 이벤트
        handlers.on_button_pressed(
            ButtonPressEvent(
                left_pressed=False,
                right_pressed=True,
                left_just_pressed=False,
                right_just_pressed=True,
            )
        )

        self.assertEqual(webview_ctrl.show_meet_web_calls, 1)
        self.assertEqual(controller.open_door_calls, 0)
        self.assertEqual(controller.close_door_calls, 0)

    @patch("kiosk_module.kiosk_events.config")
    def test_right_button_no_meet_url_does_not_crash(self, mock_config) -> None:
        """MEET_WEB_URL 이 비어있으면 show_meet_web 이 호출되지 않고 크래시도 안 한다."""
        # ysoh 2026-06-14
        mock_config.meet_web_url = ""
        mock_config.vacant_idle_close_seconds = 9999
        mock_config.kiosk_browser_cmd = ""
        mock_config.background_browser_timeout_seconds = 300

        controller = _DummyController()
        webview_ctrl = _MockWebViewController()
        handlers = KioskMonitorHandlers(
            controller,
            monitor=object(),
            input_tracker=_FakeInputTracker(),
            webview_controller=webview_ctrl,
        )

        handlers.on_button_pressed(
            ButtonPressEvent(
                left_pressed=False,
                right_pressed=True,
                left_just_pressed=False,
                right_just_pressed=True,
            )
        )

        # meet_url 비어있으면 show_meet_web 호출 안 함
        self.assertEqual(webview_ctrl.show_meet_web_calls, 0)

    @patch("kiosk_module.kiosk_events.config")
    def test_left_button_does_not_call_show_meet_web(self, mock_config) -> None:
        """왼쪽 버튼은 도어 오픈이지 show_meet_web 이 아니다."""
        # ysoh 2026-06-14
        mock_config.meet_web_url = "https://meet.example.com/test"
        mock_config.vacant_idle_close_seconds = 9999

        controller = _DummyController()
        webview_ctrl = _MockWebViewController()
        handlers = KioskMonitorHandlers(
            controller,
            monitor=object(),
            input_tracker=_FakeInputTracker(),
            webview_controller=webview_ctrl,
        )

        handlers.on_button_pressed(
            ButtonPressEvent(
                left_pressed=True,
                right_pressed=False,
                left_just_pressed=True,
                right_just_pressed=False,
            )
        )

        self.assertEqual(webview_ctrl.show_meet_web_calls, 0)
        self.assertEqual(controller.open_door_calls, 1)

    @patch("kiosk_module.kiosk_events.config")
    def test_status_monitor_right_edge_triggers_meet(self, mock_config) -> None:
        """StatusMonitor 폴링에서 우버튼 0→1 엣지가 잡히면 show_meet_web 호출."""
        # ysoh 2026-06-14
        mock_config.meet_web_url = "https://meet.example.com/test"
        mock_config.vacant_idle_close_seconds = 9999

        monitor = StatusMonitor(MagicMock())
        controller = _DummyController()
        webview_ctrl = _MockWebViewController()
        handlers = KioskMonitorHandlers(
            controller,
            monitor=monitor,
            input_tracker=_FakeInputTracker(),
            webview_controller=webview_ctrl,
        )
        handlers.bind()

        # 초기 상태: 버튼 안 눌림
        monitor._process_status(_status(left=0, right=0))
        # 다음 폴링: 오른쪽만 눌림
        monitor._process_status(_status(left=0, right=1))

        self.assertEqual(webview_ctrl.show_meet_web_calls, 1)
        self.assertEqual(controller.open_door_calls, 0)

    @patch("kiosk_module.kiosk_events.config")
    def test_both_buttons_does_not_call_show_meet_web(self, mock_config) -> None:
        """양쪽 동시 눌림은 도어 닫기이지 show_meet_web 이 아니다."""
        # ysoh 2026-06-14
        mock_config.meet_web_url = "https://meet.example.com/test"
        mock_config.vacant_idle_close_seconds = 9999

        controller = _DummyController()
        webview_ctrl = _MockWebViewController()
        handlers = KioskMonitorHandlers(
            controller,
            monitor=object(),
            input_tracker=_FakeInputTracker(),
            webview_controller=webview_ctrl,
        )

        handlers.on_button_pressed(
            ButtonPressEvent(
                left_pressed=True,
                right_pressed=True,
                left_just_pressed=True,
                right_just_pressed=True,
            )
        )

        self.assertEqual(webview_ctrl.show_meet_web_calls, 0)
        self.assertEqual(controller.close_door_calls, 1)


if __name__ == "__main__":
    unittest.main()
