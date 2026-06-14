from __future__ import annotations

import logging
import os
import threading
from typing import Callable

import webview
from PIL import Image

from .config import config
from .protocol import StatusResponse
from .webview_services import (
    KioskInfo,
    PcbWorkerThread,
    WebViewWebSocketService,
    append_device_id_query,
    resolve_webview_device_id,
)

logger = logging.getLogger(__name__)


if os.name == "nt":
    import ctypes
    from ctypes import wintypes


    class _RECT(ctypes.Structure):
        _fields_ = [
            ("left", wintypes.LONG),
            ("top", wintypes.LONG),
            ("right", wintypes.LONG),
            ("bottom", wintypes.LONG),
        ]


class CursorWindowGuard:
    """Windows에서 마우스 커서를 WebView 창 안에 묶고 Windows 키로 해제."""

    def __init__(
        self,
        window_getter: Callable[[], object | None],
        logger: logging.Logger,
    ) -> None:
        self._window_getter = window_getter
        self._logger = logger
        self._stop = threading.Event()
        self._released = False
        self._thread: threading.Thread | None = None
        self._keyboard_listener = None

    def start(self) -> None:
        if os.name != "nt" or self._released:
            return
        if not self._start_windows_key_listener():
            return
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._clip_loop,
            name="webview-cursor-guard",
            daemon=True,
        )
        self._thread.start()
        self._logger.info("마우스 커서 WebView 영역 잠금 시작 — Windows 키를 누르면 해제")

    def release(self) -> None:
        self._released = True
        self.stop()
        self._logger.info("Windows 키 감지 → 마우스 커서 WebView 영역 잠금 해제")

    def stop(self) -> None:
        self._stop.set()
        self._clip_cursor(None)
        listener = self._keyboard_listener
        self._keyboard_listener = None
        if listener is not None:
            try:
                listener.stop()
            except Exception:
                self._logger.debug("Windows 키 리스너 종료 중 오류", exc_info=True)

    def _start_windows_key_listener(self) -> bool:
        if self._keyboard_listener is not None:
            return True
        try:
            from pynput import keyboard
        except Exception:
            self._logger.warning(
                "pynput keyboard 사용 불가 — 커서 잠금을 시작하지 않습니다.",
                exc_info=True,
            )
            return False

        win_keys = {
            getattr(keyboard.Key, "cmd", None),
            getattr(keyboard.Key, "cmd_l", None),
            getattr(keyboard.Key, "cmd_r", None),
        }

        def _on_press(key) -> bool | None:
            key_name = (getattr(key, "name", "") or "").lower()
            if key in win_keys or key_name in {
                "cmd",
                "cmd_l",
                "cmd_r",
                "win",
                "win_l",
                "win_r",
            }:
                self.release()
                return False
            return None

        try:
            self._keyboard_listener = keyboard.Listener(on_press=_on_press)
            self._keyboard_listener.start()
            return True
        except Exception:
            self._logger.warning(
                "Windows 키 리스너 시작 실패 — 커서 잠금을 시작하지 않습니다.",
                exc_info=True,
            )
            self._keyboard_listener = None
            return False

    def _clip_loop(self) -> None:
        while not self._stop.is_set() and not self._released:
            self._clip_to_window()
            self._stop.wait(1.0)

    def _clip_to_window(self) -> None:
        window = self._window_getter()
        hwnd = self._window_handle(window)
        if hwnd is None:
            self._logger.debug("WebView window handle 대기 중")
            return
        rect = _RECT()
        try:
            ok = ctypes.windll.user32.GetWindowRect(hwnd, ctypes.byref(rect))
            if not ok:
                self._logger.debug("GetWindowRect 실패 hwnd=%s", hwnd)
                return
            self._clip_cursor(rect)
        except Exception:
            self._logger.debug("커서 영역 잠금 갱신 실패", exc_info=True)

    def _clip_cursor(self, rect: object | None) -> None:
        if os.name != "nt":
            return
        try:
            if rect is None:
                ctypes.windll.user32.ClipCursor(None)
            else:
                ctypes.windll.user32.ClipCursor(ctypes.byref(rect))
        except Exception:
            self._logger.debug("ClipCursor 호출 실패", exc_info=True)

    def _window_handle(self, window: object | None) -> int | None:
        if window is None:
            return None
        candidates = [window, getattr(window, "native", None)]
        for obj in candidates:
            if obj is None:
                continue
            for name in ("Handle", "handle", "HWND", "hwnd"):
                value = getattr(obj, name, None)
                hwnd = self._coerce_handle(value)
                if hwnd:
                    return hwnd
        if os.name == "nt":
            try:
                hwnd = ctypes.windll.user32.FindWindowW(
                    None,
                    "JDONE Kiosk Controller",
                )
                if hwnd:
                    return int(hwnd)
            except Exception:
                return None
        return None

    @staticmethod
    def _coerce_handle(value: object | None) -> int | None:
        if value is None:
            return None
        try:
            to_int64 = getattr(value, "ToInt64", None)
            if callable(to_int64):
                return int(to_int64())
            return int(value)
        except Exception:
            return None

