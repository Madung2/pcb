"""
하드웨어 통합 pytest 픽스처.

실제 PCB가 연결된 시리얼 포트를 열어 ``Controller`` 인스턴스를 제공합니다.
포트 열기에 실패하면 해당 테스트는 스킵됩니다.

포트 지정 우선순위:
    1) pytest --port=COMn
    2) 환경변수 SERIAL_PORT
    3) .env 파일의 SERIAL_PORT
    4) 기본값 COM3
"""

from __future__ import annotations

import os
import time
from pathlib import Path

import pytest

try:
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).resolve().parents[2] / ".env")
except Exception:
    pass

from kiosk_module.device_controller import Controller
from kiosk_module.serial_manager import SerialManager


def pytest_addoption(parser):
    parser.addoption(
        "--port",
        action="store",
        default=None,
        help="PCB 시리얼 포트 (예: COM3). 지정 안 하면 SERIAL_PORT env 사용.",
    )
    parser.addoption(
        "--baud",
        action="store",
        default=None,
        type=int,
        help="보드레이트 (기본 115200)",
    )
    parser.addoption(
        "--dim-level",
        action="store",
        default=None,
        type=int,
        help="DC 조명 DIMMING 밝기 (0~10). 지정 안 하면 DIM_LEVEL env, 없으면 5.",
    )


def _resolve_port(config) -> str:
    cli = config.getoption("--port")
    if cli:
        return cli
    return os.getenv("SERIAL_PORT", "COM3")


def _resolve_baud(config) -> int:
    cli = config.getoption("--baud")
    if cli:
        return int(cli)
    return 115200


def _resolve_dim_level(config) -> int:
    cli = config.getoption("--dim-level")
    if cli is not None:
        level = int(cli)
    else:
        level = int(os.getenv("DIM_LEVEL", "5"))
    return max(0, min(10, level))


@pytest.fixture(scope="session")
def serial_port(pytestconfig) -> str:
    return _resolve_port(pytestconfig)


@pytest.fixture(scope="session")
def baudrate(pytestconfig) -> int:
    return _resolve_baud(pytestconfig)


@pytest.fixture
def dim_level(pytestconfig) -> int:
    """DC 조명 DIMMING 밝기 (0~10).

    우선순위: ``pytest --dim-level=N`` > ``DIM_LEVEL`` env > 기본 5.
    """
    return _resolve_dim_level(pytestconfig)


@pytest.fixture
def controller(serial_port: str, baudrate: int):
    """실제 PCB 시리얼 포트를 열어 Controller 제공."""
    mgr = SerialManager(port=serial_port, baudrate=baudrate)
    if not mgr.open():
        pytest.skip(f"시리얼 포트 열기 실패: {serial_port} @ {baudrate}")

    print(f"\n[HW] 시리얼 연결: {serial_port} @ {baudrate}")
    ctrl = Controller(mgr)
    try:
        yield ctrl
        time.sleep(0.2)  # PCB가 마지막 명령을 처리할 시간
    finally:
        mgr.close()
        print(f"[HW] 시리얼 해제: {serial_port}")
