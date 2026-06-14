"""
설정 관리

.env 파일에서 환경변수를 읽어 설정값으로 사용.
"""

import os
from dataclasses import dataclass, field

from dotenv import load_dotenv

from ._paths import ensure_user_env, user_data_root

# 개발 모드: kiosk_module 패키지 상위 = 레포 루트.
# frozen(.exe): 실행 파일 옆 디렉터리.
# 둘 다 ``_paths.user_data_root()`` 로 통일 — 첫 실행 시 번들된 디폴트 .env 가 자동 복사된다.
_PROJECT_ROOT = user_data_root()
load_dotenv(ensure_user_env())

DEFAULT_SERIAL_BAUDRATE = 115200
DEFAULT_WS_ENABLED = True
DEFAULT_INPUT_MONITOR_ENABLED = True
DEFAULT_KIOSK_BROWSER_CMD = ""
DEFAULT_VOLUME_BAUDRATE = 38400
DEFAULT_VOLUME_HEX_CODES: frozenset[str] = frozenset()
WEBVIEW_WS_RECONNECT_DELAY_SECONDS = 30.0
STATUS_POLL_INTERVAL_SECONDS = float(os.getenv("STATUS_POLL_INTERVAL", "600"))


def _env_bool(name: str, default: bool = False) -> bool:
    v = os.getenv(name)
    if v is None:
        return default
    return v.strip().lower() in ("1", "true", "yes", "on")


def _webview_ws_url_from_env() -> str:
    """부팅 시 한 번: ``WEBVIEW_WS_URL`` → 레거시 ``WS_URL`` → ``WEBSOCKET_ADDR``."""
    return (
        os.getenv("WEBVIEW_WS_URL")
        or os.getenv("WS_URL")
        or os.getenv("WEBSOCKET_ADDR")
        or ""
    ).strip()


