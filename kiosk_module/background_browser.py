"""
백그라운드 브라우저 세션: URL을 별도 프로세스로 띄우고 일정 시간 후 종료.

macOS·Windows 모두 ``subprocess.Popen`` + 프로세스 그룹으로 묶어 타임아웃 시 정리합니다.
``taskkill``은 Windows에 기본 포함됩니다.
"""

from __future__ import annotations

import logging
import os
import shlex
import signal
import subprocess
import sys
import threading
from shutil import which

logger = logging.getLogger(__name__)

_SESSION_LOCK = threading.Lock()
# session_key -> (Popen | None, threading.Timer | None)
_sessions: dict[str, tuple[subprocess.Popen | None, threading.Timer | None]] = {}


def _background_browser_flags() -> list[str]:
    """포커스를 빼앗지 않도록 최소화·부가 플래그."""
    return [
        "--new-window",
        "--start-minimized",
        "--no-first-run",
        "--disable-session-crashed-bubble",
    ]


def _apply_background_flags(argv: list[str]) -> list[str]:
    """명시 브라우저 명령에도 백그라운드 플래그를 보강."""
    if not argv:
        return argv
    merged = [argv[0]]
    for flag in _background_browser_flags():
        if flag not in argv:
            merged.append(flag)
    merged.extend(argv[1:])
    return merged


def _default_browser_argv(url: str, *, background: bool) -> list[str] | None:
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
                    return [p, *_background_browser_flags(), url]
                return [p, "--new-window", url]
        return None
    for name in ("google-chrome", "chromium", "chromium-browser"):
        path = which(name)
        if path:
            if background:
                return [path, *_background_browser_flags(), url]
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
            argv = _apply_background_flags(argv)
    else:
        argv = _default_browser_argv(url, background=background)

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
