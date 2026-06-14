from __future__ import annotations

from kiosk_module.config import config
from kiosk_module.device_websocket import _apply_change_base_url


def test_apply_change_base_url_updates_base_url_only(monkeypatch) -> None:
    env_updates: list[tuple[str, str]] = []
    monkeypatch.setattr(
        "kiosk_module.device_websocket.update_env_file",
        lambda key, value: env_updates.append((key, value)),
    )
    monkeypatch.setattr(config, "base_url", "https://old.example.com")
    monkeypatch.setattr(config, "websocket_addr", "wss://old.example.com/ws/device")
    monkeypatch.setattr(config, "webview_ws_url", "wss://old.example.com/ws/device")

    _apply_change_base_url(
        {
            "type": "change_base_url",
            "base_url": "https://example.com",
        }
    )

    assert config.base_url == "https://example.com"
    assert config.websocket_addr == "wss://old.example.com/ws/device"
    assert config.webview_ws_url == "wss://old.example.com/ws/device"
    assert env_updates == [("BASE_URL", "https://example.com")]


def test_apply_change_base_url_updates_both_fields(monkeypatch) -> None:
    env_updates: list[tuple[str, str]] = []
    monkeypatch.setattr(
        "kiosk_module.device_websocket.update_env_file",
        lambda key, value: env_updates.append((key, value)),
    )
    monkeypatch.setattr(config, "base_url", "https://old.example.com")
    monkeypatch.setattr(config, "websocket_addr", "wss://old.example.com/ws/device")
    monkeypatch.setattr(config, "webview_ws_url", "wss://old.example.com/ws/device")

    _apply_change_base_url(
        {
            "type": "change_base_url",
            "base_url": "https://example.com",
            "websocket_addr": "wss://example.com/ws/device",
        }
    )

    assert config.base_url == "https://example.com"
    assert config.websocket_addr == "wss://example.com/ws/device"
    assert config.webview_ws_url == "wss://example.com/ws/device"
    assert env_updates == [
        ("BASE_URL", "https://example.com"),
        ("WEBSOCKET_ADDR", "wss://example.com/ws/device"),
    ]


def test_apply_change_base_url_websocket_addr_only(monkeypatch) -> None:
    env_updates: list[tuple[str, str]] = []
    monkeypatch.setattr(
        "kiosk_module.device_websocket.update_env_file",
        lambda key, value: env_updates.append((key, value)),
    )
    monkeypatch.setattr(config, "base_url", "https://old.example.com")
    monkeypatch.setattr(config, "websocket_addr", "wss://old.example.com/ws/device")
    monkeypatch.setattr(config, "webview_ws_url", "wss://old.example.com/ws/device")

    _apply_change_base_url(
        {
            "type": "change_base_url",
            "websocket_addr": "wss://example.com/ws/device",
        }
    )

    assert config.base_url == "https://old.example.com"
    assert config.websocket_addr == "wss://example.com/ws/device"
    assert config.webview_ws_url == "wss://example.com/ws/device"
    assert env_updates == [("WEBSOCKET_ADDR", "wss://example.com/ws/device")]
