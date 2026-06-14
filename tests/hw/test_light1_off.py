"""조명 1 (AC1) 끄기 - 실제 PCB 제어 테스트.

실행:
    uv run pytest tests/hw/test_light1_off.py -v -s
"""


def test_light1_off(controller):
    ok = controller.set_ac_light(on=False, channel=1)
    assert ok, "조명1(AC1) OFF 명령 전송 실패"
    print("[HW] 조명1(AC1) OFF 전송 완료")
