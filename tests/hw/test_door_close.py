"""도어 내리기 (CLOSE) - 실제 PCB 제어 테스트.

실행:
    uv run pytest tests/hw/test_door_close.py -v -s
"""


def test_door_close(controller):
    ok = controller.close_door()
    assert ok, "도어 CLOSE 명령 전송 실패"
    print("[HW] 도어 CLOSE 전송 완료")
