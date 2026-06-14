"""
상태 조회 클래스 (Command 'S')

PCB에 주기적으로 상태를 요청하고, 변화 발생 시 콜백을 호출.
"""

import asyncio
import logging
import time
from typing import Callable, Optional

from .protocol import (
    CMD_STATUS,
    ButtonPressEvent,
    FrameBuilder,
    FrameParser,
    StatusResponse,
)
from .serial_manager import SerialManager

logger = logging.getLogger(__name__)

DEFAULT_BUTTON_COMBO_WINDOW_SECONDS = 0.15


class StatusMonitor:
    """PCB 상태 모니터링 클래스.

    동기 모드: 한 번 요청하고 응답을 받음.
    비동기 모드: 주기적으로 상태를 폴링하며 변화 시 콜백 호출.

    Usage (동기):
        monitor = StatusMonitor(serial_manager)
        status = monitor.poll_once()
        print(status.ac_light_status1)

    Usage (비동기):
        monitor = StatusMonitor(serial_manager)
        monitor.on_status_changed = my_callback
        await monitor.start_polling(interval=1.0)
    """

    def __init__(
        self,
        serial_manager: SerialManager,
        *,
        button_combo_window_seconds: float = DEFAULT_BUTTON_COMBO_WINDOW_SECONDS,
    ):
        self._serial = serial_manager
        self._polling = False
        self._last_status: Optional[StatusResponse] = None
        self._button_combo_window_seconds = max(0.0, float(button_combo_window_seconds))
        self._pending_button_started_at: float | None = None
        self._pending_left_pressed = False
        self._pending_right_pressed = False
        self._button_action_latched = False

        # 콜백 함수들
        self.on_status_changed: Optional[Callable[[StatusResponse], None]] = None
        self.on_status_received: Optional[Callable[[StatusResponse], None]] = None
        self.on_person_detected: Optional[Callable[[bool], None]] = None
        self.on_button_pressed: Optional[Callable[[ButtonPressEvent], None]] = None

    # ──────────────────────────────────────────
    # 동기 상태 조회 (1회)
    # ──────────────────────────────────────────
    def poll_once(self, timeout: float = 0.5) -> Optional[StatusResponse]:
        """PCB에 상태 요청 후 응답을 받아 반환 (동기).

        Args:
            timeout: 응답 대기 시간 (초)

        Returns:
            StatusResponse 또는 None (타임아웃/에러)
        """
        frame = FrameBuilder.build_status_request_frame()
        response = self._serial.send_and_receive(frame, timeout=timeout)

        if response is None:
            logger.debug("상태 응답 없음 (타임아웃)")
            return None

        if not FrameParser.validate_frame(response):
            logger.debug("상태 응답 프레임 불량: %s", response.hex(" "))
            return None

        status = FrameParser.parse_status_response(response)
        if status is None:
            logger.debug("상태 파싱 실패: %s", response.hex(" "))
            return None

        logger.debug(
            f"상태 수신: AC={status.ac_light_status1}/{status.ac_light_status2}, "
            f"DC={status.dc_light_status1}/{status.dc_light_status2}"
            f"(밝기={status.dc_light_brightness1}/{status.dc_light_brightness2}), "
            f"DOOR={status.door_status} SPK={status.speaker_status}, "
            f"사람={status.person_detected}, "
            f"버튼={status.button_left_status}/{status.button_right_status}"
        )

        self._process_status(status)
        return status

    # ──────────────────────────────────────────
    # 비동기 폴링 루프
    # ──────────────────────────────────────────
    async def start_polling(self, interval: float = 1.0):
        """주기적 상태 폴링 시작 (비동기).

        Args:
            interval: 폴링 주기 (초)
        """
        self._polling = True
        logger.info(f"상태 폴링 시작 (주기: {interval}초)")

        while self._polling:
            try:
                self.poll_once()
            except Exception as e:
                logger.error(f"폴링 에러: {e}")

            await asyncio.sleep(interval)

        logger.info(f"상태 폴링 중지")

    def stop_polling(self):
        """폴링 중지."""
        self._polling = False

    # ──────────────────────────────────────────
    # 수신 프레임 직접 처리 (비동기 리더에서 호출)
    # ──────────────────────────────────────────
    def handle_frame(self, frame: bytes):
        """수신된 프레임이 상태 응답이면 처리.

        SerialManager의 비동기 수신 루프에서 콜백으로 사용 가능.

        Args:
            frame: 수신된 프레임
        """
        if not FrameParser.validate_frame(frame):
            return

        cmd = FrameParser.get_command(frame)
        if cmd != CMD_STATUS:
            return

        status = FrameParser.parse_status_response(frame)
        if status:
            self._process_status(status)

    # ──────────────────────────────────────────
    # 내부: 상태 변화 감지 + 콜백 호출
    # ──────────────────────────────────────────
    def _process_status(self, status: StatusResponse):
        """상태 데이터 처리 및 콜백 호출."""

        # 항상 호출되는 콜백
        if self.on_status_received:
            try:
                self.on_status_received(status)
            except Exception as e:
                logger.error(f"on_status_received 콜백 에러: {e}")

        previous = self._last_status

        # 변화 감지
        if previous is not None:
            changed = self._has_changed(previous, status)

            if changed:
                logger.debug("상태 변화 감지")
                if self.on_status_changed:
                    try:
                        self.on_status_changed(status)
                    except Exception as e:
                        logger.error(f"on_status_changed 콜백 에러: {e}")

                # 사람 감지 이벤트
                if (
                    self.on_person_detected
                    and previous.person_detected != status.person_detected
                ):
                    try:
                        self.on_person_detected(bool(status.person_detected))
                    except Exception as e:
                        logger.error(f"on_person_detected 콜백 에러: {e}")

        self._process_button_status(status)

        self._last_status = status

    def _process_button_status(self, status: StatusResponse) -> None:
        """버튼 상태를 조합 이벤트로 변환한다.

        빠른 폴링 중 버튼을 누르고 있는 동안 같은 동작이 반복되지 않도록 한 번만
        이벤트를 발생시킨다. 한쪽 버튼이 먼저 감지된 직후 반대쪽도 들어오는 경우를
        위해 짧은 조합 유예 시간 동안 좌/우 상태를 합친다.
        """
        if self.on_button_pressed:
            ln = status.button_left_status
            rn = status.button_right_status
            left_pressed = ln != 0
            right_pressed = rn != 0

            if not left_pressed and not right_pressed:
                if self._pending_button_started_at is not None:
                    self._emit_pending_button_event()
                self._clear_pending_button_event()
                self._button_action_latched = False
                return

            if self._button_action_latched:
                return

            now = time.monotonic()
            if self._pending_button_started_at is None:
                self._pending_button_started_at = now

            self._pending_left_pressed = self._pending_left_pressed or left_pressed
            self._pending_right_pressed = self._pending_right_pressed or right_pressed

            if now - self._pending_button_started_at >= self._button_combo_window_seconds:
                self._emit_pending_button_event()
                self._button_action_latched = True

    def _emit_pending_button_event(self) -> None:
        if self._pending_button_started_at is None:
            return
        left_pressed = self._pending_left_pressed
        right_pressed = self._pending_right_pressed
        if not left_pressed and not right_pressed:
            return
        try:
            if self.on_button_pressed:
                self.on_button_pressed(
                    ButtonPressEvent(
                        left_pressed=left_pressed,
                        right_pressed=right_pressed,
                        left_just_pressed=left_pressed,
                        right_just_pressed=right_pressed,
                    )
                )
        except Exception as e:
            logger.error(f"on_button_pressed 콜백 에러: {e}")
        finally:
            self._clear_pending_button_event()

    def _clear_pending_button_event(self) -> None:
        self._pending_button_started_at = None
        self._pending_left_pressed = False
        self._pending_right_pressed = False

    @staticmethod
    def _has_changed(old: StatusResponse, new: StatusResponse) -> bool:
        """두 상태를 비교하여 변화 여부 확인."""
        return old.model_dump() != new.model_dump()

    # ──────────────────────────────────────────
    # 상태 접근
    # ──────────────────────────────────────────
    @property
    def last_status(self) -> Optional[StatusResponse]:
        """마지막으로 수신한 상태."""
        return self._last_status

    def to_dict(self) -> Optional[dict]:
        """마지막 상태를 딕셔너리로 반환 (JSON 변환용)."""
        if self._last_status is None:
            return None

        return self._last_status.model_dump()

    @property
    def is_polling(self) -> bool:
        return self._polling

    def __repr__(self):
        polling = "폴링중" if self._polling else "정지"
        return f"StatusMonitor({polling}, last={self.to_dict()})"
