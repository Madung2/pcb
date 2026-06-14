"""테스트 모드용 가짜 SerialManager.

실제 PCB가 연결돼 있지 않아도 ``SerialManager`` 와 동일한 인터페이스로 동작한다.
내부에 가상 PCB 상태를 들고 있고:

- ``send(frame)`` 으로 들어온 컨트롤 프레임(Command 'L') 을 파싱해 상태를 갱신
- ``send_and_receive(frame)`` 로 들어온 상태 요청(Command 'S') 에 합성된 응답 프레임 반환

이를 통해 ``Controller`` · ``StatusMonitor`` · ``KioskMonitorHandlers`` · WS 브릿지 등
상위 로직은 변경 없이 그대로 흘러간다.
"""

from __future__ import annotations

import logging
import threading
from typing import Optional

from .protocol import (
    CMD_CONTROL,
    CMD_STATUS,
    DoorAction,
    DoorStatus,
    ETX,
    NO_CHANGE,
    STX,
    calc_bcc,
)
from .serial_manager import SerialManager

logger = logging.getLogger(__name__)


def _door_action_to_status(action: int, prev: int) -> int:
    """제어 명령의 ``DoorAction`` → 상태 응답의 ``DoorStatus`` 매핑."""
    if action == DoorAction.OPEN:
        return DoorStatus.OPEN
    if action == DoorAction.CLOSE:
        return DoorStatus.CLOSE
    # OFF(0) 등 그 외 값은 기존 상태 유지 (도어 릴레이 정지로 해석)
    return prev


