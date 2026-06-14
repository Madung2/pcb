"""
장치 WebSocket 연결 관리 (SMART_POLE / KIOSK / POLE_N_ED 공통).
# ysoh 2026-06-14
"""

from __future__ import annotations

import base64
import io
import json
import logging
import os
import sys
import threading
import time
import urllib.parse

from .config import config
from .env_utils import update_env_file

logger = logging.getLogger(__name__)


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


# ---------------------------------------------------------------------------
# 스크린샷 캡처 (서버 요청 시 1회)
# ---------------------------------------------------------------------------

def _capture_screenshot() -> str | None:  # ysoh 2026-06-14
    """현재 화면을 캡처하여 base64 PNG 문자열로 반환합니다.

    PIL.ImageGrab 은 macOS / Windows 모두 지원합니다.

    Returns:
        성공 시 base64 인코딩된 PNG 문자열, 실패 시 None
    """
    try:
        from PIL import ImageGrab

        screenshot = ImageGrab.grab()
        buffer = io.BytesIO()
        screenshot.save(buffer, format="PNG")
        image_base64 = base64.b64encode(buffer.getvalue()).decode("utf-8")
        logger.info(
            "스크린샷 캡처 성공: %dx%d, %d bytes",
            screenshot.width,
            screenshot.height,
            len(buffer.getvalue()),
        )
        return image_base64
    except Exception:
        logger.exception("스크린샷 캡처 실패")
        return None


def _send_screenshot(ws, device_id: str) -> None:  # ysoh 2026-06-14
    """스크린샷을 캡처하여 WebSocket 으로 전송합니다.

    서버 전송 포맷:
    {
        "type": "screen",
        "device_id": "<장치 UUID>",
        "data": {
            "image": "<base64 PNG>",
            "timestamp": <unix epoch>
        }
    }
    """
    image_data = _capture_screenshot()
    if image_data is None:
        # 캡처 실패 시 에러 응답
        error_payload = json.dumps({
            "type": "screen_error",
            "device_id": device_id,
            "data": {
                "message": "스크린샷 캡처 실패",
                "timestamp": int(time.time()),
            },
        })
        try:
            ws.send(error_payload)
        except Exception:
            logger.exception("스크린샷 에러 응답 전송 실패")
        return

    payload = json.dumps({
        "type": "screen",
        "device_id": device_id,
        "data": {
            "image": image_data,
            "timestamp": int(time.time()),
        },
    })
    try:
        ws.send(payload)
        logger.info("스크린샷 전송 완료: device_id=%s", device_id)
    except Exception:
        logger.exception("스크린샷 전송 실패")


# ---------------------------------------------------------------------------
# 서버 명령 처리
# ---------------------------------------------------------------------------

def _handle_server_message(  # ysoh 2026-06-14
    data: dict,
    ws,
    device_id: str,
) -> None:
    """서버 명령을 분기 처리합니다.

    Args:
        data: 수신된 JSON dict
        ws: 현재 연결된 WebSocket (응답 전송용)
        device_id: 장치 UUID
    """
    msg_type = data.get("type", "")
    meet_url = _extract_meet_url(data)
    if meet_url:
        logger.info("[WS 명령] meet_url 갱신 → %s", meet_url)
        config.meet_web_url = meet_url
        return

    if msg_type == "change_base_url":
        new_url = data.get("base_url", "").strip()
        if new_url:
            logger.info("[WS 명령] change_base_url → %s", new_url)
            config.base_url = new_url
            update_env_file("BASE_URL", new_url)
            # TODO: ysoh 2026-06-14 — WebView URL 변경 로직 추가 예정
        else:
            logger.warning("[WS 명령] change_base_url: base_url 비어있음")

    elif msg_type == "restart":
        logger.info("[WS 명령] restart → 프로그램 재시작")
        # TODO: ysoh 2026-06-14 — 재시작 로직 추가 예정
        if sys.platform == "win32":
            os.system('shutdown /r /t 5 /c "WebSocket restart 명령"')
        elif sys.platform == "darwin":
            logger.info("[WS 명령] macOS 재시작 — 수동 재시작 필요")

    elif msg_type == "get_screen":  # ysoh 2026-06-14
        logger.info("[WS 명령] get_screen → 스크린샷 캡처 및 전송")
        _send_screenshot(ws, device_id)

    else:
        logger.warning("[WS 명령] 알 수 없는 타입: %s", msg_type)


