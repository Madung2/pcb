from __future__ import annotations

from pathlib import Path

import kiosk_module.background_browser as browser_mod


def test_windows_background_chrome_uses_dedicated_profile(monkeypatch, tmp_path) -> None:
    chrome = tmp_path / "chrome.exe"
    chrome.write_text("", encoding="utf-8")

    monkeypatch.setattr(browser_mod.sys, "platform", "win32")
    monkeypatch.setattr(browser_mod.os.path, "isfile", lambda p: Path(p) == chrome)
    monkeypatch.setattr(
        browser_mod.os,
        "path",
        browser_mod.os.path,
    )
    monkeypatch.setattr(
        browser_mod.os.path,
        "expandvars",
        lambda _value: str(chrome),
    )

    argv = browser_mod._default_browser_argv(
        "https://meet.example",
        background=True,
        session_key="meet_web",
    )

    assert argv is not None
    assert "--start-minimized" in argv
    assert any(arg.startswith("--user-data-dir=") for arg in argv)
    assert argv[-1] == "https://meet.example"


def test_windows_custom_background_command_adds_profile(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(browser_mod.sys, "platform", "win32")
    monkeypatch.setattr(browser_mod.tempfile, "gettempdir", lambda: str(tmp_path))

    argv = browser_mod._apply_background_flags(
        [r"C:\Chrome\chrome.exe", "https://meet.example"],
        "meet_web",
    )

    assert "--start-minimized" in argv
    assert any(arg.startswith("--user-data-dir=") for arg in argv)
    assert argv[-1] == "https://meet.example"


def test_windows_background_window_guard_minimizes_until_process_exits(monkeypatch) -> None:
    calls: list[int] = []
    sleeps: list[float] = []

    class DummyProc:
        pid = 1234

        def __init__(self) -> None:
            self.poll_count = 0

        def poll(self):
            self.poll_count += 1
            return None if self.poll_count < 3 else 0

    def fake_minimize(pid: int) -> int:
        calls.append(pid)
        return 1

    class ImmediateThread:
        def __init__(self, *, target, name: str, daemon: bool) -> None:
            self.target = target
            self.name = name
            self.daemon = daemon

        def start(self) -> None:
            self.target()

    monkeypatch.setattr(browser_mod.sys, "platform", "win32")
    monkeypatch.setattr(browser_mod, "_minimize_windows_for_pid", fake_minimize)
    monkeypatch.setattr(browser_mod.time, "sleep", lambda seconds: sleeps.append(seconds))
    monkeypatch.setattr(browser_mod.threading, "Thread", ImmediateThread)

    browser_mod._start_background_window_guard(DummyProc())

    assert calls == [1234, 1234]
    assert sleeps == [0.25, 0.25]
