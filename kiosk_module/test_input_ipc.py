"""GUI ↔ WebView/워커 프로세스 간 테스트 입력(가짜 PCB) IPC.

``TEST_MODE_ENABLED`` + WebView 별도 프로세스일 때 GUI 가 쓰는 명령 큐.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from ._paths import user_data_root

if TYPE_CHECKING:
    from .fake_serial_manager import FakeSerialManager

logger = logging.getLogger(__name__)

TEST_INPUT_QUEUE_NAME = "test_input.queue"

# GUI → 워커 한 줄 명령
CMD_PERSON_TOGGLE = "person_toggle"
CMD_BTN_LEFT = "btn_left"
CMD_BTN_RIGHT = "btn_right"


def test_input_queue_path():
    return user_data_root() / TEST_INPUT_QUEUE_NAME


def enqueue_test_command(command: str) -> None:
    """테스트 명령 한 줄을 큐 파일에 append."""
    cmd = (command or "").strip()
    if not cmd:
        return
    path = test_input_queue_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(cmd + "\n")
    logger.debug("테스트 입력 큐 추가: %s", cmd)


def _apply_command(mgr: "FakeSerialManager", command: str) -> str | None:
    cmd = (command or "").strip().lower()
    if cmd == CMD_PERSON_TOGGLE:
        new = mgr.toggle_person_detected()
        return f"person_detected → {int(new)}"
    if cmd == CMD_BTN_LEFT:
        mgr.trigger_button_left()
        return "좌버튼 누름 트리거"
    if cmd == CMD_BTN_RIGHT:
        mgr.trigger_button_right()
        return "우버튼 누름 트리거"
    logger.warning("알 수 없는 테스트 입력 명령: %s", command)
    return None


def drain_raw_test_input_queue() -> list[str]:
    """큐에 쌓인 명령 문자열을 읽고 비운다."""
    path = test_input_queue_path()
    if not path.is_file():
        return []
    try:
        raw = path.read_text(encoding="utf-8")
        path.write_text("", encoding="utf-8")
    except OSError:
        logger.exception("테스트 입력 큐 읽기/비우기 실패: %s", path)
        return []
    return [
        line.strip().lower()
        for line in raw.splitlines()
        if line.strip() and not line.strip().startswith("#")
    ]


def drain_test_input_queue(mgr: "FakeSerialManager") -> list[str]:
    """큐에 쌓인 명령을 모두 적용하고 결과 메시지 목록을 반환."""
    results: list[str] = []
    for line in drain_raw_test_input_queue():
        msg = _apply_command(mgr, line)
        if msg:
            results.append(msg)
    return results
