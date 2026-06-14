"""
백그라운드 브라우저 세션: URL을 별도 프로세스로 띄우고 일정 시간 후 종료.

macOS·Windows 모두 ``subprocess.Popen`` + 프로세스 그룹으로 묶어 타임아웃 시 정리합니다.
``taskkill``은 Windows에 기본 포함됩니다.
"""

from __future__ import annotations

import logging
import os
import re
import shlex
import signal
import subprocess
import sys
import tempfile
import threading
import time
from shutil import which

logger = logging.getLogger(__name__)

_SESSION_LOCK = threading.Lock()
# session_key -> (Popen | None, threading.Timer | None)
_sessions: dict[str, tuple[subprocess.Popen | None, threading.Timer | None]] = {}


def _safe_session_name(session_key: str) -> str:
    safe = re.sub(r"[^A-Za-z0-9_.-]+", "_", session_key or "default").strip("._")
    return safe or "default"


def _background_browser_profile_arg(session_key: str) -> str | None:
    """Windows Chrome이 기존 인스턴스에 위임 후 즉시 종료하지 않도록 전용 프로필을 쓴다."""
    if sys.platform != "win32":
        return None
    profile_dir = os.path.join(
        tempfile.gettempdir(),
        "jdone_kiosk_background_browser",
        _safe_session_name(session_key),
    )
    os.makedirs(profile_dir, exist_ok=True)
    return f"--user-data-dir={profile_dir}"


def _background_browser_flags(session_key: str = "") -> list[str]:
    """포커스를 빼앗지 않도록 최소화·부가 플래그."""
    flags = [
        "--new-window",
        "--start-minimized",
        "--no-first-run",
        "--no-default-browser-check",
        "--disable-session-crashed-bubble",
    ]
    profile_arg = _background_browser_profile_arg(session_key)
    if profile_arg:
        flags.append(profile_arg)
    return flags


def _apply_background_flags(argv: list[str], session_key: str) -> list[str]:
    """명시 브라우저 명령에도 백그라운드 플래그를 보강."""
    if not argv:
        return argv
    merged = [argv[0]]
    has_user_data_dir = any(a.startswith("--user-data-dir=") for a in argv)
    for flag in _background_browser_flags(session_key):
        if flag.startswith("--user-data-dir=") and has_user_data_dir:
            continue
        if flag not in argv:
            merged.append(flag)
    merged.extend(argv[1:])
    return merged


def _default_browser_argv(
    url: str,
    *,
    background: bool,
    session_key: str = "",
) -> list[str] | None:
    if sys.platform == "darwin":
        if background:
            # ``-g``: Finder/앱 전환 없이 백그라운드로 열기
            return ["open", "-g", "-a", "Google Chrome", url]
        chrome = (
            "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
        )
        if os.path.isfile(chrome):
            return [chrome, "--new-window", url]
        return None
    if sys.platform == "win32":
        candidates = [
            os.path.expandvars(r"%ProgramFiles%\Google\Chrome\Application\chrome.exe"),
            os.path.expandvars(
                r"%ProgramFiles(x86)%\Google\Chrome\Application\chrome.exe"
            ),
            os.path.expandvars(
                r"%LocalAppData%\Google\Chrome\Application\chrome.exe"
            ),
        ]
        for p in candidates:
            if p and os.path.isfile(p):
                if background:
                    return [p, *_background_browser_flags(session_key), url]
                return [p, "--new-window", url]
        return None
    for name in ("google-chrome", "chromium", "chromium-browser"):
        path = which(name)
        if path:
            if background:
                return [path, *_background_browser_flags(session_key), url]
            return [path, "--new-window", url]
    return None


def _browser_argv_from_config(cmd_template: str, url: str) -> list[str] | None:
    parts = shlex.split(cmd_template, posix=sys.platform != "win32")
    if not parts:
        return None
    return [p.replace("{url}", url) for p in parts]


def _popen_browser(argv: list[str], *, background: bool) -> subprocess.Popen:
    kwargs: dict = {
        "stdin": subprocess.DEVNULL,
        "stdout": subprocess.DEVNULL,
        "stderr": subprocess.DEVNULL,
    }
    if sys.platform == "win32":
        creationflags = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
        kwargs["creationflags"] = creationflags
        if background:
            # SW_SHOWMINNOACTIVE — 새 창을 활성화하지 않고 최소화 상태로 시작
            startupinfo = subprocess.STARTUPINFO()
            startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
            startupinfo.wShowWindow = 7
            kwargs["startupinfo"] = startupinfo
    else:
        kwargs["start_new_session"] = True
    return subprocess.Popen(argv, **kwargs)


def _minimize_windows_for_pid(pid: int) -> int:
    """Windows에서 특정 PID가 소유한 top-level 창을 최소화한다."""
    if sys.platform != "win32" or not pid:
        return 0
    try:
        import ctypes
        from ctypes import wintypes
    except Exception:
        return 0

    user32 = ctypes.windll.user32
    enum_windows = user32.EnumWindows
    enum_windows_proc = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    get_window_thread_process_id = user32.GetWindowThreadProcessId
    is_window_visible = user32.IsWindowVisible
    show_window = user32.ShowWindow
    target_pid = int(pid)
    minimized = 0

    def _callback(hwnd, _lparam):
        nonlocal minimized
        proc_id = wintypes.DWORD()
        get_window_thread_process_id(hwnd, ctypes.byref(proc_id))
        if proc_id.value == target_pid and is_window_visible(hwnd):
            # SW_MINIMIZE. Chrome이 foreground로 떠도 즉시 작업 표시줄로 내린다.
            show_window(hwnd, 6)
            minimized += 1
        return True

    try:
        enum_windows(enum_windows_proc(_callback), 0)
    except Exception:
        return minimized
    return minimized


