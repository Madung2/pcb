"""
별도 시리얼 포트(``VOLUME_SERIAL_PORT``·``VOLUME_BAUDRATE``)에서
문자 ``U`` / ``D`` 수신 시 Windows OS 마스터 볼륨을 올리거나 내린다.

사용 예:
    uv run python -m kiosk_module.volume_serial_controller

키오스크 실행 시에는 ``VOLUME_SERIAL_ENABLED=true`` 로 ``kiosk_runner`` 수명 주기에
같은 리스너가 함께 시작·종료됩니다.
"""

from __future__ import annotations

import ctypes
import logging
import os
import sys
import threading
import time
from pathlib import Path

import serial
from ctypes import wintypes
from dotenv import load_dotenv


def _bootstrap_dotenv() -> None:
    """실행 파일(.exe)일 때 실행 파일 옆 .env를 우선 로드."""
    if getattr(sys, "frozen", False):
        exe_dir = Path(sys.executable).resolve().parent
        load_dotenv(exe_dir / ".env", override=True)
    load_dotenv(override=False)


def _hex_code_set_from_csv(csv: str) -> frozenset[str]:
    return frozenset(
        t.strip().lower()
        for t in (csv or "").split(",")
        if t.strip()
    )


def _volume_baud_from_env() -> int:
    raw = (
        os.getenv("VOLUME_BAUDRATE")
        or os.getenv("VOLUME_SERIAL_BAUDRATE")
        or "38400"
    ).strip()
    return int(raw)


VK_VOLUME_UP = 0xAF
VK_VOLUME_DOWN = 0xAE
KEYEVENTF_KEYUP = 0x0002
KEYEVENTF_EXTENDEDKEY = 0x0001
INPUT_KEYBOARD = 1
MAPVK_VK_TO_VSC = 0
MAPVK_VK_TO_VSC_EX = 4

if ctypes.sizeof(ctypes.c_void_p) == 8:
    _ULONG_PTR = ctypes.c_ulonglong
else:
    _ULONG_PTR = ctypes.c_ulong


class _MOUSEINPUT(ctypes.Structure):
    _fields_ = [
        ("dx", wintypes.LONG),
        ("dy", wintypes.LONG),
        ("mouseData", wintypes.DWORD),
        ("dwFlags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", _ULONG_PTR),
    ]


class _KEYBDINPUT(ctypes.Structure):
    _fields_ = [
        ("wVk", wintypes.WORD),
        ("wScan", wintypes.WORD),
        ("dwFlags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", _ULONG_PTR),
    ]


class _HARDWAREINPUT(ctypes.Structure):
    _fields_ = [
        ("uMsg", wintypes.DWORD),
        ("wParamL", wintypes.WORD),
        ("wParamH", wintypes.WORD),
    ]


class _INPUTUNION(ctypes.Union):
    _fields_ = [("mi", _MOUSEINPUT), ("ki", _KEYBDINPUT), ("hi", _HARDWAREINPUT)]


class _INPUT(ctypes.Structure):
    _fields_ = [("type", wintypes.DWORD), ("union", _INPUTUNION)]


def tap_volume_media_key(vk_code: int) -> None:
    """Windows 볼륨 미디어 키: ``keybd_event``보다 ``SendInput``+확장키가 안정적."""
    user32 = ctypes.windll.user32
    kernel32 = ctypes.windll.kernel32
    scan_ex = user32.MapVirtualKeyW(vk_code, MAPVK_VK_TO_VSC_EX) & 0xFF
    scan = scan_ex or (user32.MapVirtualKeyW(vk_code, MAPVK_VK_TO_VSC) & 0xFF)
    arr = (_INPUT * 2)()
    flags_down = KEYEVENTF_EXTENDEDKEY
    flags_up = KEYEVENTF_EXTENDEDKEY | KEYEVENTF_KEYUP
    for i, (flags,) in enumerate(((flags_down,), (flags_up,))):
        arr[i].type = INPUT_KEYBOARD
        arr[i].union.ki.wVk = vk_code
        arr[i].union.ki.wScan = scan
        arr[i].union.ki.dwFlags = flags
        arr[i].union.ki.time = 0
        arr[i].union.ki.dwExtraInfo = 0
    kernel32.SetLastError(0)
    n = user32.SendInput(2, ctypes.byref(arr), ctypes.sizeof(_INPUT))
    if n != 2:
        err = kernel32.GetLastError()
        raise OSError(f"SendInput 볼륨 키 실패 (반환 {n}, GetLastError={err})")


