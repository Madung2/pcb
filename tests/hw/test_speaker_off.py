"""스피커 끄기 (OFF) - 실제 PCB 제어 테스트.

실행:
    uv run pytest tests/hw/test_speaker_off.py -v -s
"""


def test_speaker_off(controller):
    ok = controller.set_speaker(on=False)
    assert ok, "스피커 OFF 명령 전송 실패"
    print("[HW] 스피커 OFF 전송 완료")