class FakeSerialManager(SerialManager):
    """테스트용 가짜 시리얼. 실제 포트는 열지 않는다."""

    def __init__(self, baudrate: int = SerialManager.DEFAULT_BAUDRATE):
        super().__init__(port="FAKE", baudrate=baudrate)
        self._fake_open = False
        # 가상 PCB 상태(상태 응답 프레임에 그대로 실린다).
        self._state: dict[str, int] = {
            "ac_light_status1": 0,
            "ac_light_status2": 0,
            "dc_light_status1": 0,
            "dc_light_status2": 0,
            "dc_light_brightness1": 0,
            "dc_light_brightness2": 0,
            "door_status": int(DoorStatus.CLOSE),
            "speaker_status": 0,
            "person_detected": 0,
            "button_left_status": 0,
            "button_right_status": 0,
        }
        # 다음 상태 응답에서 1로 보고된 뒤 자동으로 0으로 되돌릴 버튼(0→1→0 엣지 생성).
        self._pending_left_press = False
        self._pending_right_press = False
        self._lock = threading.Lock()

    # ──────────────────────────────────────────
    # 연결 관리
    # ──────────────────────────────────────────
    def open(self) -> bool:
        self._fake_open = True
        self.last_open_error = None
        logger.info("가짜 시리얼 열림 (테스트 모드)")
        return True

    def close(self) -> None:
        self._fake_open = False
        logger.info("가짜 시리얼 닫힘 (테스트 모드)")

    @property
    def is_connected(self) -> bool:
        return self._fake_open

    # ──────────────────────────────────────────
    # 송수신
    # ──────────────────────────────────────────
    def send(self, frame: bytes) -> bool:
        if not self._fake_open:
            logger.error("가짜 시리얼 미열림")
            return False
        self._apply_frame(frame)
        return True

    def receive(self, timeout: Optional[float] = None) -> Optional[bytes]:
        # StatusMonitor 는 send_and_receive 경로만 사용. 안전망으로 합성 상태를 돌려준다.
        if not self._fake_open:
            return None
        return self._build_status_response_frame()

    def send_and_receive(self, frame: bytes, timeout: float = 0.5) -> Optional[bytes]:
        if not self.send(frame):
            return None
        cmd = self._frame_command(frame)
        if cmd == CMD_STATUS:
            return self._build_status_response_frame()
        # 다른 명령(L/T/P)은 응답을 기대하지 않음.
        return None

    # ──────────────────────────────────────────
    # 비동기 수신 루프 — 가짜에선 사용하지 않지만 인터페이스 호환을 위해 no-op.
    # ──────────────────────────────────────────
    async def start_reading(self, on_frame, interval: float = 0.01) -> None:  # type: ignore[override]
        logger.debug("가짜 시리얼: start_reading no-op (테스트 모드)")

    def stop_reading(self) -> None:  # type: ignore[override]
        return

    # ──────────────────────────────────────────
    # 테스트 입력 (GUI 가상 입력 트리거에서 사용)
    # ──────────────────────────────────────────
    def set_person_detected(self, detected: bool) -> None:
        with self._lock:
            self._state["person_detected"] = 1 if detected else 0
        logger.info("[테스트] person_detected=%s", int(bool(detected)))

    def toggle_person_detected(self) -> bool:
        with self._lock:
            new = 0 if self._state["person_detected"] else 1
            self._state["person_detected"] = new
        logger.info("[테스트] person_detected → %s", new)
        return bool(new)

    def trigger_button_left(self) -> None:
        with self._lock:
            self._pending_left_press = True
        logger.info("[테스트] 좌버튼 누름 트리거")

    def trigger_button_right(self) -> None:
        with self._lock:
            self._pending_right_press = True
        logger.info("[테스트] 우버튼 누름 트리거")

    # ──────────────────────────────────────────
    # 내부: 컨트롤 프레임 적용 / 상태 응답 합성
    # ──────────────────────────────────────────
    @staticmethod
    def _frame_command(frame: bytes) -> Optional[int]:
        if len(frame) < 4 or frame[0] != STX or frame[-1] != ETX:
            return None
        return frame[1]

    def _apply_frame(self, frame: bytes) -> None:
        cmd = self._frame_command(frame)
        if cmd != CMD_CONTROL:
            return
        # STX | 'L' | AC1 AC2 DC1 DC2 DCB1 DCB2 DOOR SPK | BCC | ETX = 12 bytes
        if len(frame) < 12:
            return
        data = frame[2:-2]
        if len(data) < 8:
            return
        ac1, ac2, dc1, dc2, b1, b2, door, spk = data[:8]
        with self._lock:
            if ac1 != NO_CHANGE:
                self._state["ac_light_status1"] = int(ac1)
            if ac2 != NO_CHANGE:
                self._state["ac_light_status2"] = int(ac2)
            if dc1 != NO_CHANGE:
                self._state["dc_light_status1"] = int(dc1)
            if dc2 != NO_CHANGE:
                self._state["dc_light_status2"] = int(dc2)
            if b1 != NO_CHANGE:
                self._state["dc_light_brightness1"] = int(b1)
            if b2 != NO_CHANGE:
                self._state["dc_light_brightness2"] = int(b2)
            if door != NO_CHANGE:
                self._state["door_status"] = _door_action_to_status(
                    int(door), self._state["door_status"]
                )
            if spk != NO_CHANGE:
                self._state["speaker_status"] = int(spk)
        logger.debug("가짜 PCB 상태 갱신: %s", self._state)

    def _build_status_response_frame(self) -> bytes:
        with self._lock:
            # 펜딩 버튼 누름은 이번 한 폴링만 1로 보고하고 자동 해제 → 0→1→0 엣지 생성.
            left = 1 if self._pending_left_press else self._state["button_left_status"]
            right = 1 if self._pending_right_press else self._state["button_right_status"]
            self._pending_left_press = False
            self._pending_right_press = False
            data = bytes([
                self._state["ac_light_status1"],
                self._state["ac_light_status2"],
                self._state["dc_light_status1"],
                self._state["dc_light_status2"],
                self._state["dc_light_brightness1"],
                self._state["dc_light_brightness2"],
                self._state["door_status"],
                self._state["speaker_status"],
                self._state["person_detected"],
                left,
                right,
            ])
        payload = bytes([CMD_STATUS]) + data
        bcc = calc_bcc(payload)
        return bytes([STX]) + payload + bytes([bcc, ETX])

    def __repr__(self) -> str:
        status = "연결됨(가짜)" if self._fake_open else "미연결(가짜)"
        return f"FakeSerialManager({status})"
