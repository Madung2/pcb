"""조명 3 (DC1) 켜기 - 실제 PCB 제어 테스트.

실행:
    uv run pytest tests/hw/test_light3_on.py -v -s
"""

from kiosk_module.protocol import LightMode


def test_light3_on(controller):
    ok = controller.set_dc_light(mode=LightMode.ON, brightness=10, channel=1)
    assert ok, "조명3(DC1) ON 명령 전송 실패"
    print("[HW] 조명3(DC1) ON 전송 완료 (밝기 10)")
