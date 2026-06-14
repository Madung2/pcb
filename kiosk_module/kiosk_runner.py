"""
키오스크 시리얼·폴링·WS·입력 추적 공통 실행 루프.

CLI(`main.py`)와 GUI(`gui_main.py`)에서 공유합니다.
"""

from __future__ import annotations

import asyncio
import logging
import os
from collections.abc import Callable

from .config import STATUS_POLL_INTERVAL_SECONDS, config
from .device_controller import Controllerer
from .input_activity import InputActivityTracker
from .background_browser import shutdown_all_background_browsers
from .kiosk_background import run_polling_and_ws
from .kiosk_events import KioskMonitorHandlers
from .kiosk_ws import create_ws_bridge
from .serial_manager import SerialManager, _serial_open_likely_port_busy
from .protocol import ButtonPressEvent, StatusResponse
from .status_monitor import StatusMonitor

logger = logging.getLogger("kiosk_runner")


async def run_kiosk(
    serial_port: str,
    serial_baudrate: int,
    *,
    stop_event: asyncio.Event | None = None,
    controller_ref: dict | None = None,
    webview_controller: object | None = None,
    pcb_status_broadcast: Callable[[StatusResponse], None] | None = None,
) -> None:
    """시리얼 연결 후 선택적 WebSocket·입력 추적을 수행합니다.

    Args:
        serial_port: 시리얼 장치 경로 (예: COM3, /dev/ttyUSB0)
        serial_baudrate: 보드레이트
        stop_event: 설정 시 ``set()`` 될 때까지 루프를 유지하다가 정리 후 반환 (GUI용)
        controller_ref: ``{"controller": Controllerer}`` 형태로 참조를 채움 (GUI 제어용)
    """
    logger.info(
        "run_kiosk 시작: pcb_port=%s pcb_baud=%s volume_enabled=%s volume_port=%s",
        serial_port,
        serial_baudrate,
        config.volume_serial_enabled,
        config.volume_serial_port,
    )
    if config.test_mode_enabled:
        from .fake_serial_manager import FakeSerialManager

        serial_mgr: SerialManager = FakeSerialManager(baudrate=serial_baudrate)
        logger.info("테스트 모드: 가짜 SerialManager 사용 (실제 PCB 미연결)")
        logger.info("PCB 시리얼 연결 시도: FAKE @ %s", serial_baudrate)
        serial_mgr.open()
        logger.info("PCB 시리얼 연결 성공: FAKE @ %s", serial_baudrate)
    else:
        serial_mgr = SerialManager(port=serial_port, baudrate=serial_baudrate)
        logger.info("PCB 시리얼 연결 시도: %s @ %s", serial_port, serial_baudrate)
        if not serial_mgr.open():
            err = serial_mgr.last_open_error
            msg = f"시리얼 포트를 열 수 없습니다: {serial_port}"
            if err is not None and _serial_open_likely_port_busy(err):
                msg += " (포트 점유: 다른 프로세스가 이 COM을 사용 중일 수 있음)"
            logger.error(
                "PCB 시리얼 연결 실패: port=%s baud=%s error=%r",
                serial_port,
                serial_baudrate,
                err,
            )
            if err is not None:
                raise RuntimeError(msg) from err
            raise RuntimeError(msg)
        logger.info("PCB 시리얼 연결 성공: %s @ %s", serial_port, serial_baudrate)

    controller = Controllerer(serial_mgr)
    if controller_ref is not None:
        controller_ref["controller"] = controller
        # GUI 의 가상 입력 트리거에서 FakeSerialManager 메서드를 직접 호출하기 위해 노출.
        controller_ref["serial_manager"] = serial_mgr

    # 키오스크 부팅 시 PCB 스피커가 꺼진 상태로 남아있지 않도록 초기 1회 ON 제어.
    # 다른 필드는 NO_CHANGE(9)로 나가므로 조명/도어 상태는 건드리지 않음.
    logger.info(f"초기 제어: 스피커 ON")
    controller.set_speaker(True)

    monitor = StatusMonitor(serial_mgr)
    input_tracker = InputActivityTracker(enabled=config.input_monitor_enabled)
    bridge = create_ws_bridge(controller, monitor)

    handlers = KioskMonitorHandlers(
        controller,
        monitor,
        input_tracker,
        ws_bridge=bridge,
        webview_controller=webview_controller,
        on_pcb_status_broadcast=pcb_status_broadcast,
    )
    handlers.bind()
    try:
        input_tracker.start()
    except Exception:
        logger.exception(
            "입력 추적(pynput) 시작 실패 — INPUT_MONITOR_ENABLED를 끄거나 "
            "macOS 접근성에서 터미널/Python을 허용했는지 확인하세요."
        )
        raise

    volume_listener = None
    if config.volume_serial_enabled:
        from .volume_serial_controller import VolumeSerialListener

        volume_port = (config.volume_serial_port or "").strip()
        if not volume_port:
            logger.warning(
                "볼륨 노브 시리얼 비활성화: VOLUME_SERIAL_ENABLED=true 이지만 "
                "VOLUME_SERIAL_PORT 가 비어 있습니다."
            )
        elif volume_port.upper() == str(serial_port).strip().upper():
            logger.warning(
                "볼륨 노브 시리얼 비활성화: PCB 포트와 볼륨 노브 포트가 같습니다 (%s).",
                volume_port,
            )
        else:
            if os.name != "nt":
                logger.warning(
                    "비Windows 환경입니다. 볼륨 노브 시리얼은 run_kiosk에서 연결하지만 "
                    "OS 볼륨 키 전송은 Windows에서만 동작합니다."
                )
            logger.info(
                "볼륨 노브 시리얼 연결 시작: %s @ %s timeout=%s",
                volume_port,
                config.volume_serial_baudrate,
                config.volume_serial_timeout,
            )
            volume_listener = VolumeSerialListener(
                port=volume_port,
                baudrate=config.volume_serial_baudrate,
                timeout=config.volume_serial_timeout,
                up_hex=config.volume_up_hex_codes,
                down_hex=config.volume_down_hex_codes,
            )
            volume_listener.start()
            logger.info(
                "OS 볼륨 시리얼 리스너 시작 요청 완료 (%s @ %s)",
                volume_port,
                config.volume_serial_baudrate,
            )
    else:
        logger.info("볼륨 노브 시리얼 비활성화: VOLUME_SERIAL_ENABLED=false")

    test_input_task: asyncio.Task | None = None
    from .test_input_ipc import (
        CMD_BTN_LEFT,
        CMD_BTN_RIGHT,
        CMD_PERSON_TOGGLE,
        drain_raw_test_input_queue,
    )

    async def _test_input_drain_loop() -> None:
        while stop_event is None or not stop_event.is_set():
            for command in drain_raw_test_input_queue():
                if command == CMD_BTN_RIGHT:
                    handlers.on_button_pressed(
                        ButtonPressEvent(
                            left_pressed=False,
                            right_pressed=True,
                            left_just_pressed=False,
                            right_just_pressed=True,
                        )
                    )
                    logger.info("[TEST] 오른쪽 버튼 이벤트 가정")
                elif command == CMD_BTN_LEFT:
                    handlers.on_button_pressed(
                        ButtonPressEvent(
                            left_pressed=True,
                            right_pressed=False,
                            left_just_pressed=True,
                            right_just_pressed=False,
                        )
                    )
                    logger.info("[TEST] 왼쪽 버튼 이벤트 가정")
                elif command == CMD_PERSON_TOGGLE:
                    logger.info("[TEST] person_toggle 은 FakeSerialManager 전용이라 생략")
            await asyncio.sleep(0.25)

    test_input_task = asyncio.create_task(_test_input_drain_loop())

    try:
        await run_polling_and_ws(
            monitor,
            bridge,
            stop_event=stop_event,
            poll_interval=STATUS_POLL_INTERVAL_SECONDS,
        )
    except asyncio.CancelledError:
        pass
    finally:
        if test_input_task is not None:
            test_input_task.cancel()
            try:
                await test_input_task
            except asyncio.CancelledError:
                pass
        if volume_listener is not None:
            volume_listener.stop()
            logger.info("OS 볼륨 시리얼 리스너 종료")
        logger.info(f"종료 중...")
        shutdown_all_background_browsers()
        input_tracker.stop()
        if bridge is not None:
            await bridge.disconnect()
        serial_mgr.close()
        if controller_ref is not None:
            controller_ref.clear()
        logger.info(f"프로그램 종료")
