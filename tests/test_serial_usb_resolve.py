"""USB VID/PID 기반 시리얼 포트 해석 테스트."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from kiosk_module.serial_manager import SerialManager, _parse_usb_id


def test_parse_usb_id_hex_and_decimal():
    assert _parse_usb_id("10C4") == 0x10C4
    assert _parse_usb_id("0x10c4") == 0x10C4
    assert _parse_usb_id(4292) == 4292
    assert _parse_usb_id("") is None
    assert _parse_usb_id(None) is None


def _mock_port(device: str, vid: int, pid: int, serial: str | None = None):
    p = MagicMock()
    p.device = device
    p.vid = vid
    p.pid = pid
    p.serial_number = serial
    p.description = "USB Serial"
    return p


@patch("kiosk_module.serial_manager.serial.tools.list_ports.comports")
def test_find_port_by_usb_vid_pid(comports):
    comports.return_value = [
        _mock_port("COM3", 0x10C4, 0xEA60, "ABC"),
        _mock_port("COM8", 0x1A86, 0x7523),
    ]
    assert SerialManager.find_port_by_usb("10C4", "EA60", "ABC") == "COM3"
    assert SerialManager.find_port_by_usb("1A86", "7523") == "COM8"
    assert SerialManager.find_port_by_usb("10C4", "EA60", "WRONG") is None
    assert SerialManager.find_port_by_usb("FFFF", "FFFF") is None


@patch("kiosk_module.serial_manager.serial.tools.list_ports.comports")
def test_resolve_port_choice_usb_priority(comports):
    comports.return_value = [_mock_port("COM5", 0x10C4, 0xEA60)]
    port = SerialManager.resolve_port_choice(
        "COM99",
        "USB",
        usb_vid="10C4",
        usb_pid="EA60",
    )
    assert port == "COM5"


@patch("kiosk_module.serial_manager.serial.tools.list_ports.comports")
def test_get_port_usb_fields(comports):
    comports.return_value = [_mock_port("COM7", 0xABCD, 0x1234, "SER1")]
    assert SerialManager.get_port_usb_fields("COM7") == (0xABCD, 0x1234, "SER1")
    assert SerialManager.get_port_usb_fields("COM0") == (None, None, None)
