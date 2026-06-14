from __future__ import annotations

import asyncio

from kiosk_module.kiosk_background import _status_poll_loop
from kiosk_module.protocol import StatusResponse


class _FakeMonitor:
    def __init__(self) -> None:
        self.poll_count = 0
        self.last_status = None

    def poll_once(self):
        self.poll_count += 1
        self.last_status = StatusResponse(
            ac_light_status1=0,
            ac_light_status2=0,
            dc_light_status1=0,
            dc_light_status2=0,
            dc_light_brightness1=0,
            dc_light_brightness2=0,
            door_status=0,
            speaker_status=0,
            person_detected=0,
            button_left_status=1,
            button_right_status=0,
        )
        return self.last_status


class _FakeBridge:
    def __init__(self) -> None:
        self.send_count = 0

    async def send_status(self) -> None:
        self.send_count += 1


def test_status_poll_loop_polls_buttons_faster_than_status_report(monkeypatch) -> None:
    monitor = _FakeMonitor()
    bridge = _FakeBridge()
    sleeps: list[float] = []

    async def fake_to_thread(func, *args, **kwargs):
        return func(*args, **kwargs)

    async def fake_sleep(seconds: float):
        sleeps.append(seconds)
        if len(sleeps) >= 3:
            raise asyncio.CancelledError

    class FakeTime:
        def __init__(self) -> None:
            self._values = iter([0.0, 0.1, 0.2])

        def monotonic(self) -> float:
            return next(self._values)

    monkeypatch.setattr("kiosk_module.kiosk_background.asyncio.to_thread", fake_to_thread)
    monkeypatch.setattr("kiosk_module.kiosk_background.asyncio.sleep", fake_sleep)
    monkeypatch.setattr("kiosk_module.kiosk_background.time", FakeTime())

    async def run_loop() -> None:
        await _status_poll_loop(
            monitor,
            bridge,
            status_report_interval_sec=600.0,
            event_poll_interval_sec=0.2,
        )

    try:
        asyncio.run(run_loop())
    except asyncio.CancelledError:
        pass
    else:
        raise AssertionError("poll loop should be cancelled by fake sleep")

    assert monitor.poll_count == 3
    assert bridge.send_count == 1
    assert sleeps == [0.2, 0.2, 0.2]
