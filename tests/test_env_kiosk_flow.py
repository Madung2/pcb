from __future__ import annotations

import asyncio
import os
import subprocess
import time
from pathlib import Path
import sys
import types
from unittest.mock import MagicMock


try:
    import serial  # noqa: F401
except ModuleNotFoundError:
    serial_mod = types.ModuleType("serial")
    serial_mod.EIGHTBITS = 8
    serial_mod.STOPBITS_ONE = 1
    serial_mod.PARITY_NONE = "N"
    serial_mod.SerialException = Exception
    serial_mod.Serial = MagicMock()

    tools_mod = types.ModuleType("serial.tools")
    list_ports_mod = types.ModuleType("serial.tools.list_ports")
    list_ports_mod.comports = lambda: []
    common_mod = types.ModuleType("serial.tools.list_ports_common")
    common_mod.ListPortInfo = object
    tools_mod.list_ports = list_ports_mod

    sys.modules["serial"] = serial_mod
    sys.modules["serial.tools"] = tools_mod
    sys.modules["serial.tools.list_ports"] = list_ports_mod
    sys.modules["serial.tools.list_ports_common"] = common_mod

import main as main_mod
import kiosk_module.kiosk_events as kiosk_events_mod
import kiosk_module.env_utils as env_utils_mod
import kiosk_module.background_browser as browser_mod
from kiosk_module.kiosk_events import KioskMonitorHandlers, SESSION_MEET_WEB
from kiosk_module.protocol import ButtonPressEvent


ROOT = Path(__file__).resolve().parents[1]
ENV_KIOSK = ROOT / ".env_kiosk"
WEBVIEW_STARTUP_SECONDS = 5.0


def _read_env_file(path: Path = ENV_KIOSK) -> dict[str, str]:
    values: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, value = stripped.split("=", 1)
        values[key.strip()] = value.strip()
    return values


def _env_bool(value: str) -> bool:
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _apply_env_to_main_config(monkeypatch, env: dict[str, str]) -> None:
    for key, value in env.items():
        monkeypatch.setenv(key, value)

    cfg = main_mod.config
    monkeypatch.setattr(cfg, "asset_device_type", env["ASSET_DEVICE_TYPE"])
    monkeypatch.setattr(cfg, "device_id", env["DEVICE_ID"])
    monkeypatch.setattr(cfg, "base_url", env["BASE_URL"])
    monkeypatch.setattr(cfg, "default_url", env["DEFAULT_URL"])
    monkeypatch.setattr(cfg, "websocket_addr", env["WEBSOCKET_ADDR"])
    monkeypatch.setattr(cfg, "serial_port", env["SERIAL_PORT"])
    monkeypatch.setattr(cfg, "serial_baudrate", int(env["SERIAL_BAUDRATE"]))
    monkeypatch.setattr(
        cfg,
        "serial_port_description_keyword",
        env["SERIAL_PORT_DESCRIPTION_KEYWORD"],
    )
    monkeypatch.setattr(
        cfg,
        "volume_serial_enabled",
        _env_bool(env["VOLUME_SERIAL_ENABLED"]),
    )
    monkeypatch.setattr(cfg, "volume_serial_port", env["VOLUME_SERIAL_PORT"])
    monkeypatch.setattr(cfg, "volume_serial_baudrate", int(env["VOLUME_BAUDRATE"]))
    monkeypatch.setattr(cfg, "webview_enabled", _env_bool(env["WEBVIEW_ENABLED"]))
    monkeypatch.setattr(
        cfg,
        "background_browser_timeout_seconds",
        float(env["BACKGROUND_BROWSER_TIMEOUT_SECONDS"]),
    )
    monkeypatch.setattr(cfg, "kiosk_browser_cmd", env.get("KIOSK_BROWSER_CMD", ""))


class _DummyController:
    def open_door(self) -> None:
        return None

    def close_door(self) -> None:
        return None

    def set_speaker(self, _on: bool) -> None:
        return None


class _FakeInputTracker:
    def seconds_since_activity(self) -> float:
        return 0.0


def _launch_real_kiosk_webview(url: str) -> subprocess.Popen:
    """테스트용 실제 키오스크 WebView를 별도 프로세스로 띄운다.

    pywebview는 GUI 메인 루프를 잡기 때문에 pytest 프로세스 안에서 직접 호출하지 않고,
    별도 Python 프로세스에서 실제 `kiosk_display()`를 실행한다.
    """
    code = (
        "from kiosk_module.config import config\n"
        "from kiosk_module.device_display import kiosk_display\n"
        "config.log_level = 'INFO'\n"
        f"kiosk_display({url!r}, fullscreen=True)\n"
    )
    env = os.environ.copy()
    pythonpath = env.get("PYTHONPATH", "")
    env["PYTHONPATH"] = (
        str(ROOT) if not pythonpath else f"{str(ROOT)}{os.pathsep}{pythonpath}"
    )
    return subprocess.Popen(
        [sys.executable, "-c", code],
        cwd=str(ROOT),
        env=env,
        stdin=subprocess.DEVNULL,
    )


