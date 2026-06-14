"""
GPS SMART KIOSK 통신 모듈 - 메인 엔트리포인트

실행 방법:
    uv sync
    uv run python main.py

WebView 단독 표시 (macOS / Windows):
    uv run python main.py --display https://example.com
    uv run python main.py --display https://example.com --title "My Kiosk"
    uv run python main.py --display https://example.com --no-fullscreen --width 1280 --height 800

.exe 빌드 (예시):
    uv add --group dev pyinstaller
    uv run pyinstaller --onefile main.py
"""
# ysoh 2026-06-14

from __future__ import annotations

import asyncio
import logging
import sys
import threading
import time

from kiosk_module.config import config
from kiosk_module.device_display import (
    build_device_url,
    display_local_fallback,
    download_web_resources,
    get_res_dir,
    kiosk_display,
    pole_n_ed_display,
    probe_url,
    smartpole_display,
)
from kiosk_module.device_websocket import start_device_websocket_thread
from kiosk_module.env_utils import resolve_device_urls
from kiosk_module.kiosk_runner import run_kiosk
from kiosk_module.serial_manager import SerialManager


# ---------------------------------------------------------------------------
# 로깅
# ---------------------------------------------------------------------------

def setup_logging() -> None:  # ysoh 2026-06-14
    """로깅 설정."""
    logging.basicConfig(
        level=getattr(logging, config.log_level, logging.INFO),
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )


# ---------------------------------------------------------------------------
# 장치 타입별 시작 함수
# ---------------------------------------------------------------------------

def _start_device_common(  # ysoh 2026-06-14
    device_type: str,
    device_id: str,
    base_url: str,
    title: str,
) -> int:
    """SMART_POLE / KIOSK / POLE_N_ED 공통 시작 로직.

    1. URL 조립
    2. WebSocket 데몬 스레드 시작
    3. 5초 후 웹 리소스 다운로드 (백그라운드)
    4. KIOSK 일 때만 PCB 모니터링·kiosk_events 워커 시작
    5. URL 접속 가능 → WebView 표시 / 불가 → 로컬 캐시 fallback
    """
    logger = logging.getLogger(f"start_{device_type.lower()}")

    url = build_device_url(base_url, device_id)
    res_dir = get_res_dir(device_type)
    logger.info("%s 모드 시작: url=%s res_dir=%s", device_type, url, res_dir)

    # WebSocket 연결 (데몬 스레드)
    start_device_websocket_thread(device_id)

    # 5초 후 리소스 다운로드 (백그라운드)
    def _download_after_delay() -> None:
        time.sleep(5)
        ok = download_web_resources(url, res_dir)
        if not ok:
            logger.warning(
                "%s 리소스 다운로드 실패 — 로컬 캐시가 있으면 다음 실행 시 사용됩니다.",
                device_type,
            )

    threading.Thread(
        target=_download_after_delay,
        name=f"{device_type.lower()}-resource-download",
        daemon=True,
    ).start()

    if device_type == "KIOSK":
        if config.test_mode_enabled:
            logger.info("TEST_MODE_ENABLED=true — 가짜 시리얼 사용")
            serial_port = "FAKE"
        else:
            serial_port = resolve_serial_port()
            if serial_port != config.serial_port.strip():
                logger.info("시리얼 포트(자동): %s", serial_port)

        def _run_kiosk_events_worker() -> None:
            logger.info(
                "KIOSK PCB 모니터링/kiosk_events 워커 진입: port=%s baud=%s",
                serial_port,
                config.serial_baudrate,
            )
            try:
                asyncio.run(run_kiosk(serial_port, config.serial_baudrate))
            except BaseException:
                logger.exception("KIOSK PCB 모니터링/kiosk_events 워커 종료/실패")
                raise

        threading.Thread(
            target=_run_kiosk_events_worker,
            name="kiosk-events-worker",
            daemon=True,
        ).start()
        logger.info("KIOSK PCB 모니터링/kiosk_events 워커 시작")

    # ysoh 2026-06-14: 장치 타입별 display 함수 분기
    # 각 함수가 고유한 기본 title · 듀얼 디스플레이 등 자체 로직을 가지므로
    # url 만 전달하고 나머지는 함수 내부 기본값에 위임합니다.
    _DISPLAY_FN_MAP = {
        "SMART_POLE": smartpole_display,
        "KIOSK": kiosk_display,
        "POLE_N_ED": pole_n_ed_display,
    }
    _display_fn = _DISPLAY_FN_MAP.get(device_type)
    if _display_fn is None:
        logger.error("알 수 없는 device_type: %s — display 함수 없음", device_type)
        raise SystemExit(f"알 수 없는 ASSET_DEVICE_TYPE: {device_type}")

    if probe_url(url):
        return _display_fn(url)

    logger.warning("원격 URL 접속 불가 → 로컬 캐시 fallback")
    return display_local_fallback(res_dir, title=title)


def start_smartpole(device_id: str, base_url: str) -> int:  # ysoh 2026-06-14
    """스마트폴 모드 시작."""
    return _start_device_common(
        "SMART_POLE", device_id, base_url, title="JDONE Smart Pole"
    )


def start_kiosk(device_id: str, base_url: str) -> int:  # ysoh 2026-06-14
    """키오스크 모드 시작."""
    return _start_device_common(
        "KIOSK", device_id, base_url, title="JDONE Kiosk"
    )


def start_pole_n_ed(device_id: str, base_url: str) -> int:  # ysoh 2026-06-14
    """POLE_N_ED 모드 시작."""
    return _start_device_common(
        "POLE_N_ED", device_id, base_url, title="JDONE Pole N ED"
    )


