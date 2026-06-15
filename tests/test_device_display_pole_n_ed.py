from __future__ import annotations

import sys
from types import SimpleNamespace

import kiosk_module.device_display as display_mod
from kiosk_module.config import config


def test_pole_n_ed_second_display_uses_config_size_and_offset(monkeypatch) -> None:
    calls: list[dict] = []

    class FakeWebView:
        screens = [
            SimpleNamespace(x=0, y=0),
            SimpleNamespace(x=100, y=200),
        ]

        @staticmethod
        def create_window(**kwargs):
            calls.append(kwargs)
            return SimpleNamespace(handle=1000 + len(calls))

        @staticmethod
        def start(*_args, **_kwargs):
            return None

    monkeypatch.setitem(sys.modules, "webview", FakeWebView)
    monkeypatch.setattr(config, "base_url", "https://hcg.jdone.co.kr")
    monkeypatch.setattr(config, "led_url", "led")
    monkeypatch.setattr(config, "pole_n_ed_second_width", 330)
    monkeypatch.setattr(config, "pole_n_ed_second_height", 160)
    monkeypatch.setattr(config, "pole_n_ed_second_x_offset", -3)
    monkeypatch.setattr(config, "pole_n_ed_second_y_offset", -3)

    result = display_mod.pole_n_ed_display(
        "https://hcg.jdone.co.kr/player?device_id=device-1"
    )

    assert result == 0
    assert len(calls) == 2
    second = calls[1]
    assert second["width"] == 330
    assert second["height"] == 160
    assert second["x"] == 97
    assert second["y"] == 197
    assert second["frameless"] is True
    assert second["fullscreen"] is False
    assert second["resizable"] is False
