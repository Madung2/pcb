"""조명 4 (DC2) 켜기 - 실제 PCB 제어 테스트.

실행:
    uv run pytest tests/hw/test_light4_on.py -v -s
"""

from kiosk_module.protocol import LightMode


def test_light4_on(controller):
    ok = controller.set_dc_light(mode=LightMode.ON, brightness=10, channel=2)
    assert ok, "조명4(DC2) ON 명령 전송 실패"
    print("[HW] 조명4(DC2) ON 전송 완료 (밝기 10)")