def test_env_kiosk_starts_kiosk_screen_and_run_kiosk_worker(monkeypatch) -> None:
    env = _read_env_file()
    assert env["ASSET_DEVICE_TYPE"] == "KIOSK"
    assert env["SERIAL_PORT"] == "COM4"
    assert env["VOLUME_SERIAL_ENABLED"] == "true"
    assert env["VOLUME_SERIAL_PORT"] == "COM3"

    _apply_env_to_main_config(monkeypatch, env)

    calls: dict[str, object] = {}

    async def fake_run_kiosk(serial_port: str, serial_baudrate: int) -> None:
        calls["run_kiosk"] = (serial_port, serial_baudrate)

    def fake_kiosk_display(url: str) -> int:
        calls["kiosk_display_url"] = url
        return 0

    class FakeThread:
        def __init__(self, *, target, name: str, daemon: bool) -> None:
            self.target = target
            self.name = name
            self.daemon = daemon

        def start(self) -> None:
            calls.setdefault("thread_names", []).append(self.name)
            if self.name == "kiosk-events-worker":
                self.target()

    monkeypatch.setattr(
        main_mod,
        "resolve_device_urls",
        lambda device_id: env["BASE_URL"],
    )
    monkeypatch.setattr(
        main_mod,
        "start_device_websocket_thread",
        lambda device_id: calls.setdefault("ws_device_id", device_id),
    )
    monkeypatch.setattr(main_mod, "download_web_resources", lambda *_a, **_k: True)
    monkeypatch.setattr(main_mod, "probe_url", lambda _url: True)
    monkeypatch.setattr(main_mod, "resolve_serial_port", lambda: env["SERIAL_PORT"])
    monkeypatch.setattr(main_mod, "run_kiosk", fake_run_kiosk)
    monkeypatch.setattr(main_mod, "kiosk_display", fake_kiosk_display)
    monkeypatch.setattr(main_mod.threading, "Thread", FakeThread)

    result = main_mod.fun_start()

    expected_url = f"{env['BASE_URL'].rstrip('/')}/?device_id={env['DEVICE_ID']}"
    assert result == 0
    assert calls["ws_device_id"] == env["DEVICE_ID"]
    assert calls["kiosk_display_url"] == expected_url
    assert calls["run_kiosk"] == (env["SERIAL_PORT"], int(env["SERIAL_BAUDRATE"]))
    assert "kiosk-events-worker" in calls["thread_names"]


def test_env_kiosk_real_base_url_opens_real_meetone_chromium(monkeypatch) -> None:
    env = _read_env_file()
    assert env["ASSET_DEVICE_TYPE"] == "KIOSK"
    assert env["WEBVIEW_ENABLED"] == "false"

    _apply_env_to_main_config(monkeypatch, env)

    base_url = env_utils_mod.resolve_device_urls(env["DEVICE_ID"])
    assert base_url
    kiosk_url = main_mod.build_device_url(base_url, env["DEVICE_ID"])

    meet_url = (kiosk_events_mod.config.meet_web_url or "").strip()
    assert meet_url, "DEVICE_API_BASE_URL 응답에 meet_url 이 있어야 MeetOne을 띄울 수 있습니다."

    webview_proc = _launch_real_kiosk_webview(kiosk_url)
    try:
        time.sleep(WEBVIEW_STARTUP_SECONDS)
        assert webview_proc.poll() is None, "키오스크 WebView 프로세스가 바로 종료되었습니다."

        handlers = KioskMonitorHandlers(
            _DummyController(),
            monitor=object(),
            input_tracker=_FakeInputTracker(),
            webview_controller=None,
        )

        handlers.on_button_pressed(
            ButtonPressEvent(
                left_pressed=False,
                right_pressed=True,
                left_just_pressed=False,
                right_just_pressed=True,
            )
        )

        deadline = time.monotonic() + 5.0
        while time.monotonic() < deadline:
            with browser_mod._SESSION_LOCK:
                if SESSION_MEET_WEB in browser_mod._sessions:
                    break
            time.sleep(0.1)

        with browser_mod._SESSION_LOCK:
            session = browser_mod._sessions.get(SESSION_MEET_WEB)

        assert session is not None, "MeetOne 브라우저 세션이 생성되지 않았습니다."
        proc, timer = session
        assert proc is not None
        assert proc.poll() is None
        assert timer is not None

        # 눈으로 확인할 시간을 준다: 키오스크 WebView가 떠 있고, MeetOne Chrome도 함께 떠 있어야 한다.
        time.sleep(10.0)
    finally:
        browser_mod.shutdown_background_browser(SESSION_MEET_WEB)
        if webview_proc.poll() is None:
            webview_proc.terminate()
            try:
                webview_proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                webview_proc.kill()
                webview_proc.wait(timeout=5)
