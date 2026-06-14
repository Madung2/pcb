"""조명 4 (DC2) 끄기 - 실제 PCB 제어 테스트.

실행:
    uv run pytest tests/hw/test_light4_off.py -v -s
"""

from kiosk_module.protocol import LightMode


def test_light4_off(controller):
    ok = controller.set_dc_light(mode=LightMode.OFF, brightness=0, channel=2)
    assert ok, "조명4(DC2) OFF 명령 전송 실패"
    print("[HW] 조명4(DC2) OFF 전송 완료")