def apply_volume_command(
    raw: bytes,
    decoded: str,
    logger: logging.Logger,
    *,
    up_hex: frozenset[str] | set[str],
    down_hex: frozenset[str] | set[str],
) -> bool:
    """``U`` → OS 볼륨 업, ``D`` → OS 볼륨 다운(Windows 볼륨 미디어 키). hex 코드는 선택."""
    cmd = decoded.strip().upper()
    raw_hex = raw.hex().lower()

    is_up: bool | None = None
    reason = ""

    if cmd == "U" or raw_hex in up_hex:
        is_up = True
        reason = f"UP ({cmd if cmd else raw_hex})"
    elif cmd == "D" or raw_hex in down_hex:
        is_up = False
        reason = f"DOWN ({cmd if cmd else raw_hex})"
    else:
        return False

    if os.name != "nt":
        logger.warning(
            "볼륨 명령 수신했지만 OS 볼륨 키 전송은 Windows에서만 지원됩니다: %s",
            reason,
        )
        return False

    try:
        tap_volume_media_key(VK_VOLUME_UP if is_up else VK_VOLUME_DOWN)
        logger.info("[성공] OS 볼륨 조절 키 전송: %s", reason)
        return True
    except Exception as exc:
        logger.error("가상 키 입력 실패: %s", exc)
        return False


class VolumeSerialListener:
    """백그라운드 스레드에서 시리얼 바이트를 읽어 OS 볼륨을 조절."""

    def __init__(
        self,
        *,
        port: str,
        baudrate: int,
        timeout: float,
        up_hex: frozenset[str] | set[str],
        down_hex: frozenset[str] | set[str],
    ) -> None:
        self._port = port.strip()
        self._baudrate = baudrate
        self._timeout = timeout
        self._up = frozenset(up_hex)
        self._down = frozenset(down_hex)
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop.clear()
        logging.getLogger("volume-serial").info(
            "볼륨용 시리얼 리스너 스레드 시작: %s @ %s timeout=%s",
            self._port,
            self._baudrate,
            self._timeout,
        )
        self._thread = threading.Thread(
            target=self._run,
            name="volume-serial",
            daemon=True,
        )
        self._thread.start()

    def stop(self, *, join_timeout: float = 5.0) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=join_timeout)
            self._thread = None

    def _run(self) -> None:
        log = logging.getLogger("volume-serial")
        while not self._stop.is_set():
            try:
                log.info(
                    "볼륨용 시리얼 연결 시도: %s @ %s timeout=%s",
                    self._port,
                    self._baudrate,
                    self._timeout,
                )
                with serial.Serial(
                    port=self._port,
                    baudrate=self._baudrate,
                    timeout=self._timeout,
                ) as ser:
                    log.info(
                        "볼륨용 시리얼 연결됨: %s @ %s — U=볼륨업 D=볼륨다운",
                        ser.port,
                        self._baudrate,
                    )
                    while not self._stop.is_set():
                        chunk = ser.read(ser.in_waiting or 1)
                        if not chunk:
                            continue
                        for raw in (chunk[i : i + 1] for i in range(len(chunk))):
                            msg = raw.decode(errors="ignore")
                            log.info(
                                "시리얼 수신 raw=%s decoded=%r",
                                raw.hex(),
                                msg,
                            )
                            if apply_volume_command(
                                raw,
                                msg,
                                log,
                                up_hex=self._up,
                                down_hex=self._down,
                            ):
                                log.info(
                                    "볼륨 명령 처리 완료: %r", msg.upper()
                                )
                            else:
                                log.debug("무시된 입력: %r", msg)
            except serial.SerialException as exc:
                if self._stop.is_set():
                    break
                log.warning(
                    "볼륨용 시리얼 미연결/오류(%s). 2초 후 재시도 — "
                    "Hercules 등이 같은 COM을 이미 열었으면 한쪽만 열 수 있습니다.",
                    exc,
                )
                for _ in range(20):
                    if self._stop.wait(0.1):
                        break


def run(port: str | None = None, baudrate: int | None = None) -> None:
    if os.name != "nt":
        raise SystemExit("이 스크립트는 Windows에서만 동작합니다.")

    logging.basicConfig(
        level=getattr(
            logging,
            os.getenv("LOG_LEVEL", "INFO"),
            logging.INFO,
        ),
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    logger = logging.getLogger("volume-serial")

    resolved_port = (port or os.getenv("VOLUME_SERIAL_PORT", "COM5") or "COM5").strip()
    resolved_baudrate = (
        int(baudrate) if baudrate is not None else _volume_baud_from_env()
    )
    timeout = float(os.getenv("VOLUME_SERIAL_TIMEOUT", "0.2"))
    up = _hex_code_set_from_csv(os.getenv("VOLUME_UP_HEX_CODES", "") or "")
    down = _hex_code_set_from_csv(os.getenv("VOLUME_DOWN_HEX_CODES", "") or "")

    listener = VolumeSerialListener(
        port=resolved_port,
        baudrate=resolved_baudrate,
        timeout=timeout,
        up_hex=up,
        down_hex=down,
    )
    logger.info("볼륨 시리얼 리스너 시작: %s @ %s", resolved_port, resolved_baudrate)
    listener.start()
    try:
        while True:
            time.sleep(3600)
    except KeyboardInterrupt:
        pass
    finally:
        listener.stop()


if __name__ == "__main__":
    _bootstrap_dotenv()
    run()
