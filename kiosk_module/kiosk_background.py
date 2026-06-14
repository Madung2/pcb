"""
WebSocket 연결 태스크를 실행·중지합니다.
"""

from __future__ import annotations

import asyncio
import logging

from .status_monitor import StatusMonitor
from .ws_bridge import WSBridge

logger = logging.getLogger(__name__)


async def _status_poll_loop(
    monitor: StatusMonitor,
    bridge: WSBridge | None,
    interval_sec: float,
) -> None:
    """PCB 상태를 낮은 빈도로 조회하고 조회 직후 WS에 전송한다."""
    try:
        while True:
            try:
                await asyncio.to_thread(monitor.poll_once)
                if bridge is not None:
                    await bridge.send_status()
            except Exception:
                logger.exception("PCB 상태 조회/전송 실패 — 다음 주기까지 계속합니다.")
            await asyncio.sleep(interval_sec)
    except asyncio.CancelledError:
        logger.debug("상태 조회 루프 취소됨")
        raise


async def run_polling_and_ws(
    monitor: StatusMonitor,
    bridge: WSBridge | None,
    *,
    stop_event: asyncio.Event | None,
    poll_interval: float | None = None,
) -> None:
    """5분 단위 PCB 상태 조회와 선택적 WS ``connect()`` 루프를 유지합니다.

    ``stop_event``가 있으면 이벤트가 set되거나 하위 태스크가 끝날 때까지 대기한 뒤
    WS 연결 해제·나머지 태스크를 취소합니다.
    """
    poll_sec = 300.0 if poll_interval is None else float(poll_interval)
    ws_task: asyncio.Task | None = None
    if bridge is not None:
        ws_task = asyncio.create_task(bridge.connect())
    poll_task = asyncio.create_task(_status_poll_loop(monitor, bridge, poll_sec))

    if stop_event is None:
        tasks = [poll_task]
        if ws_task is not None:
            tasks.append(ws_task)
        await asyncio.gather(*tasks)
        return

    stop_task = asyncio.create_task(stop_event.wait())
    wait_set: set[asyncio.Task] = {poll_task, stop_task}
    if ws_task is not None:
        wait_set.add(ws_task)

    done, pending = await asyncio.wait(
        wait_set,
        return_when=asyncio.FIRST_COMPLETED,
    )
    if bridge is not None:
        await bridge.disconnect()
    for t in pending:
        t.cancel()
    for t in pending:
        try:
            await t
        except asyncio.CancelledError:
            pass
    for t in done:
        if t is not stop_task and not t.cancelled():
            try:
                await t
            except asyncio.CancelledError:
                pass
            except Exception:
                logger.exception(
                    "WebSocket 태스크가 예외로 끝나 연결 루프를 정리합니다."
                )
                raise
