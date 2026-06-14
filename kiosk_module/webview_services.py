from __future__ import annotations

import asyncio
import base64
import io
import json
import logging
import os
import threading
import time
import urllib.parse
from dataclasses import dataclass
from typing import TYPE_CHECKING, Callable

import websockets
from PIL import ImageGrab

from .config import Config, WEBVIEW_WS_RECONNECT_DELAY_SECONDS
from .device_controller import Controller, PcbControlInput
from .kiosk_runner import run_kiosk
from .protocol import StatusResponse

if TYPE_CHECKING:
    from .webview_app import IntegratedWebViewApp

logger = logging.getLogger(__name__)


@dataclass
class KioskInfo:
    device_id: str
    name: str = "Unknown Kiosk"
    latitude: float | None = None
    longitude: float | None = None
    screen_url: str | None = None


def resolve_webview_device_id(cfg: Config) -> str:
    return (cfg.device_id or "").strip()


def append_device_id_query(url: str, device_id: str) -> str:
    """URL 에 device_id 쿼리를 추가하거나 기존 값을 교체한다."""
    did = (device_id or "").strip()
    s = (url or "").strip()
    if not did or not s:
        return s
    parsed = urllib.parse.urlparse(s)
    qsl = urllib.parse.parse_qsl(parsed.query, keep_blank_values=True)
    updated = False
    out: list[tuple[str, str]] = []
    for name, value in qsl:
        if name.lower() in ("device_id", "deviceid"):
            out.append((name, did))
            updated = True
        else:
            out.append((name, value))
    if not updated:
        out.append(("device_id", did))
    return urllib.parse.urlunparse(
        parsed._replace(query=urllib.parse.urlencode(out, doseq=True))
    )


def build_webview_ws_url(base_url: str, device_id: str) -> str:
    """웹뷰 WS URL 에 ``device_id=<asset.id>`` 쿼리를 채워 반환한다.

    백엔드 `/ws/kiosk` 는 ``device_id`` 만 검증한다. 이미 쿼리에
    ``device_id`` 가 있으면 값만 교체한다.
    """
    return append_device_id_query(base_url, device_id)


def extract_meet_url(data: dict) -> str:
    payload = data.get("data")
    candidates = [
        data.get("meet_url"),
        data.get("meetUrl"),
        payload.get("meet_url") if isinstance(payload, dict) else None,
        payload.get("meetUrl") if isinstance(payload, dict) else None,
    ]
    for value in candidates:
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


class PcbWorkerThread(threading.Thread):
    """기존 asyncio 기반 PCB 루프를 백그라운드 스레드에서 실행한다."""

    def __init__(
        self,
        serial_port: str,
        serial_baudrate: int,
        *,
        webview_controller: object | None = None,
        on_error: Callable[[Exception], None] | None = None,
        pcb_status_broadcast: Callable[[StatusResponse], None] | None = None,
    ) -> None:
        super().__init__(daemon=True)
        self._serial_port = serial_port
        self._serial_baudrate = serial_baudrate
        self._webview_controller = webview_controller
        self._on_error = on_error
        self._pcb_status_broadcast = pcb_status_broadcast
        self._loop: asyncio.AbstractEventLoop | None = None
        self._stop_event: asyncio.Event | None = None
        # ``run_kiosk`` 가 채워주는 컨트롤러 참조. 백엔드 ``type=control`` 메시지
        # 처리에서 즉시 ``Controller.send_control`` 을 호출하기 위해 보관한다.
        self._controller_ref: dict = {}
        self.error: Exception | None = None

    @property
    def controller(self) -> Controller | None:
        """`run_kiosk` 가 초기화한 후의 PCB 컨트롤러. 시작 직후엔 ``None`` 일 수 있다."""
        ref = self._controller_ref.get("controller")
        return ref if isinstance(ref, Controller) else None

    def apply_pcb_control(self, payload: dict) -> bool:
        """백엔드로부터 받은 ``type=control`` 페이로드를 PCB 에 적용.

        Returns:
            성공 = True. 컨트롤러 미준비 / 검증 실패 / 시리얼 송신 실패 = False.
        """
        ctrl = self.controller
        if ctrl is None:
            logger.warning(
                "PCB 제어 무시: 컨트롤러 미준비 payload_keys=%s",
                list((payload or {}).keys()),
            )
            return False
        try:
            model = PcbControlInput.model_validate(payload or {})
        except Exception:
            logger.exception("PCB 제어 페이로드 검증 실패 payload=%s", payload)
            return False
        try:
            return bool(ctrl.send_control(model))
        except Exception:
            logger.exception("PCB 제어 송신 중 예외 payload=%s", payload)
            return False

    def request_stop(self) -> None:
        if self._loop and self._stop_event and not self._stop_event.is_set():
            self._loop.call_soon_threadsafe(self._stop_event.set)

    def run(self) -> None:
        try:
            self._loop = asyncio.new_event_loop()
            asyncio.set_event_loop(self._loop)
            self._stop_event = asyncio.Event()
            self._loop.run_until_complete(
                run_kiosk(
                    self._serial_port,
                    self._serial_baudrate,
                    stop_event=self._stop_event,
                    controller_ref=self._controller_ref,
                    webview_controller=self._webview_controller,
                    pcb_status_broadcast=self._pcb_status_broadcast,
                )
            )
        except Exception as exc:
            self.error = exc
            logger.exception("PCB 백그라운드 워커 실패")
            if self._on_error is not None:
                try:
                    self._on_error(exc)
                except Exception:
                    logger.exception("PCB 워커 오류 콜백 실패")
        finally:
            if self._loop is not None:
                self._loop.close()
                self._loop = None


