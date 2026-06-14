""".env 파일 관리 및 장치 설정 조회 유틸리티."""

from __future__ import annotations

import json
import logging
import os
import urllib.parse
import urllib.request

from ._paths import user_env_path
from .config import config

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# .env 파일 특정 키 업데이트
# ---------------------------------------------------------------------------

def update_env_file(key: str, value: str) -> None:  # ysoh 2026-06-14
    """로컬 .env 파일에서 ``key=...`` 행을 찾아 값을 교체합니다.
    키가 없으면 파일 끝에 추가합니다.
    """
    env_path = user_env_path()
    if not env_path.is_file():
        env_path.write_text(f"{key}={value}\n", encoding="utf-8")
        return

    lines = env_path.read_text(encoding="utf-8").splitlines(keepends=True)
    found = False
    new_lines: list[str] = []
    for line in lines:
        stripped = line.lstrip()
        if stripped.startswith(f"{key}=") or stripped.startswith(f"{key} ="):
            new_lines.append(f"{key}={value}\n")
            found = True
        else:
            new_lines.append(line)
    if not found:
        if new_lines and not new_lines[-1].endswith("\n"):
            new_lines.append("\n")
        new_lines.append(f"{key}={value}\n")
    env_path.write_text("".join(new_lines), encoding="utf-8")


def _device_api_base_url() -> str:
    configured = (
        os.getenv("DEVICE_API_BASE_URL")
        or config.base_url
        or config.default_url
        or "https://hcg.jdone.co.kr"
    )
    return configured.strip().rstrip("/")


def query_device_base_url(device_id: str) -> dict | None:
    """장치별 기본 화면/LED/Meet URL 설정을 서버에서 조회한다."""
    did = (device_id or "").strip()
    if not did:
        return None

    api_base = _device_api_base_url()
    url = (
        f"{api_base}/api/v1/device-base-url"
        f"?device_id={urllib.parse.quote(did)}"
    )
    timeout = float(os.getenv("DEVICE_API_TIMEOUT", "10.0"))
    logger.info("device-base-url 조회 요청: %s", url)
    try:
        req = urllib.request.Request(url, headers={"Accept": "application/json"}, method="GET")
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = resp.read().decode("utf-8")
        data = json.loads(body)
    except Exception:
        logger.exception("device-base-url 조회 실패")
        return None

    if not isinstance(data, dict):
        logger.warning("device-base-url 응답 형식 오류: %r", data)
        return None
    return data


def resolve_device_urls(device_id: str) -> str:
    """서버 응답을 config에 반영하고 확정 base_url을 반환한다."""
    result = query_device_base_url(device_id)
    if result:
        base_url = (result.get("base_url") or "").strip()
        led_url = (result.get("led_url") or "").strip()
        meet_url = (result.get("meet_url") or result.get("meetUrl") or "").strip()

        if base_url:
            config.base_url = base_url
            logger.info("BASE_URL 서버 반영: %s", base_url)
        if led_url:
            config.led_url = led_url
            logger.info("LED_URL 서버 반영: %s", led_url)
        if meet_url:
            config.meet_web_url = meet_url
            logger.info("Meet URL 서버 반영: %s", meet_url)

    fallback = (config.base_url or config.default_url or "https://hcg.jdone.co.kr").strip()
    return fallback
