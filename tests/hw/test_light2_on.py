"""조명 2 (AC2) 켜기 - 실제 PCB 제어 테스트.

실행:
    uv run pytest tests/hw/test_light2_on.py -v -s
"""


def test_light2_on(controller):
    ok = controller.set_ac_light(on=True, channel=2)
    assert ok, "조명2(AC2) ON 명령 전송 실패"
    print("[HW] 조명2(AC2) ON 전송 완료")
