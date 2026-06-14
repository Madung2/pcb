"""도어 열기 (OPEN) - 실제 PCB 제어 테스트.

실행:
    uv run pytest tests/hw/test_door_open.py -v -s
"""


def test_door_open(controller):
    ok = controller.open_door()
    assert ok, "도어 OPEN 명령 전송 실패"
    print("[HW] 도어 OPEN 전송 완료")