class WebViewWebSocketService(threading.Thread):
    """웹뷰 전용 WebSocket 연결을 유지하며 서버 메시지를 처리한다."""

    def __init__(self, app: "IntegratedWebViewApp", cfg: Config) -> None:
        super().__init__(daemon=True)
        self.app = app
        self.config = cfg
        self._stop_event = threading.Event()
        self.websocket = None
        self.loop: asyncio.AbstractEventLoop | None = None
        self.reconnect_interval = WEBVIEW_WS_RECONNECT_DELAY_SECONDS
        self.device_id = resolve_webview_device_id(cfg)
        self.logger = logging.getLogger("webview.ws")

    @property
    def stopped(self) -> bool:
        return self._stop_event.is_set()

    @property
    def connected(self) -> bool:
        return self.websocket is not None

    async def _connect_loop(self) -> None:
        while not self.stopped:
            try:
                ws_url = build_webview_ws_url(
                    self.config.webview_ws_url,
                    self.device_id,
                )
                self.logger.info("웹뷰 WS 연결 시도: %s", ws_url)
                async with websockets.connect(ws_url) as websocket:
                    self.websocket = websocket
                    self.logger.info("웹뷰 WS 연결 성공!!")
                    await self._handle_messages(websocket)
            except websockets.exceptions.ConnectionClosedError:
                self.logger.warning("웹뷰 WS 연결 종료, 재연결 대기")
            except Exception:
                self.logger.exception("웹뷰 WS 연결 오류")
            finally:
                self.websocket = None

            if not self.stopped:
                await asyncio.sleep(self.reconnect_interval)

    async def _handle_messages(self, websocket) -> None:
        async for message in websocket:
            if self.stopped:
                break

            try:
                data = json.loads(message)
            except json.JSONDecodeError:
                self.logger.warning("웹뷰 WS JSON 파싱 실패: %s", message)
                continue

            try:
                await self._process_message(data)
            except Exception:
                self.logger.exception("웹뷰 WS 메시지 처리 실패")

    async def _process_message(self, data: dict) -> None:
        if "error" in data and "type" in data:
            self.logger.error(
                "웹뷰 WS 서버 에러 [%s]: %s",
                data.get("type"),
                data.get("error"),
            )
            return

        message_type = data.get("type")
        meet_url = extract_meet_url(data)
        if meet_url:
            self.config.meet_web_url = meet_url
            self.logger.info("Meet URL 갱신: %s", meet_url)

        if message_type == "kiosk_info":
            payload = data.get("data", {})
            self.app.update_kiosk_info(
                name=payload.get("name") or self.app.kiosk_info.name,
                latitude=payload.get("latitude"),
                longitude=payload.get("longitude"),
                screen_url=payload.get("screen_url"),
            )
            return

        if message_type == "reboot":
            await self.send_log_message_async(
                "info",
                "재부팅 메시지를 수신하여 키오스크를 재부팅합니다.",
            )
            await self._handle_reboot()
            return

        if message_type == "control":
            # 백엔드 ``kiosk_manager.send_to_kiosk`` 가 ``{"type": "control", "data": {...}}`` 로 보냄.
            payload = data.get("data")
            await self._handle_control(payload if isinstance(payload, dict) else None)
            return

        if message_type == "get_screen":
            await self._send_current_screenshot()
            return

        self.logger.warning("알 수 없는 웹뷰 WS 메시지 타입: %s", message_type)

    async def _send_current_screenshot(self) -> None:
        """서버 요청을 받은 시점에만 현재 화면을 캡처해 전송."""
        try:
            image_data = await asyncio.to_thread(self._capture_screenshot)
        except Exception:
            self.logger.exception("웹뷰 스크린샷 캡처 실패")
            await self.send_log_message_async("error", "스크린샷 캡처 실패")
            return
        await self.send_screenshot_async(image_data)

    @staticmethod
    def _capture_screenshot() -> str:
        screenshot = ImageGrab.grab()
        buffer = io.BytesIO()
        screenshot.save(buffer, format="PNG")
        return base64.b64encode(buffer.getvalue()).decode("utf-8")

    async def _handle_control(self, payload: dict | None) -> None:
        """백엔드 ``{"type": "control", ...}`` 메시지를 PCB 에 반영.

        payload 는 ``PcbControlInput`` 과 동일한 wire-format. 빠진 필드는 PCB 가 NO_CHANGE 처리.
        실제 시리얼 송신은 ``PcbWorkerThread.apply_pcb_control`` (sync) — 이벤트 루프를 막지
        않도록 to_thread 로 위임한다.
        """
        if not isinstance(payload, dict) or not payload:
            self.logger.warning("PCB 제어 무시: payload 비어있음")
            return

        # ``IntegratedWebViewApp`` 가 ``pcb_worker`` 를 ws_service 보다 나중에 시작하므로
        # 시작 직후 한 두 번은 None 이 보일 수 있다 (재연결 시점엔 대부분 준비됨).
        worker = getattr(self.app, "pcb_worker", None)
        if worker is None or not hasattr(worker, "apply_pcb_control"):
            self.logger.warning(
                "PCB 제어 무시: pcb_worker 미준비 payload_keys=%s",
                sorted(payload.keys()),
            )
            return

        try:
            ok = await asyncio.to_thread(worker.apply_pcb_control, payload)
        except Exception:
            self.logger.exception("PCB 제어 적용 실패 payload=%s", payload)
            await self.send_log_message_async(
                "error",
                f"PCB 제어 실패: {sorted(payload.keys())}",
            )
            return

        self.logger.info(
            "PCB 제어 반영 ok=%s payload_keys=%s",
            ok,
            sorted(payload.keys()),
        )

    async def _handle_reboot(self) -> None:
        # ysoh 2026-06-13: macOS 재부팅 명령 추가
        import sys
        if sys.platform == "win32":
            reboot_cmd = 'shutdown /r /t 5 /c "웹뷰 WS 재부팅 명령 수신"'
        elif sys.platform == "darwin":
            reboot_cmd = "sudo shutdown -r +1 'WebView WS 재부팅 명령 수신'"
        else:
            self.logger.warning("재부팅 명령은 Windows 또는 macOS에서만 지원됩니다.")
            return

        await self.send_log_message_async(
            "info",
            f"재부팅 명령 실행: {reboot_cmd}",
        )
        try:
            await asyncio.to_thread(os.system, reboot_cmd)
        except Exception as exc:
            self.logger.error("재부팅 명령 실행 실패: %s", exc)
            await self.send_log_message_async("error", f"재부팅 실패: {exc}")

    async def send_log_message_async(self, log_type: str, message: str) -> None:
        if not self.connected:
            return

        payload = {
            "type": "log",
            "data": {
                "level": log_type,
                "message": message,
                "timestamp": int(time.time()),
            },
        }
        try:
            await self.websocket.send(json.dumps(payload, ensure_ascii=False))
        except Exception:
            self.logger.exception("웹뷰 로그 전송 실패")

    def send_log_message(self, log_type: str, message: str) -> None:
        if self.loop is None:
            return
        asyncio.run_coroutine_threadsafe(
            self.send_log_message_async(log_type, message),
            self.loop,
        )

    async def send_screenshot_async(self, image_data: str) -> None:
        if not self.connected:
            return

        payload = {
            "type": "screen",
            "data": {
                "image": image_data,
                "timestamp": int(time.time()),
            },
        }
        try:
            await self.websocket.send(json.dumps(payload, ensure_ascii=False))
        except Exception:
            self.logger.exception("웹뷰 스크린샷 전송 실패")

    def send_screenshot(self, image_data: str) -> None:
        if self.loop is None:
            return
        asyncio.run_coroutine_threadsafe(
            self.send_screenshot_async(image_data),
            self.loop,
        )

    async def request_meet_url_async(self) -> None:
        if not self.connected:
            return
        payload = {
            "type": "get_meet_url",
            "device_id": self.device_id,
            "timestamp": int(time.time()),
        }
        try:
            await self.websocket.send(json.dumps(payload, ensure_ascii=False))
        except Exception:
            self.logger.exception("Meet URL 요청 전송 실패")

    def request_meet_url(self) -> None:
        if self.loop is None:
            return
        asyncio.run_coroutine_threadsafe(
            self.request_meet_url_async(),
            self.loop,
        )

    async def send_pcb_status_async(self, status: StatusResponse) -> None:
        if not self.connected:
            return
        payload = {
            "type": "pcb_status",
            "device_id": self.device_id,
            "data": {
                "status": status.model_dump(),
                "timestamp": int(time.time()),
            },
        }
        try:
            await self.websocket.send(json.dumps(payload, ensure_ascii=False))
        except Exception:
            self.logger.exception("웹뷰 WS PCB 상태 전송 실패")

    def send_pcb_status(self, status: StatusResponse) -> None:
        if self.loop is None:
            return
        asyncio.run_coroutine_threadsafe(
            self.send_pcb_status_async(status),
            self.loop,
        )

    def run(self) -> None:
        self.loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self.loop)
        try:
            self.loop.run_until_complete(self._connect_loop())
        finally:
            self.loop.close()
            self.loop = None

    def stop(self) -> None:
        self._stop_event.set()
        if self.loop and self.websocket is not None:
            asyncio.run_coroutine_threadsafe(self.websocket.close(), self.loop)


