"""OS 부팅 시 GUI 자동 실행 등록/해제."""

from __future__ import annotations

import logging
import sys
from pathlib import Path

from ._paths import is_frozen

logger = logging.getLogger(__name__)

APP_NAME = "JDoneKiosk"
LINUX_DESKTOP_NAME = "jdone-kiosk.desktop"


def _gui_main_script() -> Path:
    return Path(__file__).resolve().parent.parent / "gui_main.py"


def autostart_launch_command() -> str:
    """자동 실행에 등록할 명령 문자열."""
    exe = Path(sys.executable).resolve()
    if is_frozen():
        return f'"{exe}"'
    script = _gui_main_script().resolve()
    return f'"{exe}" "{script}"'


def _linux_desktop_path() -> Path:
    return Path.home() / ".config" / "autostart" / LINUX_DESKTOP_NAME


def _linux_desktop_content() -> str:
    cmd = autostart_launch_command()
    return (
        "[Desktop Entry]\n"
        "Type=Application\n"
        f"Name={APP_NAME}\n"
        "Comment=JDONE Kiosk Controller\n"
        f"Exec={cmd}\n"
        "Terminal=false\n"
        "X-GNOME-Autostart-enabled=true\n"
    )


def is_autostart_enabled() -> bool:
    """현재 OS에 자동 실행이 등록되어 있는지."""
    if sys.platform == "win32":
        return _windows_is_enabled()
    if sys.platform.startswith("linux"):
        return _linux_desktop_path().is_file()
    return False


def set_autostart_enabled(enabled: bool) -> tuple[bool, str]:
    """자동 실행 등록/해제. (성공 여부, 사용자 메시지)"""
    if sys.platform == "win32":
        return _windows_set_enabled(enabled)
    if sys.platform.startswith("linux"):
        return _linux_set_enabled(enabled)
    action = "등록" if enabled else "해제"
    return False, f"이 OS({sys.platform})에서는 부팅 자동 실행을 지원하지 않습니다."


def can_manage_autostart() -> bool:
    return sys.platform == "win32" or sys.platform.startswith("linux")


# ── Windows ──────────────────────────────────────────


def _windows_reg_path() -> str:
    return r"Software\Microsoft\Windows\CurrentVersion\Run"


def _windows_is_enabled() -> bool:
    try:
        import winreg
    except ImportError:
        return False
    try:
        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER, _windows_reg_path(), 0, winreg.KEY_READ
        ) as key:
            winreg.QueryValueEx(key, APP_NAME)
            return True
    except OSError:
        return False


def _windows_set_enabled(enabled: bool) -> tuple[bool, str]:
    try:
        import winreg
    except ImportError:
        return False, "Windows 레지스트리 모듈을 사용할 수 없습니다."

    try:
        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            _windows_reg_path(),
            0,
            winreg.KEY_SET_VALUE,
        ) as key:
            if enabled:
                winreg.SetValueEx(
                    key, APP_NAME, 0, winreg.REG_SZ, autostart_launch_command()
                )
                return True, "Windows 로그인 시 자동 실행이 등록되었습니다."
            try:
                winreg.DeleteValue(key, APP_NAME)
            except OSError:
                pass
            return True, "Windows 자동 실행 등록이 해제되었습니다."
    except OSError as exc:
        logger.exception("Windows 자동 실행 설정 실패")
        return False, f"Windows 자동 실행 설정 실패: {exc}"


# ── Linux (XDG autostart) ────────────────────────────


def _linux_set_enabled(enabled: bool) -> tuple[bool, str]:
    path = _linux_desktop_path()
    if enabled:
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(_linux_desktop_content(), encoding="utf-8")
            return True, f"부팅 시 자동 실행이 등록되었습니다:\n{path}"
        except OSError as exc:
            logger.exception("Linux autostart 등록 실패")
            return False, f"자동 실행 등록 실패: {exc}"
    try:
        if path.is_file():
            path.unlink()
        return True, "부팅 자동 실행 등록이 해제되었습니다."
    except OSError as exc:
        logger.exception("Linux autostart 해제 실패")
        return False, f"자동 실행 해제 실패: {exc}"