BLACK_SCREEN_HTML = """\
<!doctype html>
<html lang="ko">
  <head>
    <meta charset="utf-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1" />
    <meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src 'unsafe-inline'" />
    <style>
      html, body { height: 100%; margin: 0; background: #000; }
    </style>
    <title>JDONE Kiosk WebView</title>
  </head>
  <body></body>
</html>
"""


# 부팅 초기 표시용 로딩 placeholder.
LOADING_HTML = """\
<!doctype html>
<html lang="ko">
  <head>
    <meta charset="utf-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1" />
    <meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src 'unsafe-inline'" />
    <style>
      html, body { height: 100%; margin: 0; background: #000; color: #ddd;
        font-family: system-ui, -apple-system, "Segoe UI", Roboto, "Malgun Gothic", sans-serif; }
      .c { height: 100%; display: flex; flex-direction: column; align-items: center; justify-content: center; }
      .s { width: 56px; height: 56px; border: 4px solid #2a2a2a; border-top-color: #888;
           border-radius: 50%; animation: r 1s linear infinite; }
      .m { margin-top: 28px; font-size: 22px; opacity: 0.85; letter-spacing: 0.02em; }
      .h { margin-top: 10px; font-size: 13px; opacity: 0.45; }
      @keyframes r { to { transform: rotate(360deg); } }
    </style>
    <title>JDONE Kiosk</title>
  </head>
  <body>
    <div class="c">
      <div class="s"></div>
      <div class="m">네트워크 연결 확인 중</div>
      <div class="h">잠시만 기다려 주세요</div>
    </div>
  </body>
</html>
"""

