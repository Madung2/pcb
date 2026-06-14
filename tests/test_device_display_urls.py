import sys
import types


serial_mod = types.ModuleType("serial")
serial_mod.SerialException = Exception
serial_mod.Serial = object
serial_mod.EIGHTBITS = 8
serial_mod.PARITY_NONE = "N"
serial_mod.STOPBITS_ONE = 1
tools_mod = types.ModuleType("serial.tools")
list_ports_mod = types.ModuleType("serial.tools.list_ports")
list_ports_mod.comports = lambda: []
common_mod = types.ModuleType("serial.tools.list_ports_common")


class ListPortInfo:
    pass


common_mod.ListPortInfo = ListPortInfo
serial_mod.tools = tools_mod
tools_mod.list_ports = list_ports_mod
sys.modules.setdefault("serial", serial_mod)
sys.modules.setdefault("serial.tools", tools_mod)
sys.modules.setdefault("serial.tools.list_ports", list_ports_mod)
sys.modules.setdefault("serial.tools.list_ports_common", common_mod)

from kiosk_module.device_display import build_led_url


def test_build_led_url_joins_relative_led_url() -> None:
    assert (
        build_led_url(
            "https://kiosk.jdone.co.kr/kiosk/",
            "led/",
            "device-1",
        )
        == "https://kiosk.jdone.co.kr/kiosk/led/?device_id=device-1"
    )


def test_build_led_url_preserves_existing_device_id() -> None:
    assert (
        build_led_url(
            "https://kiosk.jdone.co.kr/kiosk/",
            "https://led.example/view?device_id=already",
            "device-1",
        )
        == "https://led.example/view?device_id=already"
    )
