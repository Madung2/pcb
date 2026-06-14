"""사람 없음 + 입력(키보드/마우스) 유휴 시 자동 도어 닫기 검증.

실제 동작 요약 (``kiosk_events.KioskMonitorHandlers``):

- **있음:** ``on_status_received`` 에서 ``person_detected`` 가 꺼진 폴링마다
  ``_maybe_close_door_on_vacancy_idle`` 이 호출됨.
- **조건:** ``seconds_since_activity() >= vacant_idle_close_seconds`` (설정 기본 20초,
  ``VACANT_IDLE_CLOSE_SECONDS``) 이고, 아직 한 번도 이 '공석 유휴'로 닫지 않았을 때
  (``_vacancy_idle_closed`` 가 False).
- **유휴 시간:** ``InputActivityTracker`` 가 pynput으로 잡는 **PC 키보드/마우스** 기준.
  추적이 꺼지면 ``seconds_since_activity`` 는 항상 0으로 취급되어 이 자동 닫기는
  사실상 동작하지 않음 (``input_activity.py`` 주석과 동일).
- **PCB 버튼 클릭** 은 이 트래커를 갱신하지 않으므로, '클릭'이 전부 PCB 쪽이면
  유휴 타이머와는 별개로 동작함.

"""

from __future__ import annotations

from unittest.mock import patch

import pytest

import kiosk_module.kiosk_events as kiosk_events_mod
from kiosk_module.kiosk_events import KioskMonitorHandlers
from kiosk_module.protocol import StatusResponse


def _status(*, person: int) -> StatusResponse:
    return StatusResponse(
        ac_light_status1=0,
        ac_light_status2=0,
        dc_light_status1=0,
        dc_light_status2=0,
        dc_light_brightness1=0,
        dc_light_brightness2=0,
        door_status=0,
        speaker_status=0,
        person_detected=person,
        button_left_status=0,
        button_right_status=0,
    )


class _TrackingController:
    def __init__(self) -> None:
        self.close_door_calls = 0
        self.speaker_off_calls = 0

    def close_door(self) -> None:
        self.close_door_calls += 1

    def set_speaker(self, on: bool) -> None:
        if not on:
            self.speaker_off_calls += 1

    def open_door(self) -> None:
        pass


class _MutableIdleTracker:
    def __init__(self, idle_sec: float = 0.0) -> None:
        self.idle_sec = idle_sec

    def seconds_since_activity(self) -> float:
        return self.idle_sec


@pytest.fixture
def threshold_sec() -> float:
    return 20.0


def test_person_present_never_triggers_vacancy_close(threshold_sec: float) -> None:
    ctrl = _TrackingController()
    tracker = _MutableIdleTracker(idle_sec=999.0)
    h = KioskMonitorHandlers(ctrl, monitor=object(), input_tracker=tracker)
    with patch.object(kiosk_events_mod.config, "vacant_idle_close_seconds", threshold_sec):
        h.on_status_received(_status(person=1))
    assert ctrl.close_door_calls == 0


def test_vacant_but_idle_under_threshold_no_close(threshold_sec: float) -> None:
    ctrl = _TrackingController()
    tracker = _MutableIdleTracker(idle_sec=threshold_sec - 0.5)
    h = KioskMonitorHandlers(ctrl, monitor=object(), input_tracker=tracker)
    with patch.object(kiosk_events_mod.config, "vacant_idle_close_seconds", threshold_sec):
        h.on_status_received(_status(person=0))
    assert ctrl.close_door_calls == 0


def test_vacant_and_idle_at_threshold_closes_once(threshold_sec: float) -> None:
    ctrl = _TrackingController()
    tracker = _MutableIdleTracker(idle_sec=threshold_sec)
    h = KioskMonitorHandlers(ctrl, monitor=object(), input_tracker=tracker)
    with (
        patch.object(kiosk_events_mod.config, "vacant_idle_close_seconds", threshold_sec),
        patch.object(
            kiosk_events_mod,
            "shutdown_background_browser",
            return_value=True,
        ) as mock_shutdown,
    ):
        h.on_status_received(_status(person=0))
        h.on_status_received(_status(person=0))
    assert ctrl.close_door_calls == 1
    assert ctrl.speaker_off_calls == 1
    mock_shutdown.assert_called_with(kiosk_events_mod.SESSION_MEET_WEB)


def test_person_returns_resets_so_next_vacancy_can_close_again(
    threshold_sec: float,
) -> None:
    ctrl = _TrackingController()
    tracker = _MutableIdleTracker(idle_sec=threshold_sec)
    h = KioskMonitorHandlers(ctrl, monitor=object(), input_tracker=tracker)
    with patch.object(kiosk_events_mod.config, "vacant_idle_close_seconds", threshold_sec):
        h.on_status_received(_status(person=0))
        assert ctrl.close_door_calls == 1
        h.on_status_received(_status(person=1))
        h.on_status_received(_status(person=0))
        assert ctrl.close_door_calls == 2
