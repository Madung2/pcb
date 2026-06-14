"""
PCB 상태 폴링을 대략적으로 모킹한 person_detected 시나리오 테스트.

실제 시리얼 대신 ``StatusMonitor._process_status`` 에 ``StatusResponse`` 를
차례로 넣어, 폴링 루프에서 ``on_status_received`` 로 들어가는 경로를 흉내 냅니다.
첫 프레임은 ``StatusMonitor`` 가 이전 상태를 모를 때와 같이 동작합니다.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from kiosk_module.kiosk_events import KioskMonitorHandlers
from kiosk_module.protocol import StatusResponse
from kiosk_module.status_monitor import StatusMonitor


def _status(*, person_detected: int) -> StatusResponse:
    return StatusResponse(
        ac_light_status1=0,
        ac_light_status2=0,
        dc_light_status1=0,
        dc_light_status2=0,
        dc_light_brightness1=0,
        dc_light_brightness2=0,
        door_status=0,
        speaker_status=0,
        person_detected=person_detected,
        button_left_status=0,
        button_right_status=0,
    )


class _FakeController:
    def __init__(self) -> None:
        self.speaker_calls: list[bool] = []

    def close_door(self) -> None:
        pass

    def set_speaker(self, on: bool) -> None:
        self.speaker_calls.append(on)

    def open_door(self) -> None:
        pass


class _FakeInputTracker:
    def seconds_since_activity(self) -> float:
        return 0.0


def _run_poll_scenario(
    person_sequence: list[int],
    *,
    ws_bridge: MagicMock | None = None,
) -> tuple[KioskMonitorHandlers, StatusMonitor, _FakeController, MagicMock]:
    """person_detected 값 시퀀스를 PCB 폴링처럼 ``StatusMonitor`` 에 밀어 넣는다."""
    controller = _FakeController()
    bridge = ws_bridge or MagicMock()
    handlers = KioskMonitorHandlers(
        controller,
        monitor=object(),
        input_tracker=_FakeInputTracker(),
        ws_bridge=bridge,
    )
    monitor = StatusMonitor(MagicMock())
    monitor.on_status_received = handlers.on_status_received

    for pd in person_sequence:
        monitor._process_status(_status(person_detected=pd))

    return handlers, monitor, controller, bridge


def test_scenario_enter_stay_leave_reenter() -> None:
    """비어 있음 → 유지 → 접근 → 체류 → 이탈 → 재접근 시 스피커/WS 는 엣지당 1회."""
    _handlers, _monitor, controller, bridge = _run_poll_scenario(
        [0, 0, 1, 1, 1, 0, 0, 1],
    )

    assert controller.speaker_calls.count(True) == 2
    assert bridge.schedule_send.call_count == 2


def test_scenario_ws_payload_once_per_rising_edge() -> None:
    """WS bridge 가 person 0→1 엣지에서만 PERSON_DETECTED 를 스케줄한다."""
    _handlers, _monitor, _controller, bridge = _run_poll_scenario(
        [0, 1, 1, 0, 1],
    )

    assert bridge.schedule_send.call_count == 2
    bodies = [c.args[0] for c in bridge.schedule_send.call_args_list]
    assert all(b.get("event") == "PERSON_DETECTED" for b in bodies)


@pytest.mark.parametrize(
    "sequence,expected_edges",
    [
        ([1], 1),
        ([0, 1], 1),
        ([1, 1], 1),
        ([0, 1, 0, 1], 2),
    ],
)
def test_scenario_edge_count_param(
    sequence: list[int],
    expected_edges: int,
) -> None:
    """시퀀스에 따른 person_detected 상승 엣지 횟수(첫 샘이 1이면 0→1 로 간주)."""
    _handlers, _monitor, controller, bridge = _run_poll_scenario(
        sequence,
    )

    assert controller.speaker_calls.count(True) == expected_edges
    assert bridge.schedule_send.call_count == expected_edges
