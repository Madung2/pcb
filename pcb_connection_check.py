from __future__ import annotations

import argparse
import os
import sys
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Iterable

import serial
import serial.tools.list_ports


STX = 0x02
ETX = 0x03
CMD_STATUS = ord("S")
DUMMY_BYTE = 0x00

DEFAULT_BAUDRATE = 115200
DEFAULT_TIMEOUT = 1.0
DEFAULT_ATTEMPTS = 5
DEFAULT_INTERVAL = 0.5


@dataclass(frozen=True)
class StatusResponse:
    ac_light_status1: int
    ac_light_status2: int
    dc_light_status1: int
    dc_light_status2: int
    dc_light_brightness1: int
    dc_light_brightness2: int
    door_status: int
    speaker_status: int
    person_detected: int
    button_left_status: int
    button_right_status: int


class TeeLogger:
    def __init__(self, log_path: Path) -> None:
        self.log_path = log_path
        self._fp = log_path.open("w", encoding="utf-8")

    def close(self) -> None:
        self._fp.close()

    def line(self, text: str = "") -> None:
        print(text)
        self._fp.write(text + "\n")
        self._fp.flush()

    def section(self, title: str) -> None:
        self.line()
        self.line(f"== {title} ==")


def app_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


def parse_env_file(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.is_file():
        return values

    for raw in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        values[key] = value
    return values


def load_serial_defaults() -> dict[str, str]:
    values: dict[str, str] = {}
    for path in (
        app_dir() / "default.env",
        Path.cwd() / "default.env",
        app_dir() / ".env",
        Path.cwd() / ".env",
    ):
        values.update(parse_env_file(path))
    for key in (
        "SERIAL_PORT",
        "SERIAL_USB_VID",
        "SERIAL_USB_PID",
        "SERIAL_USB_SERIAL",
    ):
        if os.getenv(key):
            values[key] = os.environ[key]
    return values


def parse_usb_id(value: str | int | None) -> int | None:
    if value is None:
        return None
    if isinstance(value, int):
        return value if value >= 0 else None
    text = str(value).strip()
    if not text:
        return None
    try:
        if text.lower().startswith("0x"):
            return int(text, 16)
        if all(c in "0123456789abcdefABCDEF" for c in text):
            return int(text, 16)
        return int(text)
    except ValueError:
        return None


def format_hex(data: bytes) -> str:
    return data.hex(" ").upper()


def calc_bcc(payload: bytes) -> int:
    bcc = 0
    for byte in payload:
        bcc ^= byte
    return bcc


def build_status_request_frame() -> bytes:
    payload = bytes([CMD_STATUS, DUMMY_BYTE])
    return bytes([STX]) + payload + bytes([calc_bcc(payload), ETX])


def extract_frames(buffer: bytes) -> tuple[list[bytes], bytes]:
    frames: list[bytes] = []
    start = 0
    total = len(buffer)
    while start < total:
        stx = buffer.find(bytes([STX]), start)
        if stx == -1:
            return frames, buffer[start:]
        etx = buffer.find(bytes([ETX]), stx + 1)
        if etx == -1:
            return frames, buffer[stx:]
        frames.append(buffer[stx : etx + 1])
        start = etx + 1
    return frames, buffer[start:]


def extract_fixed_status_frames(buffer: bytes) -> list[bytes]:
    """Find fixed-length status frames even if a data byte happens to be 0x03."""
    frames: list[bytes] = []
    frame_len = 15  # STX + CMD + 11 data + BCC + ETX
    start = 0
    while True:
        stx = buffer.find(bytes([STX]), start)
        if stx == -1:
            return frames
        candidate = buffer[stx : stx + frame_len]
        if len(candidate) < frame_len:
            return frames
        if candidate[1] == CMD_STATUS and candidate[-1] == ETX:
            frames.append(candidate)
        start = stx + 1


def validate_frame(frame: bytes) -> tuple[bool, str]:
    if len(frame) < 4:
        return False, "too short"
    if frame[0] != STX or frame[-1] != ETX:
        return False, "missing STX/ETX"
    payload = frame[1:-2]
    expected = calc_bcc(payload)
    actual = frame[-2]
    if expected != actual:
        return False, f"BCC mismatch expected={expected:02X} actual={actual:02X}"
    return True, "ok"


def parse_status_response(frame: bytes) -> StatusResponse | None:
    ok, _reason = validate_frame(frame)
    if not ok or len(frame) < 15 or frame[1] != CMD_STATUS:
        return None
    data = frame[2:-2]
    if len(data) < 11:
        return None
    return StatusResponse(
        ac_light_status1=data[0],
        ac_light_status2=data[1],
        dc_light_status1=data[2],
        dc_light_status2=data[3],
        dc_light_brightness1=data[4],
        dc_light_brightness2=data[5],
        door_status=data[6],
        speaker_status=data[7],
        person_detected=data[8],
        button_left_status=data[9],
        button_right_status=data[10],
    )


def list_port_rows() -> list[object]:
    return list(serial.tools.list_ports.comports())


def port_label(port: object) -> str:
    device = getattr(port, "device", "")
    description = getattr(port, "description", "") or "Serial"
    hwid = getattr(port, "hwid", "") or ""
    vid = getattr(port, "vid", None)
    pid = getattr(port, "pid", None)
    serial_number = getattr(port, "serial_number", "") or ""
    pieces = [f"{device} - {description}"]
    if vid is not None and pid is not None:
        usb = f"VID:PID={vid:04X}:{pid:04X}"
        if serial_number:
            usb += f" SER={serial_number}"
        pieces.append(usb)
    if hwid:
        pieces.append(f"HWID={hwid}")
    return " | ".join(pieces)


def find_port_by_usb(
    rows: Iterable[object],
    vid: str | int | None,
    pid: str | int | None,
    serial_number: str | None,
) -> str | None:
    vid_i = parse_usb_id(vid)
    pid_i = parse_usb_id(pid)
    serial_needle = (serial_number or "").strip()
    if vid_i is None or pid_i is None:
        return None
    matches: list[str] = []
    for row in rows:
        if getattr(row, "vid", None) != vid_i or getattr(row, "pid", None) != pid_i:
            continue
        if serial_needle and (getattr(row, "serial_number", "") or "") != serial_needle:
            continue
        matches.append(getattr(row, "device"))
    return matches[0] if matches else None


def find_port_by_keyword(rows: Iterable[object], keyword: str | None) -> str | None:
    needle = (keyword or "").strip().lower()
    if not needle:
        return None
    for row in rows:
        description = (getattr(row, "description", "") or "").lower()
        hwid = (getattr(row, "hwid", "") or "").lower()
        if needle in description or needle in hwid:
            return getattr(row, "device")
    return None


def resolve_port(args: argparse.Namespace, env: dict[str, str], rows: list[object]) -> tuple[str | None, str]:
    if args.port:
        if args.port.upper() != "AUTO":
            return args.port, "command line --port"

    usb_port = find_port_by_usb(
        rows,
        args.usb_vid or env.get("SERIAL_USB_VID"),
        args.usb_pid or env.get("SERIAL_USB_PID"),
        args.usb_serial or env.get("SERIAL_USB_SERIAL"),
    )
    if usb_port:
        return usb_port, "USB VID/PID match"

    raw_port = args.port or env.get("SERIAL_PORT") or "AUTO"
    if raw_port.strip().upper() != "AUTO":
        return raw_port.strip(), ".env SERIAL_PORT"

    keyword = args.keyword or "USB"
    keyword_port = find_port_by_keyword(rows, keyword)
    if keyword_port:
        return keyword_port, f"keyword match {keyword!r}"

    return None, "not found"


def read_response(ser: serial.Serial, timeout: float, logger: TeeLogger) -> tuple[bytes, list[bytes]]:
    deadline = time.monotonic() + timeout
    raw = b""
    buffer = b""
    frames: list[bytes] = []

    while time.monotonic() < deadline:
        waiting = ser.in_waiting
        chunk = ser.read(waiting or 1)
        if chunk:
            raw += chunk
            buffer += chunk
            logger.line(f"RX raw chunk ({len(chunk)}B): {format_hex(chunk)}")
            new_frames, buffer = extract_frames(buffer)
            fixed_frames = extract_fixed_status_frames(raw)
            for frame in [*new_frames, *fixed_frames]:
                if frame not in frames:
                    frames.append(frame)
            if any(parse_status_response(frame) is not None for frame in frames):
                break
    return raw, frames


def print_status(status: StatusResponse, logger: TeeLogger) -> None:
    logger.line("Parsed status:")
    logger.line(f"  AC light       : {status.ac_light_status1} / {status.ac_light_status2}")
    logger.line(f"  DC light       : {status.dc_light_status1} / {status.dc_light_status2}")
    logger.line(f"  DC brightness  : {status.dc_light_brightness1} / {status.dc_light_brightness2}")
    logger.line(f"  Door           : {status.door_status} (0=unknown, 1=close, 2=open, 3=fault)")
    logger.line(f"  Speaker        : {status.speaker_status}")
    logger.line(f"  Person         : {status.person_detected}")
    logger.line(f"  Buttons L/R    : {status.button_left_status} / {status.button_right_status}")


def ordered_scan_targets(
    args: argparse.Namespace,
    env: dict[str, str],
    rows: list[object],
) -> list[tuple[str, str]]:
    targets: list[tuple[str, str]] = []

    def add(port: str | None, reason: str) -> None:
        if not port:
            return
        normalized = port.strip()
        if not normalized:
            return
        if normalized.upper() in {existing.upper() for existing, _ in targets}:
            return
        targets.append((normalized, reason))

    selected, reason = resolve_port(args, env, rows)
    add(selected, reason)

    if args.scan_all or not args.port or (args.port or "").strip().upper() == "AUTO":
        for row in rows:
            add(getattr(row, "device", ""), "detected serial port")

    return targets


def test_port(
    *,
    port: str,
    reason: str,
    baudrate: int,
    timeout: float,
    attempts: int,
    interval: float,
    logger: TeeLogger,
) -> int:
    logger.section(f"Port {port}")
    logger.line(f"Reason   : {reason}")
    logger.line(f"Baudrate : {baudrate}")
    logger.line(f"Timeout  : {timeout:g}s")
    logger.line(f"Attempts : {attempts}")

    try:
        with serial.Serial(
            port=port,
            baudrate=baudrate,
            bytesize=serial.EIGHTBITS,
            stopbits=serial.STOPBITS_ONE,
            parity=serial.PARITY_NONE,
            timeout=0.1,
            write_timeout=1.0,
        ) as ser:
            logger.line(f"OPEN: PASS ({port} @ {baudrate}bps)")
            ser.reset_input_buffer()
            ser.reset_output_buffer()

            frame = build_status_request_frame()
            saw_any_rx = False

            for attempt in range(1, attempts + 1):
                logger.line()
                logger.line(f"Attempt {attempt}/{attempts}")
                written = ser.write(frame)
                ser.flush()
                logger.line(f"TX ({written}B): {format_hex(frame)}")

                raw, frames = read_response(ser, timeout, logger)
                saw_any_rx = saw_any_rx or bool(raw)

                if not raw:
                    logger.line("RX: timeout, no bytes received.")
                if not frames and raw:
                    logger.line("RX: bytes received, but no complete status frame found.")

                for response in frames:
                    logger.line(f"RX frame ({len(response)}B): {format_hex(response)}")
                    valid, reason_text = validate_frame(response)
                    logger.line(f"Frame check: {'PASS' if valid else 'FAIL'} ({reason_text})")
                    status = parse_status_response(response)
                    if status:
                        print_status(status, logger)
                        logger.line(f"PCB FOUND: {port}")
                        return 0
                    if valid:
                        cmd = chr(response[1]) if 32 <= response[1] <= 126 else f"0x{response[1]:02X}"
                        logger.line(f"Valid frame, but not a status response. Command={cmd}")

                if attempt < attempts:
                    time.sleep(interval)

            if saw_any_rx:
                logger.line(f"PORT RESULT: FAIL ({port}) - RX bytes received, but no valid PCB status response.")
                return 3
            logger.line(f"PORT RESULT: FAIL ({port}) - TX succeeded, but no bytes came back.")
            return 4
    except serial.SerialException as exc:
        logger.line(f"OPEN: FAIL ({port}) - {exc}")
        logger.line("Hint: close the kiosk app, serial monitor, or any other program that may already be using this COM port.")
        return 5


def run_check(args: argparse.Namespace, logger: TeeLogger) -> int:
    env = load_serial_defaults()
    rows = list_port_rows()
    logger.section("PCB Connection Check")
    logger.line(f"Started         : {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    logger.line(f"Program folder  : {app_dir()}")
    logger.line(f"Working folder  : {Path.cwd()}")
    logger.line(f"Log file        : {logger.log_path}")
    logger.line(f"Status TX frame : {format_hex(build_status_request_frame())}")

    logger.section("Detected serial ports")
    if rows:
        for row in rows:
            logger.line(f"- {port_label(row)}")
    else:
        logger.line("No serial ports detected.")

    baudrate = int(args.baudrate or DEFAULT_BAUDRATE)
    timeout = float(args.timeout)
    scan_all = bool(args.scan_all or not args.port or (args.port or "").strip().upper() == "AUTO")
    attempts = int(args.attempts if args.attempts is not None else (2 if scan_all else DEFAULT_ATTEMPTS))
    targets = ordered_scan_targets(args, env, rows)

    logger.section("Test plan")
    logger.line(f"Mode     : {'scan all detected ports' if scan_all else 'single selected port'}")
    logger.line(f"Baudrate : {baudrate}")
    logger.line(f"Timeout  : {timeout:g}s")
    logger.line(f"Attempts : {attempts}")
    if targets:
        for port, reason in targets:
            logger.line(f"- {port}: {reason}")
    else:
        logger.line("- No target ports.")

    if not targets:
        logger.section("Result")
        logger.line("FAIL: No COM ports could be selected or detected.")
        return 2

    results: list[tuple[str, int]] = []
    for port, reason in targets:
        result = test_port(
            port=port,
            reason=reason,
            baudrate=baudrate,
            timeout=timeout,
            attempts=attempts,
            interval=float(args.interval),
            logger=logger,
        )
        results.append((port, result))
        if result == 0 and not args.keep_scanning:
            break

    found = [port for port, result in results if result == 0]
    logger.section("Result")
    for port, result in results:
        label = {
            0: "PCB FOUND",
            3: "RX BUT INVALID",
            4: "NO RX",
            5: "OPEN FAIL",
        }.get(result, f"FAIL {result}")
        logger.line(f"- {port}: {label}")

    if found:
        logger.line()
        logger.line(f"PASS: PCB responded on {', '.join(found)}.")
        return 0

    if all(result == 5 for _port, result in results):
        logger.line()
        logger.line("FAIL: Every tested port failed to open.")
        return 5

    logger.line()
    logger.line("FAIL: No tested COM port returned a valid PCB status response.")
    return 4


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Check whether the kiosk PCB serial module responds to the status request frame."
    )
    parser.add_argument("--port", help="COM port to test. If omitted or AUTO, all detected ports are scanned.")
    parser.add_argument("--baudrate", type=int, help=f"Baudrate. Default: .env or {DEFAULT_BAUDRATE}.")
    parser.add_argument("--keyword", help="Auto-detect keyword for port description/HWID. Default: .env or USB.")
    parser.add_argument("--usb-vid", help="USB VID for auto-detect, e.g. 10C4.")
    parser.add_argument("--usb-pid", help="USB PID for auto-detect, e.g. EA60.")
    parser.add_argument("--usb-serial", help="USB serial number for auto-detect.")
    parser.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT, help="Seconds to wait for each response.")
    parser.add_argument("--attempts", type=int, help="Number of status request attempts. Default: 2 when scanning, 5 for one port.")
    parser.add_argument("--interval", type=float, default=DEFAULT_INTERVAL, help="Delay between attempts.")
    parser.add_argument("--scan-all", action="store_true", help="Scan every detected COM port, even when --port is set.")
    parser.add_argument("--keep-scanning", action="store_true", help="Continue scanning after a PCB response is found.")
    parser.add_argument("--no-pause", action="store_true", help="Do not wait for Enter before closing.")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    log_path = app_dir() / f"pcb_connection_check_{timestamp}.log"
    logger = TeeLogger(log_path)
    try:
        code = run_check(args, logger)
    finally:
        logger.close()

    if not args.no_pause:
        try:
            input("\nPress Enter to close...")
        except EOFError:
            pass
    return code


if __name__ == "__main__":
    raise SystemExit(main())
