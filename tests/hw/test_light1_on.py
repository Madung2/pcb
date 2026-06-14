"""조명 1 (AC1) 켜기 - 실제 PCB 제어 테스트.

실행:
    uv run pytest tests/hw/test_light1_on.py -v -s
    uv run pytest tests/hw/test_light1_on.py -v -s --port=COM3
"""


def test_light1_on(controller):
    ok = controller.set_ac_light(on=True, channel=1)
    assert ok, "조명1(AC1) ON 명령 전송 실패"
    print("[HW] 조명1(AC1) ON 전송 완료")