# ---------------------------------------------------------------------------
# WebSocket 연결 루프
# ---------------------------------------------------------------------------

def run_device_websocket(  # ysoh 2026-06-14
    device_id: str,
    websocket_addr: str,
) -> None:
    """WebSocket 서버에 연결하여 alive 전송 + 서버 명령을 수신·처리합니다.

    - 연결 실패 시 ``WS_RECONNECT_INTERVAL`` 마다 재시도 (프로그램 종료까지)
    - 연결 성공 후 1분마다 alive 메시지 전송
    - 서버 명령: change_base_url, restart, get_screen

    SMART_POLE / KIOSK / POLE_N_ED 모두 동일 로직으로 처리됩니다.
    별도 데몬 스레드에서 실행되므로 메인 스레드(WebView)를 블로킹하지 않습니다.
    """
    import websockets.sync.client as ws_sync

    if not websocket_addr:
        logger.warning(
            "WEBSOCKET_ADDR 비어있음 — WebSocket 연결을 시작하지 않습니다."
        )
        return

    # device_id 쿼리 파라미터 추가
    sep = "&" if "?" in websocket_addr else "?"
    ws_url = (
        f"{websocket_addr}{sep}"
        f"device_id={urllib.parse.quote(device_id)}"
    )

    def _send_alive(ws) -> None:
        payload = json.dumps({
            "type": "alive",
            "device_id": device_id,
            "timestamp": int(time.time()),
        })
        ws.send(payload)

    while True:
        try:
            logger.info("WebSocket 연결 시도: %s", ws_url)
            with ws_sync.connect(ws_url) as ws:
                logger.info("WebSocket 연결 성공!")

                # 연결 직후 자신의 device_id 전달
                register_msg = json.dumps({
                    "type": "register",
                    "device_id": device_id,
                    "device_type": config.asset_device_type,
                    "timestamp": int(time.time()),
                })
                ws.send(register_msg)
                logger.info(
                    "WebSocket 등록 메시지 전송: device_id=%s", device_id
                )

                last_alive = time.time()

                while True:
                    # 1분(60초) 주기 alive 전송
                    now = time.time()
                    if now - last_alive >= 60:
                        try:
                            _send_alive(ws)
                            last_alive = now
                            logger.debug("alive 전송")
                        except Exception:
                            logger.exception("alive 전송 실패 — 재연결")
                            break

                    # 서버 메시지 수신 (5초 타임아웃으로 alive 주기 확인)
                    try:
                        raw = ws.recv(timeout=5)
                    except TimeoutError:
                        continue
                    except Exception:
                        logger.exception("WebSocket 수신 오류 — 재연결")
                        break

                    try:
                        data = json.loads(raw)
                    except json.JSONDecodeError:
                        logger.warning(
                            "WebSocket JSON 파싱 실패: %s", raw[:200]
                        )
                        continue

                    try:
                        _handle_server_message(data, ws, device_id)
                    except Exception:
                        logger.exception("서버 메시지 처리 실패: %s", data)

        except Exception:
            logger.exception("WebSocket 연결 실패")

        wait_sec = max(0.5, float(config.ws_reconnect_interval))
        logger.info("WebSocket 재연결 대기 %.1f초...", wait_sec)
        time.sleep(wait_sec)


# ---------------------------------------------------------------------------
# 데몬 스레드 시작
# ---------------------------------------------------------------------------

def start_device_websocket_thread(  # ysoh 2026-06-14
    device_id: str,
) -> threading.Thread:
    """WebSocket 연결을 데몬 스레드로 시작합니다."""
    ws_addr = config.websocket_addr
    t = threading.Thread(
        target=run_device_websocket,
        args=(device_id, ws_addr),
        name="device-websocket",
        daemon=True,
    )
    t.start()
    return t