@dataclass
class Config:
    """모듈 설정."""

    # 시리얼 포트 (빈 값 또는 AUTO이면 ``SERIAL_PORT_DESCRIPTION_KEYWORD``로 자동 검색)
    serial_port: str = os.getenv("SERIAL_PORT", "COM3")
    serial_baudrate: int = int(os.getenv("SERIAL_BAUDRATE", str(DEFAULT_SERIAL_BAUDRATE)))
    serial_port_description_keyword: str = os.getenv(
        "SERIAL_PORT_DESCRIPTION_KEYWORD", "USB"
    )
    # USB 장치 식별 (리부트 후 COM 번호가 바뀌어도 동일 장치를 찾을 때 사용)
    serial_usb_vid: str = (os.getenv("SERIAL_USB_VID", "") or "").strip()
    serial_usb_pid: str = (os.getenv("SERIAL_USB_PID", "") or "").strip()
    serial_usb_serial: str = (os.getenv("SERIAL_USB_SERIAL", "") or "").strip()

    # 백엔드에서 키오스크 구분 (예: WS 이벤트 ``PERSON_DETECTED`` 페이로드)
    kiosk_id: str = (os.getenv("KIOSK_ID", "") or "").strip()

    # ysoh 2026-06-13: 장치 타입 (KIOSK / SMART_POLE / POLE_N_ED) 및 장치 UUID
    asset_device_type: str = (os.getenv("ASSET_DEVICE_TYPE", "") or "KIOSK").strip().upper()
    device_id: str = (os.getenv("DEVICE_ID", "") or "").strip()

    # ysoh 2026-06-14: 송출 베이스 URL / 기본 URL / LED URL / WebSocket 주소
    base_url: str = (os.getenv("BASE_URL", "") or "").strip()
    default_url: str = (os.getenv("DEFAULT_URL", "") or "https://hcg.jdone.co.kr").strip()
    led_url: str = (os.getenv("LED_URL", "") or "").strip()
    websocket_addr: str = (os.getenv("WEBSOCKET_ADDR", "") or "").strip()

    # WebSocket 브릿지 + 웹뷰 WS: 모두 ``webview_ws_url`` 하나.
    ws_enabled: bool = _env_bool("WS_ENABLED", default=DEFAULT_WS_ENABLED)
    ws_reconnect_interval: float = float(
        os.getenv("WS_RECONNECT_INTERVAL", "5.0")
    )

    # webview_enabled: bool = _env_bool("WEBVIEW_ENABLED", default=False)
    # ysoh 2026-06-13
    webview_enabled: bool = _env_bool("WEBVIEW_ENABLED", default=True)
    webview_ws_url: str = field(default_factory=_webview_ws_url_from_env)
    webview_ws_kiosk_id: str = (os.getenv("KIOSK_ID", "") or "").strip()
    webview_devtools: bool = _env_bool("WEBVIEW_DEVTOOLS", default=False)
    webview_tray_enabled: bool = _env_bool(
        "WEBVIEW_TRAY_ENABLED", default=True
    )

    # 사람 없음 + 입력 유휴 시 자동 도어 닫기 (초).
    # 전제: pynput이 동작해야 유휴 시간이 증가함(기본 20초).
    vacant_idle_close_seconds: float = float(
        os.getenv("VACANT_IDLE_CLOSE_SECONDS", "20.0")
    )

    # 키보드·마우스 전역 감지.
    input_monitor_enabled: bool = _env_bool(
        "INPUT_MONITOR_ENABLED", default=DEFAULT_INPUT_MONITOR_ENABLED
    )

    # 오른쪽 버튼 전용 Meet URL. 서버 WebSocket 메시지로 런타임 갱신된다.
    meet_web_url: str = ""
    # 외부 브라우저 fallback은 항상 기본 Chrome 경로 자동 검색을 사용.
    kiosk_browser_cmd: str = DEFAULT_KIOSK_BROWSER_CMD
    background_browser_timeout_seconds: float = float(
        os.getenv("BACKGROUND_BROWSER_TIMEOUT_SECONDS", "300")
    )

    # Windows: 별도 시리얼(COM)에서 U/D 문자로 OS 마스터 볼륨 업/다운
    # (PCB 제어용 SERIAL_PORT 와 별도: VOLUME_SERIAL_PORT)
    volume_serial_enabled: bool = _env_bool("VOLUME_SERIAL_ENABLED", default=False)
    volume_serial_port: str = (os.getenv("VOLUME_SERIAL_PORT", "COM5") or "COM5").strip()
    volume_serial_baudrate: int = int(
        os.getenv("VOLUME_BAUDRATE", str(DEFAULT_VOLUME_BAUDRATE))
    )
    volume_serial_timeout: float = float(os.getenv("VOLUME_SERIAL_TIMEOUT", "0.2"))
    # 비우면 U/D 문자만 처리. 바이너리 장치는 VOLUME_*_HEX_CODES 로 추가
    volume_up_hex_codes: frozenset[str] = field(
        default_factory=lambda: DEFAULT_VOLUME_HEX_CODES
    )
    volume_down_hex_codes: frozenset[str] = field(
        default_factory=lambda: DEFAULT_VOLUME_HEX_CODES
    )

    # 테스트 모드: 실제 PCB 없이 가짜 시리얼로 제어·수집 로직을 그대로 돌려본다.
    # ``true`` 면 ``FakeSerialManager`` 가 사용되고 시리얼 포트 검색·연결을 생략한다.
    test_mode_enabled: bool = _env_bool("TEST_MODE_ENABLED", default=False)

    # 로그
    log_level: str = os.getenv("LOG_LEVEL", "INFO")

    def effective_ws_bridge_url(self) -> str:
        """백엔드 ``WSBridge``·웹뷰 WS 공통 URL."""
        return (self.webview_ws_url or "").strip()

    @property
    def pcb_control_enabled(self) -> bool:
        """PCB 제어는 키오스크 장치에서만 활성화."""
        return (self.asset_device_type or "").strip().upper() == "KIOSK"

    def __repr__(self):
        return (
            f"Config(\n"
            f"  kiosk_id={self.kiosk_id!r},\n"
            f"  asset_device_type={self.asset_device_type!r},\n"
            f"  serial={self.serial_port}@{self.serial_baudrate}, "
            f"port_kw={self.serial_port_description_keyword!r},\n"
            f"  serial_usb={self.serial_usb_vid}:{self.serial_usb_pid}"
            f"{(' SER=' + self.serial_usb_serial) if self.serial_usb_serial else ''},\n"
            f"  ws_enabled={self.ws_enabled}, "
            f"ws_bridge_url={self.effective_ws_bridge_url()!r},\n"
            f"  webview_enabled={self.webview_enabled}, "
            f"webview_ws_url={self.webview_ws_url},\n"
            f"  vacant_idle_close={self.vacant_idle_close_seconds}s,\n"
            f"  input_monitor={self.input_monitor_enabled},\n"
            f"  meet_url_set={bool(self.meet_web_url)},\n"
            f"  browser_timeout={self.background_browser_timeout_seconds}s,\n"
            f"  volume_serial={self.volume_serial_enabled} "
            f"{self.volume_serial_port!r}@{self.volume_serial_baudrate},\n"
            f"  test_mode={self.test_mode_enabled},\n"
            f"  log={self.log_level}\n"
            f")"
        )


# 싱글톤 인스턴스
config = Config()