class TrayIcon:
    """Windows용 트레이 아이콘."""

    def __init__(
        self,
        show_callback: Callable[[], None],
        hide_callback: Callable[[], None],
        quit_callback: Callable[[], None],
    ) -> None:
        self._show_callback = show_callback
        self._hide_callback = hide_callback
        self._quit_callback = quit_callback
        self._icon = None
        self._thread: threading.Thread | None = None
        self.is_visible = True
        self.logger = logging.getLogger("webview.tray")

    def _load_image(self) -> Image.Image:
        icon_path = os.path.join(
            os.path.dirname(os.path.dirname(__file__)),
            "static",
            "favicon.png",
        )
        if os.path.exists(icon_path):
            image = Image.open(icon_path)
            return image.resize((16, 16), Image.Resampling.LANCZOS)
        return Image.new("RGB", (16, 16), color="blue")

    def _create_menu(self):
        import pystray
        from pystray import MenuItem as Item

        items = []
        if self.is_visible:
            items.append(Item("앱 숨기기", self._on_hide, default=True))
        else:
            items.append(Item("앱 보기", self._on_show, default=True))
        items.extend([pystray.Menu.SEPARATOR, Item("앱 종료", self._on_quit)])
        return pystray.Menu(*items)

    def _on_show(self, _icon=None, _item=None) -> None:
        self.is_visible = True
        self._show_callback()

    def _on_hide(self, _icon=None, _item=None) -> None:
        self.is_visible = False
        self._hide_callback()

    def _on_quit(self, _icon=None, _item=None) -> None:
        self._quit_callback()
        self.stop()

    def _on_toggle(self, _icon=None, _item=None) -> None:
        if self.is_visible:
            self._on_hide()
        else:
            self._on_show()

    def update_visibility(self, is_visible: bool) -> None:
        self.is_visible = is_visible
        if self._icon is not None:
            self._icon.menu = self._create_menu()

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return

        def _run() -> None:
            import pystray

            try:
                self._icon = pystray.Icon(
                    "kiosk-pcb-controller",
                    self._load_image(),
                    menu=self._create_menu(),
                )
                self._icon.default_action = self._on_toggle
                self._icon.run()
            except Exception:
                self.logger.exception("트레이 아이콘 실행 실패")

        self._thread = threading.Thread(target=_run, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        if self._icon is not None:
            try:
                self._icon.stop()
            except Exception:
                self.logger.exception("트레이 아이콘 종료 실패")
            self._icon = None


class WebViewUI:
    def __init__(self) -> None:
        self.window = None
        self.current_url: str | None = None
        self.default_screen_url: str | None = None
        self.logger = logging.getLogger("webview.ui")

    def create_window(self) -> None:
        webview.settings["KIOSK_MODE"] = True
        webview.settings["IGNORE_SSL_ERRORS"] = True
        webview.settings["OPEN_DEVTOOLS_IN_DEBUG"] = config.webview_devtools
        self.window = webview.create_window(
            title="JDONE Kiosk Controller",
            html=LOADING_HTML,
            resizable=False,
            fullscreen=True,
            frameless=True,
            shadow=False,
            background_color="#000000",
        )

    def show(self) -> None:
        if self.window is not None:
            self.window.show()

    def hide(self) -> None:
        if self.window is not None:
            self.window.hide()

    def close(self) -> None:
        if self.window is not None:
            self.window.destroy()

    def show_black_screen(self) -> None:
        if self.window is None:
            return
        self.window.load_html(BLACK_SCREEN_HTML)
        self.current_url = None
    def navigate_to(self, url: str) -> None:
        if not url or self.window is None:
            return
        self.window.load_url(url)
        self.current_url = url
        self.logger.info("웹뷰 URL 이동: %s", url)


class IntegratedWebViewApp(WebViewUI):
    def __init__(self, serial_port: str, serial_baudrate: int) -> None:
        super().__init__()
        device_id = resolve_webview_device_id(config)
        self.kiosk_info = KioskInfo(device_id=device_id)
        self._serial_port = serial_port
        self._serial_baudrate = serial_baudrate
        self._shutdown_lock = threading.Lock()
        self._shutdown_started = False
        self.websocket_service: WebViewWebSocketService | None = None
        self.pcb_worker: PcbWorkerThread | None = None
        self.tray_icon: TrayIcon | None = None
        self.logger = logging.getLogger("webview.app")
        self.cursor_guard = CursorWindowGuard(lambda: self.window, self.logger)
        # load_url / load_html 은 webview.start() 이후(메인 창 shown)에만 유효하다.
        self._webview_runtime_ready: bool = False
        self._pending_nav_url: str | None = None

        if config.webview_tray_enabled:
            try:
                self.tray_icon = TrayIcon(
                    show_callback=self.show_app,
                    hide_callback=self.hide_app,
                    quit_callback=self.quit_app,
                )
            except Exception:
                self.logger.exception("트레이 아이콘 초기화 실패")

    def start_services(self) -> None:
        initial_url = (config.base_url or config.default_url or "").strip()
        if initial_url:
            self.default_screen_url = append_device_id_query(
                initial_url,
                self.kiosk_info.device_id,
            )
            self.navigate_to(self.default_screen_url)

        if config.webview_ws_url:
            self.websocket_service = WebViewWebSocketService(self, config)
            self.websocket_service.start()
        else:
            self.logger.info(
                "웹뷰 WebSocket URL 이 비어 있어 전용 WS·PCB 제어를 시작하지 않습니다. "
                "WEBVIEW_WS_URL 을 설정하세요."
            )

        if config.pcb_control_enabled:
            self.pcb_worker = PcbWorkerThread(
                self._serial_port,
                self._serial_baudrate,
                webview_controller=self,
                on_error=self._on_pcb_worker_error,
                pcb_status_broadcast=self._broadcast_pcb_status_to_webview_ws,
            )
            self.pcb_worker.start()
        else:
            self.logger.info(
                "ASSET_DEVICE_TYPE=%s — PCB 제어/시리얼 워커 비활성화.",
                config.asset_device_type,
            )

        if self.tray_icon is not None:
            threading.Timer(1.0, self.tray_icon.start).start()

    def stop_services(self) -> None:
        current = threading.current_thread()

        if self.websocket_service is not None:
            self.websocket_service.stop()
            if current is not self.websocket_service:
                self.websocket_service.join(timeout=5)
            self.websocket_service = None

        if self.pcb_worker is not None:
            self.pcb_worker.request_stop()
            if current is not self.pcb_worker:
                self.pcb_worker.join(timeout=5)
            self.pcb_worker = None

        if self.tray_icon is not None:
            self.tray_icon.stop()

        self.cursor_guard.stop()

    def _broadcast_pcb_status_to_webview_ws(self, status: StatusResponse) -> None:
        ws = self.websocket_service
        if ws is not None:
            ws.send_pcb_status(status)

    def navigate_to(self, url: str) -> None:
        """``webview.start()`` 이전(및 shown 이전)에는 URL 만 대기하고, 런타임 준비 뒤 로드한다."""
        if not url or self.window is None:
            return
        if not self._webview_runtime_ready:
            self._pending_nav_url = url
            self.logger.debug("웹뷰 런타임 이전—URL 은 창이 뜬 뒤 로드: %s", url)
            return
        super().navigate_to(url)

    def _on_webview_runtime_ready(self) -> None:
        """``webview.start`` 가 ``func`` 로 호출. 메인 창 shown 이후에만 ``load_url`` 이 안전하다."""
        w = self.window
        if w is None:
            return
        try:
            if not w.events.shown.wait(60.0):
                self.logger.error("웹뷰 shown 대기(60s) 실패")
                return
        except Exception:
            self.logger.exception("웹뷰 shown 대기 중 오류")
            return
        self._webview_runtime_ready = True
        self.cursor_guard.start()
        pending = self._pending_nav_url
        self._pending_nav_url = None
        if pending:
            super().navigate_to(pending)

    def update_kiosk_info(
        self,
        *,
        name: str,
        latitude: float | None = None,
        longitude: float | None = None,
        screen_url: str | None = None,
    ) -> None:
        self.kiosk_info = KioskInfo(
            device_id=self.kiosk_info.device_id,
            name=name,
            latitude=latitude,
            longitude=longitude,
            screen_url=screen_url,
        )
        if screen_url:
            self.default_screen_url = append_device_id_query(
                screen_url,
                self.kiosk_info.device_id,
            )
            self.navigate_to(self.default_screen_url)

    def show_meet_web(self, url: str | None = None) -> None:
        meet_url = (url or config.meet_web_url or "").strip()
        if not meet_url:
            if self.websocket_service is not None:
                self.websocket_service.request_meet_url()
            self.logger.warning(
                "Meet URL 이 아직 서버에서 수신되지 않아 서버에 get_meet_url 을 요청합니다."
            )
            return
        self.navigate_to(meet_url)

    def restore_default_screen(self) -> None:
        if self.default_screen_url:
            self.navigate_to(self.default_screen_url)
        else:
            self.show_black_screen()

    def show_app(self) -> None:
        self.show()
        if self.tray_icon is not None:
            self.tray_icon.update_visibility(True)

    def hide_app(self) -> None:
        self.hide()
        if self.tray_icon is not None:
            self.tray_icon.update_visibility(False)

    def quit_app(self) -> None:
        self.shutdown()
        try:
            self.close()
        except Exception:
            self.logger.exception("웹뷰 종료 실패")

    def shutdown(self) -> None:
        with self._shutdown_lock:
            if self._shutdown_started:
                return
            self._shutdown_started = True
        self.cursor_guard.stop()
        self.stop_services()

    def _on_pcb_worker_error(self, exc: Exception) -> None:
        self.logger.error("PCB 워커 오류로 앱을 종료합니다: %s", exc)
        threading.Thread(target=self.quit_app, daemon=True).start()

    def run(self) -> int:
        self.logger.info(
            "IntegratedWebViewApp.run() 시작 serial=%r baud=%s device_id=%s",
            self._serial_port,
            self._serial_baudrate,
            self.kiosk_info.device_id,
        )
        self._webview_runtime_ready = False
        self._pending_nav_url = None
        self.create_window()
        self.logger.info("웹뷰 창 생성 완료")
        self.start_services()
        self.logger.info(
            "서비스 시작 완료 ws_url=%r default_screen=%r",
            config.webview_ws_url,
            self.default_screen_url,
        )
        try:
            webview_debug = (
                config.log_level.upper() == "DEBUG" or config.webview_devtools
            )
            # ysoh 2026-06-13: macOS는 cocoa(WebKit), Windows는 edgechromium
            import sys
            if sys.platform == "darwin":
                _gui_backend = "cocoa"
            else:
                _gui_backend = "edgechromium"
            self.logger.info("webview.start() 진입 (gui=%s debug=%s)", _gui_backend, webview_debug)
            webview.start(
                gui=_gui_backend,
                debug=webview_debug,
                private_mode=False,
                no_cache=True,
                func=self._on_webview_runtime_ready,
            )
            self.logger.info("webview.start() 반환 — 사용자가 창을 닫았거나 런타임 종료")
        except Exception:
            self.logger.exception("webview.start() 중 예외")
            raise
        finally:
            self.shutdown()
            self.logger.info("IntegratedWebViewApp.run() 종료")
        return 0


def run_integrated_app(serial_port: str, serial_baudrate: int) -> int:

    # ysoh 2026-06-13: macOS(darwin)도 허용, 그 외(Linux 등)는 차단
    import sys
    if sys.platform not in ("win32", "darwin"):
        raise RuntimeError(
            "WEBVIEW_ENABLED 모드는 Windows 또는 macOS에서만 지원합니다."
        )

    logger.info("run_integrated_app 시작 port=%r baud=%s", serial_port, serial_baudrate)
    app = IntegratedWebViewApp(serial_port, serial_baudrate)
    return app.run()
