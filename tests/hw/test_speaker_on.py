"""스피커 켜기 (MAIN) - 실제 PCB 제어 테스트.

실행:
    uv run pytest tests/hw/test_speaker_on.py -v -s
"""


def test_speaker_on(controller):
    ok = controller.set_speaker(on=True)
    assert ok, "스피커 ON 명령 전송 실패"
    print("[HW] 스피커 ON (MAIN) 전송 완료")
