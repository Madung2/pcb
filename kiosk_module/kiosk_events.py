"""
StatusMonitor·입력 추적에 연결되는 키오스크 비즈니스 이벤트 핸들러.
"""

from __future__ import annotations

import json
import logging
import time
from collections.abc import Callable

from .background_browser import (
    launch_foreground_browser,
    shutdown_background_browser,
)
from .config import config
from .device_controller import Controllerer
from .input_activity import InputActivityTracker
from .protocol import ButtonPressEvent, StatusResponse
from .status_monitor import StatusMonitor
from .webview_protocols import WebViewController
from .ws_bridge import WSBridge

logger = logging.getLogger(__name__)

_INPUT_LOG_THROTTLE_SEC = 2.0

SESSION_MEET_WEB = "meet_web"

_PERSON_DETECTED_EVENT = "PERSON_DETECTED"


def person_detected_ws_payload() -> dict[str, object]:
    """사람 최초 재실 시 백엔드로 보내는 WebSocket JSON 본문."""
    return {
        "event": _PERSON_DETECTED_EVENT,
        "kiosk_id": config.kiosk_id,
    }


class KioskMonitorHandlers:
    """폴링 상태·사람 감지·버튼·입력 유휴에 따른 제어 로직."""

    def __init__(
        self,
        controller: Controllerer,
        monitor: StatusMonitor,
        input_tracker: InputActivityTracker,
        ws_bridge: WSBridge | None = None,
        webview_controller: WebViewController | None = None,
        on_pcb_status_broadcast: Callable[[StatusResponse], None] | None = None,
    ) -> None:
        self._controller = controller
        self._monitor = monitor
        self._input_tracker = input_tracker
        self._ws_bridge = ws_bridge
        self._webview_controller = webview_controller
        self._on_pcb_status_broadcast = on_pcb_status_broadcast
        self._vacancy_idle_closed = False
        self._input_log_at = 0.0
        # 직전 폴링의 person_detected (false→true 엣지에서만 환영 동작)
        self._prev_person_detected = False

    def bind(self) -> None:
        self._input_tracker.on_activity = self._on_input_activity
        self._monitor.on_status_received = self.on_status_received
        self._monitor.on_status_changed = self.on_status_changed
        self._monitor.on_person_detected = self.on_person_detected
        self._monitor.on_button_pressed = self.on_button_pressed

    def _on_input_activity(self) -> None:
        now = time.monotonic()
        if now - self._input_log_at < _INPUT_LOG_THROTTLE_SEC:
            return
        self._input_log_at = now
        logger.info(f"[이벤트] 키보드/마우스 입력 감지")

    def on_status_received(self, status: StatusResponse) -> None:
        now_person = bool(status.person_detected)
        rising_edge = now_person and not self._prev_person_detected
        self._prev_person_detected = now_person

        if now_person:
            self._vacancy_idle_closed = False

        if rising_edge:
            self._on_person_detected_rising_edge()

        if not now_person:
            self._shutdown_meet_web_browser_on_absence()

        if not now_person:
            self._maybe_close_door_on_vacancy_idle()

        if self._on_pcb_status_broadcast is not None:
            try:
                self._on_pcb_status_broadcast(status)
            except Exception:
                logger.exception("PCB 상태 브로드캐스트 콜백 실패")

    def on_status_changed(self, _status: StatusResponse) -> None:
        logger.info(f"[이벤트] 상태 변화: {self._monitor.to_dict()}")

    def on_person_detected(self, detected: bool) -> None:
        logger.info(
            f"[이벤트] 사람 감지: {'감지됨' if detected else '없음'}"
        )

    def on_button_pressed(self, event: ButtonPressEvent) -> None:
        """PCB 버튼 수신 이벤트. 좌·우 로그는 분리하고 조합별 동작은 라우터가 맡김."""
        if event.left_just_pressed:
            self._on_left_button_clicked()
        if event.right_just_pressed:
            self._on_right_button_clicked()
        self._route_button_press_actions(event.left_pressed, event.right_pressed)

    def _on_left_button_clicked(self) -> None:
        logger.info(f"[이벤트] 왼쪽 버튼 클릭됨")

    def _on_right_button_clicked(self) -> None:
        logger.info(f"[이벤트] 오른쪽 버튼 클릭됨")

    def _route_button_press_actions(self, left: bool, right: bool) -> None:
        if left and right:
            self._close_door_on_both_buttons()
        elif left:
            self._open_door_on_left_only()
        elif right:
            self._open_guidance_center_on_right_only()

    ###############################################
    ######           실제 기능             ##########
    ###############################################

    def _on_person_detected_rising_edge(self) -> None:
        """0→1 엣지: AUTO_OPEN 이면 PCB 스피커 ON · WS."""
        self._welcome_on_person_detected_rising_edge()

    def _welcome_on_person_detected_rising_edge(self) -> None:
        """사람 감지 시 항상 PCB 스피커 ON + WS ``PERSON_DETECTED``."""
        self._controller.set_speaker(True)
        if self._ws_bridge is not None:
            ws_body = person_detected_ws_payload()
            self._ws_bridge.schedule_send(ws_body)
            logger.info(
                f"[동작] 사람 감지 엣지(0→1) → PCB 스피커 ON, WS "
                f"{json.dumps(ws_body, ensure_ascii=False)}"
            )
        else:
            logger.info(
                f"[동작] 사람 감지 엣지(0→1) → PCB 스피커 ON "
                f"(WebSocket 비활성화로 이벤트 미전송)"
            )

    def _shutdown_meet_web_browser_on_absence(self) -> None:
        """사람 없음(센서) 시점에는 Meet 화면을 건드리지 않음.

        MeetOne 등은 도어(커버) 자동 닫기(공석 유휴) 시에만 종료/복귀한다.
        """
        return

    def _maybe_close_door_on_vacancy_idle(self) -> None:
        idle = self._input_tracker.seconds_since_activity()
        if idle < config.vacant_idle_close_seconds or self._vacancy_idle_closed:
            return
        self._controller.close_door()
        time.sleep(2)
        self._controller.set_speaker(False)
        if shutdown_background_browser(SESSION_MEET_WEB):
            logger.info(
                "[동작] 공석 유휴 도어 닫기 → Meet 브라우저(세션 %s) 종료",
                SESSION_MEET_WEB,
            )
        if self._webview_controller is not None:
            try:
                self._webview_controller.restore_default_screen()
                logger.info("[동작] 공석 유휴 도어 닫기 → 기본 WebView 화면 복귀")
            except Exception:
                logger.exception("[동작] 기본 WebView 화면 복귀 실패")
        self._vacancy_idle_closed = True
        logger.info(
            f"사람 없음 + 입력 유휴 {idle:.1f}s 이상 → 도어 닫기 & 음향 중지"
        )

    def _open_door_on_left_only(self) -> None:
        self._controller.open_door()
        logger.info(f"[동작] 왼쪽만 눌림 → 도어 오픈")

    def _open_guidance_center_on_right_only(self) -> None:
        """MeetOne 등을 통합 WebView가 있으면 그 화면으로, 없으면 전면 브라우저로 연다.

        ``meet_web_url`` 은 서버 WebSocket 메시지의 ``meet_url`` 로 채워진다.
        """
        self._open_guidance_center(button_label="오른쪽만 눌림")

    def _open_guidance_center(self, *, button_label: str) -> None:
        url = (config.meet_web_url or "").strip()
        if not url:
            if self._ws_bridge is not None:
                self._ws_bridge.schedule_send(
                    {
                        "type": "get_meet_url",
                        "device_id": config.device_id or config.kiosk_id,
                        "kiosk_id": config.kiosk_id,
                    }
                )
            logger.warning(
                "[동작] %s — Meet URL 미수신, 서버에 get_meet_url 요청",
                button_label,
            )
            return
        if self._webview_controller is not None:
            try:
                self._webview_controller.show_meet_web(url)
                logger.info("[동작] %s → Meet 내장 WebView: %s", button_label, url)
                return
            except Exception:
                logger.exception("[동작] %s → Meet 내장 WebView 이동 실패", button_label)
        launch_foreground_browser(
            url,
            session_key=SESSION_MEET_WEB,
            timeout_sec=config.background_browser_timeout_seconds,
            browser_cmd_template=config.kiosk_browser_cmd,
        )
        logger.info("[동작] %s → Meet 전면 브라우저: %s", button_label, url)

    def _close_door_on_both_buttons(self) -> None:
        self._controller.close_door()
        logger.info(f"[동작] 양쪽 동시 눌림 → 도어 클로즈")