def _start_background_window_guard(proc: subprocess.Popen) -> None:
    """Windows Chrome 창이 WebView 위로 올라오지 않게 초기 몇 초 동안 반복 최소화."""
    if sys.platform != "win32":
        return

    def _guard() -> None:
        for _ in range(20):
            if proc.poll() is not None:
                return
            count = _minimize_windows_for_pid(proc.pid)
            if count:
                logger.debug("백그라운드 브라우저 창 최소화: pid=%s windows=%s", proc.pid, count)
            time.sleep(0.25)

    threading.Thread(target=_guard, name="background-browser-window-guard", daemon=True).start()


def _terminate_process_tree(proc: subprocess.Popen) -> None:
    if proc.poll() is not None:
        return
    try:
        if sys.platform == "win32":
            subprocess.run(
                ["taskkill", "/PID", str(proc.pid), "/T", "/F"],
                capture_output=True,
                timeout=15,
                check=False,
            )
        else:
            try:
                os.killpg(proc.pid, signal.SIGTERM)
            except (ProcessLookupError, PermissionError, OSError):
                proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                try:
                    os.killpg(proc.pid, signal.SIGKILL)
                except (ProcessLookupError, PermissionError, OSError):
                    proc.kill()
    except Exception as e:
        logger.debug(f"브라우저 프로세스 종료 중: {e}")


def _kill_session_locked(session_key: str) -> None:
    entry = _sessions.pop(session_key, None)
    if not entry:
        return
    proc, timer = entry
    if timer is not None:
        timer.cancel()
    if proc is not None:
        _terminate_process_tree(proc)


def shutdown_background_browser(session_key: str) -> bool:
    """해당 세션의 브라우저·타이머를 즉시 종료합니다. 세션이 없으면 ``False``."""
    with _SESSION_LOCK:
        if session_key not in _sessions:
            return False
        _kill_session_locked(session_key)
        return True


def shutdown_all_background_browsers() -> None:
    """앱 종료 시 모든 백그라운드 브라우저 세션을 정리합니다."""
    with _SESSION_LOCK:
        keys = list(_sessions.keys())
        for k in keys:
            _kill_session_locked(k)


def launch_background_browser(
    url: str,
    *,
    session_key: str,
    timeout_sec: float,
    browser_cmd_template: str,
) -> None:
    launch_browser_session(
        url,
        session_key=session_key,
        timeout_sec=timeout_sec,
        browser_cmd_template=browser_cmd_template,
        background=True,
    )


def launch_foreground_browser(
    url: str,
    *,
    session_key: str,
    timeout_sec: float,
    browser_cmd_template: str,
) -> None:
    launch_browser_session(
        url,
        session_key=session_key,
        timeout_sec=timeout_sec,
        browser_cmd_template=browser_cmd_template,
        background=False,
    )


def launch_browser_session(
    url: str,
    *,
    session_key: str,
    timeout_sec: float,
    browser_cmd_template: str,
    background: bool,
) -> None:
    """같은 ``session_key``에 이미 프로세스가 있으면 먼저 종료한 뒤 새로 띄웁니다.

    ``url``이 비어 있으면 아무 것도 하지 않습니다.
    ``browser_cmd_template``가 비어 있으면 플랫폼 기본 Chrome(계열) 경로를 시도합니다.
    """
    url = (url or "").strip()
    if not url:
        logger.debug(f"백그라운드 브라우저: URL 비어 있음 — 건너뜀 ({session_key})")
        return

    if browser_cmd_template.strip():
        argv = _browser_argv_from_config(browser_cmd_template.strip(), url)
        if background and argv and sys.platform == "win32":
            argv = _apply_background_flags(argv, session_key)
    else:
        argv = _default_browser_argv(
            url,
            background=background,
            session_key=session_key,
        )

    if not argv:
        logger.warning(
            f"브라우저: 실행 파일을 찾을 수 없습니다 ({session_key}). "
            f"Chrome 설치 경로를 확인하세요."
        )
        return

    def _run() -> None:
        try:
            with _SESSION_LOCK:
                _kill_session_locked(session_key)
            proc = _popen_browser(argv, background=background)
            if background:
                _start_background_window_guard(proc)
        except Exception as e:
            logger.error(f"브라우저 실행 실패 ({session_key}): {e}")
            return

        def _on_timeout() -> None:
            with _SESSION_LOCK:
                cur = _sessions.get(session_key)
                if not cur or cur[0] is not proc:
                    return
                _kill_session_locked(session_key)
            logger.info(
                f"백그라운드 브라우저 타임아웃({timeout_sec:.0f}s) → 종료 ({session_key})"
            )

        timer = threading.Timer(timeout_sec, _on_timeout)
        timer.daemon = True

        with _SESSION_LOCK:
            _sessions[session_key] = (proc, timer)
        timer.start()
        mode = "백그라운드" if background else "전면"
        logger.info(f"{mode} 브라우저 시작 ({session_key}): {url}")

    threading.Thread(target=_run, daemon=True).start()