# ---------------------------------------------------------------------------
# fun_start: 통합 진입
# ---------------------------------------------------------------------------

def fun_start() -> int:  # ysoh 2026-06-14
    """통합 시작 함수.
    1. .env 로드 (config.py 싱글톤이 이미 로드)
    2. 서버에서 base_url / led_url / meet_url 조회
    3. ASSET_DEVICE_TYPE 에 따라 start_smartpole / start_kiosk / start_pole_n_ed 호출
    4. WebSocket 연결 (데몬 스레드) — _start_device_common 내부에서 처리
    """
    logger = logging.getLogger("fun_start")
    device_id = config.device_id
    if not device_id:
        logger.error("DEVICE_ID 가 비어 있습니다. .env 에 DEVICE_ID 를 설정하세요.")
        raise SystemExit("DEVICE_ID 가 비어 있습니다.")

    logger.info(
        "fun_start: device_type=%s device_id=%s",
        config.asset_device_type,
        device_id,
    )

    base_url = resolve_device_urls(device_id)
    logger.info("확정된 BASE_URL: %s", base_url)

    # 장치 타입별 분기
    
    dispatch = {
        "SMART_POLE": start_smartpole,
        "KIOSK": start_kiosk,
        "POLE_N_ED": start_pole_n_ed,
    }
    starter = dispatch.get(config.asset_device_type)
    if starter is None:
        logger.error("알 수 없는 ASSET_DEVICE_TYPE: %s", config.asset_device_type)
        raise SystemExit(f"알 수 없는 ASSET_DEVICE_TYPE: {config.asset_device_type}")

    return starter(device_id, base_url)


# ---------------------------------------------------------------------------
# 기존 시리얼 관련 함수
# ---------------------------------------------------------------------------

def resolve_serial_port() -> str:  # ysoh 2026-06-14
    """환경설정의 포트 문자열을 실제 장치 경로로 해석."""
    logger = logging.getLogger("serial_resolver")
    logger.info(
        "PCB 시리얼 포트 검색 시작: configured=%r keyword=%r usb=%s:%s serial=%r",
        config.serial_port,
        config.serial_port_description_keyword,
        config.serial_usb_vid or "-",
        config.serial_usb_pid or "-",
        config.serial_usb_serial,
    )
    found = SerialManager.resolve_port_choice(
        config.serial_port,
        config.serial_port_description_keyword,
        usb_vid=config.serial_usb_vid or None,
        usb_pid=config.serial_usb_pid or None,
        usb_serial=config.serial_usb_serial or None,
    )
    if found is None:
        if config.serial_usb_vid and config.serial_usb_pid:
            raise SystemExit(
                "USB 장치 자동 검색 실패: "
                f"VID:PID={config.serial_usb_vid}:{config.serial_usb_pid}"
                f"{(' SER=' + config.serial_usb_serial) if config.serial_usb_serial else ''} "
                "에 맞는 포트가 없습니다."
            )
        kw = (config.serial_port_description_keyword or "").strip() or "USB"
        raise SystemExit(
            f"자동 포트 검색 실패: 설명에 {kw!r} 가 포함된 포트가 없습니다."
        )
    logger.info("PCB 시리얼 포트 검색 완료: %s", found)
    return found


async def run_cli_main(port: str) -> None:  # ysoh 2026-06-14
    await run_kiosk(
        port,
        config.serial_baudrate,
        stop_event=None,
        controller_ref=None,
    )


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------

def main() -> int:  # ysoh 2026-06-14
    setup_logging()
    logger = logging.getLogger("main")

    logger.info("=" * 50)
    logger.info("설정: %s", config)
    logger.info("=" * 50)

    # ysoh 2026-06-14: ASSET_DEVICE_TYPE 분기 → fun_start
    if config.asset_device_type in ("SMART_POLE", "KIOSK", "POLE_N_ED"):
        return fun_start()

    # -------------------------------------------------------------------
    # --display CLI 인자 (범용 WebView 단독 표시)  # ysoh 2026-06-13
    # -------------------------------------------------------------------
    if "--display" in sys.argv:
        idx = sys.argv.index("--display")
        if idx + 1 >= len(sys.argv):
            print(
                "사용법: python main.py --display <URL> [--title <제목>] "
                "[--no-fullscreen] [--width N] [--height N]",
                file=sys.stderr,
            )
            return 1
        kw = dict(
            url=sys.argv[idx + 1],
            title="JDONE Kiosk Display",
            fullscreen="--no-fullscreen" not in sys.argv,
            width=1280,
            height=800,
        )
        for flag, key, conv in [
            ("--title", "title", str),
            ("--width", "width", int),
            ("--height", "height", int),
        ]:
            if flag in sys.argv:
                fi = sys.argv.index(flag)
                if fi + 1 < len(sys.argv):
                    kw[key] = conv(sys.argv[fi + 1])
        return smartpole_display(**kw)

    # 기존 PCB 시리얼 모드
    if not config.pcb_control_enabled:
        logger.info("ASSET_DEVICE_TYPE=%s — PCB 제어 비활성화", config.asset_device_type)
        port = ""
    elif config.test_mode_enabled:
        logger.info("TEST_MODE_ENABLED=true — 가짜 시리얼 사용")
        port = "FAKE"
    else:
        port = resolve_serial_port()
        if port != config.serial_port.strip():
            logger.info("시리얼 포트(자동): %s", port)

    if not config.pcb_control_enabled:
        logger.info("ASSET_DEVICE_TYPE=%s — PCB 제어 비활성화, 종료", config.asset_device_type)
        return 0

    asyncio.run(run_cli_main(port))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        print("\n프로그램이 종료되었습니다.")
