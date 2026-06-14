"""
GPS SMART KIOSK LED CONTROL 통신 모듈
PCB 시리얼 통신 + 백엔드 WebSocket 브릿지
"""

from .protocol import FrameBuilder, FrameParser, NO_CHANGE, calc_bcc
from .serial_manager import SerialManager
from .device_controller import Controllerer, PcbControlInput
from .status_monitor import StatusMonitor
from .ws_bridge import WSBridge

__version__ = "1.0.0"
__all__ = [
    "FrameBuilder",
    "FrameParser",
    "NO_CHANGE",
    "calc_bcc",
    "SerialManager",
    "Controllerer",
    "PcbControlInput",
    "StatusMonitor",
    "WSBridge",
]
