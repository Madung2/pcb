"""
WebSocket 브릿지 클래스

백엔드 서버와 WebSocket으로 연결하여,
수신한 메시지를 Controllerer/StatusMonitor에 전달하고,
PCB 상태를 백엔드에 보고하는 뼈대.

※ JSON 메시지 처리 로직은 예슬님이 직접 구현하세요.
   on_ws_message() 메서드를 오버라이드하거나 콜백을 등록하면 됩니다.
"""

import asyncio
import json
import logging
from typing import Callable, Optional

import websockets
from websockets.exceptions import ConnectionClosed

from .config import config
from .device_controller import Controllerer
from .status_monitor import StatusMonitor

logger = logging.getLogger(__name__)

try:
    # websockets >= 13 은 연결 상태를 State enum 으로 노출한다(.open 속성 제거됨).
    from websockets.protocol import State as _WSState
except Exception:  # pragma: no cover - 구버전 호환
    _WSState = None


class WSBridge:
    """백엔드 WebSocket 연동 브릿지.

    Usage:
        bridge = WSBridge(
            ws_url="wss://백엔드/ws",
            controller=device_controller,
            monitor=status_monitor,
        )

        # 메시지 수신 핸들러 등록 (예슬님이 구현)
        bridge.on_message = my_handler

        await bridge.connect()
    """

    def __init__(
        self,
        ws_url: str,
        controller: Controllerer,
        monitor: StatusMonitor,
        reconnect_interval: float = 5.0,
    ):
        self.ws_url = ws_url
        self.controller = controller
        self.monitor = monitor
        self.reconnect_interval = reconnect_interval

        self._ws = None
        self._running = False

        # ─── 콜백: 예슬님이 직접 구현할 부분 ───
        self.on_message: Optional[Callable[[dict], None]] = None
        self.on_connected: Optional[Callable[[], None]] = None
        self.on_disconnected: Optional[Callable[[], None]] = None

    # ──────────────────────────────────────────
    # 연결 관리
    # ──────────────────────────────────────────
    async def connect(self):
        """WebSocket 서버에 연결하고 메시지 수신 루프 시작.

        연결 끊기면 자동 재연결.
        """
        self._running = True

        while self._running:
            try:
                logger.info(f"WebSocket 연결 시도: {self.ws_url}")
                async with websockets.connect(self.ws_url) as ws:
                    self._ws = ws
                    logger.info(f"WebSocket 연결됨: {self.ws_url}")

                    if self.on_connected:
                        self.on_connected()

                    await self._receive_loop(ws)

            except ConnectionClosed as e:
                logger.warning(f"WebSocket 연결 끊김: {e}")
            except Exception as e:
                logger.error(f"WebSocket 에러: {e}")
            finally:
                self._ws = None
                if self.on_disconnected:
                    self.on_disconnected()

            if self._running:
                logger.info(
                    f"{self.reconnect_interval}초 후 재연결 시도..."
                )
                await asyncio.sleep(self.reconnect_interval)

    async def disconnect(self):
        """WebSocket 연결 종료."""
        self._running = False
        if self._ws:
            await self._ws.close()
            self._ws = None

    @property
    def is_connected(self) -> bool:
        ws = self._ws
        if ws is None:
            return False
        # websockets >= 13: ClientConnection 에 .open 이 없으므로 state / close_code 로 판정.
        state = getattr(ws, "state", None)
        if state is not None:
            if _WSState is not None:
                return state is _WSState.OPEN
            return str(getattr(state, "name", state)).upper() == "OPEN"
        # 구버전(websockets < 13) 호환: .open 사용.
        return bool(getattr(ws, "open", False))

    # ──────────────────────────────────────────
    # 메시지 송신
    # ──────────────────────────────────────────
    async def send(self, data: dict):
        """백엔드로 JSON 메시지 전송.

        Args:
            data: 전송할 딕셔너리 (JSON 직렬화됨)
        """
        if not self.is_connected:
            logger.warning(f"WebSocket 미연결 상태에서 전송 시도")
            return

        try:
            msg = json.dumps(data, ensure_ascii=False)
            await self._ws.send(msg)
            logger.debug(f"WS TX: {msg}")
        except Exception as e:
            logger.error(f"WS 전송 에러: {e}")

    def schedule_send(self, data: dict) -> None:
        """폴링 등 동기 콜백에서 ``send``를 예약합니다. 실행 중인 asyncio 루프가 없으면 무시."""
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            logger.warning(f"WebSocket schedule_send: 실행 중인 asyncio 루프 없음 — 전송 생략")
            return
        loop.create_task(self.send(data))

    async def send_status(self):
        """현재 PCB 상태를 백엔드로 전송.

        ※ JSON 구조는 예슬님이 필요에 따라 수정하세요.
        """
        status_dict = self.monitor.to_dict()
        if status_dict is None:
            return

        await self.send({
            "type": "status",
            "device_id": config.device_id or config.kiosk_id,
            "data": status_dict,
        })

    # ──────────────────────────────────────────
    # 메시지 수신 루프
    # ──────────────────────────────────────────
    async def _receive_loop(self, ws):
        """WebSocket 메시지 수신 루프."""
        async for message in ws:
            try:
                data = json.loads(message)
                logger.debug(f"WS RX: {data}")

                # ─── 여기에 메시지 처리 로직 추가 ───
                # 예슬님이 on_message 콜백을 등록하거나
                # 이 클래스를 상속해서 처리하세요.
                if self.on_message:
                    self.on_message(data)

            except json.JSONDecodeError:
                logger.warning(f"WS 수신: JSON 파싱 실패 → {message}")
            except Exception as e:
                logger.error(f"WS 메시지 처리 에러: {e}")

    def __repr__(self):
        status = "연결됨" if self.is_connected else "미연결"
        return f"WSBridge(url={self.ws_url!r}, {status})"
