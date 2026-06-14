from __future__ import annotations

import ctypes
import sys
import threading
import time
from ctypes import wintypes


if sys.platform != "win32":
    raise SystemExit("DragBlocker only runs on Windows.")


try:
    from PIL import Image, ImageDraw
    import pystray
except Exception:  # pragma: no cover - fallback is only for partial installs.
    Image = None
    ImageDraw = None
    pystray = None


WH_MOUSE_LL = 14
HC_ACTION = 0
WM_MOUSEMOVE = 0x0200
WM_MOUSEWHEEL = 0x020A
WM_MOUSEHWHEEL = 0x020E
WM_QUIT = 0x0012

VK_LBUTTON = 0x01
VK_RBUTTON = 0x02
VK_MBUTTON = 0x04
VK_XBUTTON1 = 0x05
VK_XBUTTON2 = 0x06

FORMAT_MESSAGE_FROM_SYSTEM = 0x00001000
FORMAT_MESSAGE_IGNORE_INSERTS = 0x00000200

LRESULT = ctypes.c_ssize_t
LowLevelMouseProc = ctypes.WINFUNCTYPE(
    LRESULT,
    ctypes.c_int,
    wintypes.WPARAM,
    wintypes.LPARAM,
)

user32 = ctypes.WinDLL("user32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)


class POINT(ctypes.Structure):
    _fields_ = [
        ("x", wintypes.LONG),
        ("y", wintypes.LONG),
    ]


class MSG(ctypes.Structure):
    _fields_ = [
        ("hwnd", wintypes.HWND),
        ("message", wintypes.UINT),
        ("wParam", wintypes.WPARAM),
        ("lParam", wintypes.LPARAM),
        ("time", wintypes.DWORD),
        ("pt", POINT),
    ]


user32.SetWindowsHookExW.argtypes = [
    ctypes.c_int,
    LowLevelMouseProc,
    wintypes.HINSTANCE,
    wintypes.DWORD,
]
user32.SetWindowsHookExW.restype = wintypes.HANDLE
user32.CallNextHookEx.argtypes = [
    wintypes.HANDLE,
    ctypes.c_int,
    wintypes.WPARAM,
    wintypes.LPARAM,
]
user32.CallNextHookEx.restype = LRESULT
user32.UnhookWindowsHookEx.argtypes = [wintypes.HANDLE]
user32.UnhookWindowsHookEx.restype = wintypes.BOOL
user32.GetMessageW.argtypes = [
    ctypes.POINTER(MSG),
    wintypes.HWND,
    wintypes.UINT,
    wintypes.UINT,
]
user32.GetMessageW.restype = ctypes.c_int
user32.TranslateMessage.argtypes = [ctypes.POINTER(MSG)]
user32.TranslateMessage.restype = wintypes.BOOL
user32.DispatchMessageW.argtypes = [ctypes.POINTER(MSG)]
user32.DispatchMessageW.restype = LRESULT
user32.GetAsyncKeyState.argtypes = [ctypes.c_int]
user32.GetAsyncKeyState.restype = wintypes.SHORT
user32.PostThreadMessageW.argtypes = [
    wintypes.DWORD,
    wintypes.UINT,
    wintypes.WPARAM,
    wintypes.LPARAM,
]
user32.PostThreadMessageW.restype = wintypes.BOOL
user32.MessageBoxW.argtypes = [
    wintypes.HWND,
    wintypes.LPCWSTR,
    wintypes.LPCWSTR,
    wintypes.UINT,
]
user32.MessageBoxW.restype = ctypes.c_int

kernel32.GetCurrentThreadId.argtypes = []
kernel32.GetCurrentThreadId.restype = wintypes.DWORD
kernel32.GetModuleHandleW.argtypes = [wintypes.LPCWSTR]
kernel32.GetModuleHandleW.restype = wintypes.HMODULE
kernel32.FormatMessageW.argtypes = [
    wintypes.DWORD,
    wintypes.LPCVOID,
    wintypes.DWORD,
    wintypes.DWORD,
    wintypes.LPWSTR,
    wintypes.DWORD,
    wintypes.LPVOID,
]
kernel32.FormatMessageW.restype = wintypes.DWORD


enabled = threading.Event()
stop_requested = threading.Event()
hook_ready = threading.Event()
enabled.set()

hook_handle: wintypes.HANDLE | None = None
hook_thread_id = 0
hook_error: str | None = None


def format_windows_error(code: int) -> str:
    buffer = ctypes.create_unicode_buffer(512)
    length = kernel32.FormatMessageW(
        FORMAT_MESSAGE_FROM_SYSTEM | FORMAT_MESSAGE_IGNORE_INSERTS,
        None,
        code,
        0,
        buffer,
        len(buffer),
        None,
    )
    if length:
        return buffer.value.strip()
    return f"Windows error {code}"


def any_mouse_button_pressed() -> bool:
    for key in (VK_LBUTTON, VK_RBUTTON, VK_MBUTTON, VK_XBUTTON1, VK_XBUTTON2):
        if user32.GetAsyncKeyState(key) & 0x8000:
            return True
    return False


@LowLevelMouseProc
def mouse_hook_proc(n_code: int, w_param: int, l_param: int) -> int:
    if n_code == HC_ACTION and enabled.is_set():
        message = int(w_param)
        if message in (WM_MOUSEWHEEL, WM_MOUSEHWHEEL):
            return 1
        if message == WM_MOUSEMOVE and any_mouse_button_pressed():
            return 1
    return user32.CallNextHookEx(hook_handle, n_code, w_param, l_param)


def hook_loop() -> None:
    global hook_handle, hook_thread_id, hook_error

    hook_thread_id = kernel32.GetCurrentThreadId()
    module_handle = kernel32.GetModuleHandleW(None)
    hook_handle = user32.SetWindowsHookExW(
        WH_MOUSE_LL,
        mouse_hook_proc,
        module_handle,
        0,
    )

    if not hook_handle:
        hook_error = format_windows_error(ctypes.get_last_error())
        hook_ready.set()
        return

    hook_ready.set()
    msg = MSG()

    try:
        while not stop_requested.is_set():
            result = user32.GetMessageW(ctypes.byref(msg), None, 0, 0)
            if result == 0:
                break
            if result == -1:
                hook_error = format_windows_error(ctypes.get_last_error())
                break
            user32.TranslateMessage(ctypes.byref(msg))
            user32.DispatchMessageW(ctypes.byref(msg))
    finally:
        if hook_handle:
            user32.UnhookWindowsHookEx(hook_handle)
            hook_handle = None


def start_hook() -> threading.Thread:
    thread = threading.Thread(target=hook_loop, name="DragBlockerHook", daemon=True)
    thread.start()
    hook_ready.wait(timeout=5)
    if hook_error:
        raise RuntimeError(hook_error)
    return thread


def stop_hook() -> None:
    stop_requested.set()
    if hook_thread_id:
        user32.PostThreadMessageW(hook_thread_id, WM_QUIT, 0, 0)


def build_icon_image(is_enabled: bool) -> Image.Image:
    image = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    fill = (31, 132, 72, 255) if is_enabled else (119, 119, 119, 255)
    draw.rounded_rectangle((6, 6, 58, 58), radius=12, fill=fill)
    draw.line((18, 32, 44, 32), fill=(255, 255, 255, 255), width=5)
    draw.polygon([(40, 22), (52, 32), (40, 42)], fill=(255, 255, 255, 255))
    draw.line((17, 17, 47, 47), fill=(220, 38, 38, 255), width=6)
    return image


def set_tray_state(icon: pystray.Icon) -> None:
    is_enabled = enabled.is_set()
    icon.icon = build_icon_image(is_enabled)
    icon.title = "DragBlocker - ON" if is_enabled else "DragBlocker - OFF"
    icon.update_menu()


def toggle_drag_blocking(icon: pystray.Icon, item: object) -> None:
    if enabled.is_set():
        enabled.clear()
    else:
        enabled.set()
    set_tray_state(icon)


def quit_app(icon: pystray.Icon, item: object) -> None:
    stop_hook()
    icon.stop()


def run_with_tray() -> None:
    menu = pystray.Menu(
        pystray.MenuItem(
            lambda item: "Drag blocking: ON" if enabled.is_set() else "Drag blocking: OFF",
            toggle_drag_blocking,
            checked=lambda item: enabled.is_set(),
        ),
        pystray.MenuItem("Exit", quit_app),
    )
    icon = pystray.Icon(
        "DragBlocker",
        build_icon_image(True),
        "DragBlocker - ON",
        menu,
    )
    icon.run()


def run_console_fallback() -> None:
    print("DragBlocker is running. Press Ctrl+C to exit.")
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        stop_hook()


def main() -> int:
    try:
        start_hook()
    except RuntimeError as exc:
        user32.MessageBoxW(None, str(exc), "DragBlocker failed to start", 0x10)
        return 1

    try:
        if pystray and Image and ImageDraw:
            run_with_tray()
        else:
            run_console_fallback()
    finally:
        stop_hook()

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
