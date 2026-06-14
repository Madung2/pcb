"""
WebSocket 브릿지 생성 및 서버 제어(type=control) 처리.
"""

from __future__ import annotations

import logging
import urllib.parse
from functools import partial

from pydantic import ValidationError

from .config import config
from .device_controller import Controllerer, PcbControlInput
from .status_monitor import StatusMonitor
from .ws_bridge import WSBridge

logger = logging.getLogger(__name__)

_WS_CONTROL_KEYS = frozenset(PcbControlInput.model_fields)


def _extract_meet_url(data: dict) -> str:
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


def _append_device_id_query(url: str, device_id: str) -> str:
    """WS URL 에 device_id 쿼리를 추가하거나 기존 값을 교체한다."""
    if not url or not device_id:
        return url
    parsed = urllib.parse.urlparse(url)
    qsl = urllib.parse.parse_qsl(parsed.query, keep_blank_values=True)
    updated = False
    out: list[tuple[str, str]] = []
    for key, value in qsl:
        if key.lower() in ("device_id", "deviceid"):
            out.append((key, device_id))
            updated = True
        else:
            out.append((key, value))
    if not updated:
        out.append(("device_id", device_id))
    return urllib.parse.urlunparse(
        parsed._replace(query=urllib.parse.urlencode(out, doseq=True))
    )


def handle_ws_message(
    controller: Controllerer,
    data: object,
) -> None:
    """WebSocket JSON을 ``type`` 에 따라 분기 처리합니다.

    - ``control``: PCB 즉시 제어.
    """
    logger.info(f"[WS 수신] {data}")
    if not isinstance(data, dict):
        return

    msg_type = data.get("type")
    meet_url = _extract_meet_url(data)
    if meet_url:
        config.meet_web_url = meet_url
        logger.info("[WS] Meet URL 갱신: %s", meet_url)
        if msg_type != "control":
            return

    if msg_type != "control":
        return

    payload = {k: v for k, v in data.items() if k in _WS_CONTROL_KEYS}
    if not payload:
        logger.warning(f"[WS] type=control 이지만 제어 필드가 없습니다.")
        return
    try:
        control = PcbControlInput.model_validate(payload)
    except ValidationError as e:
        logger.error(f"[WS] 제어 메시지 검증 실패: {e}")
        return
    controller.send_control(control)


def create_ws_bridge(
    controller: Controllerer,
    monitor: StatusMonitor,
) -> WSBridge | None:
    """설정에 따라 ``WSBridge``를 만들고 메시지 핸들러를 연결합니다.

    연결 URL은 ``config.webview_ws_url`` 하나뿐입니다. 레거시 ``WS_URL`` 은
    초기 로드 시 ``webview_ws_url`` 과 합쳐집니다.
    """
    if not config.ws_enabled:
        logger.info(
            f"WebSocket 비활성화. "
            f"시리얼 제어만 동작합니다."
        )
        return None

    ws_url = config.effective_ws_bridge_url()
    if not ws_url:
        logger.warning(
            "WebSocket URL 이 없습니다. "
            "WEBVIEW_WS_URL 또는 레거시 WS_URL 을 설정하세요."
        )
        return None

    device_id = (config.device_id or config.kiosk_id or "").strip()
    if device_id:
        ws_url = _append_device_id_query(ws_url, device_id)
    else:
        logger.warning(
            "WSBridge: kiosk_id(=자산 UUID) 가 비어 있어 device_id 쿼리를 붙이지 못했습니다. "
            "DEVICE_ID 또는 KIOSK_ID 환경변수를 설정하세요."
        )

    logger.info("WSBridge 연결 URL(엔드포인트/캐시 우선): %s", ws_url)
    bridge = WSBridge(
        ws_url=ws_url,
        controller=controller,
        monitor=monitor,
        reconnect_interval=config.ws_reconnect_interval,
    )
    bridge.on_message = partial(handle_ws_message, controller)
    return bridge
