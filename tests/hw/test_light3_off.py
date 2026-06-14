"""조명 3 (DC1) 끄기 - 실제 PCB 제어 테스트.

실행:
    uv run pytest tests/hw/test_light3_off.py -v -s
"""

from kiosk_module.protocol import LightMode


def test_light3_off(controller):
    ok = controller.set_dc_light(mode=LightMode.OFF, brightness=0, channel=1)
    assert ok, "조명3(DC1) OFF 명령 전송 실패"
    print("[HW] 조명3(DC1) OFF 전송 완료")
