"""
JDONE Smart Pole — PyQt5: .env 대응 변수 입력 + 시리얼 연결 + 통신 로그

의존성:
    uv sync --group gui

실행:
    uv run python gui_main.py
"""

from __future__ import annotations

import logging
import os
import signal
import subprocess
import sys
import time
from pathlib import Path
from typing import Optional

from PyQt5.QtCore import Qt, QThread, QTimer, pyqtSignal
from PyQt5.QtGui import QColor, QPalette
from PyQt5.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QFormLayout,
    QFrame,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from kiosk_module._paths import user_env_path
from kiosk_module.autostart import (
    can_manage_autostart,
    is_autostart_enabled,
    set_autostart_enabled,
)
from kiosk_module.config import config
from kiosk_module.system_logging import (
    read_log_from_offset,
    read_log_tail,
    setup_system_logging,
    system_log_path,
)
from kiosk_module.serial_manager import SerialManager
from kiosk_module.test_input_ipc import CMD_BTN_RIGHT, enqueue_test_command


# GUI가 더 이상 저장하지 않는 예전 환경변수. 부모 프로세스 환경에 남아 있어도
# 자식 main.py 실행에는 전달하지 않아 현재 화면/저장값만 적용되게 한다.
DEPRECATED_GUI_ENV_KEYS = {
    "KIOSK_ID",
    "KIOSK_BROWSER_CMD",
    "WEBVIEW_WS_KIOSK_ID",
    "WEBVIEW_WS_URL",
    "WS_URL",
    "WEBVIEW_WS_RECONNECT_INTERVAL",
    "WEBVIEW_SCREENSHOT_INTERVAL",
    "WEBVIEW_ERROR_REFRESH_INTERVAL",
    "WEBVIEW_NETWORK_PROBE_TIMEOUT",
    "AUTO_OPEN_DOOR_ON_PERSON",
    "DEFAULT_ASSET_API_BASE_URL",
    "ASSET_API_TOKEN",
    "ASSET_API_TIMEOUT",
    "ASSET_NEARBY_RADIUS",
    "ASSET_NEARBY_COUNT",
    "ASSET_NEARBY_TYPE",
    "NEARBY_ASSET_CACHE_PATH",
    "NEARBY_ASSET_REFRESH_INTERVAL",
    "CURRENT_LATITUDE",
    "CURRENT_LONGITUDE",
    "MEET_WEB_URL",
    "PERSON_DETECTED_MP3_PATH",
    "PERSON_DETECTED_TTS_TEXT",
    "PERSON_DETECTED_TTS_LANG",
    "PERSON_DETECTED_TTS_AUTOGEN",
    "tts_text",
    "tts_text2",
    "SERIAL_BAUDRATE",
    "SERIAL_PORT_DESCRIPTION_KEYWORD",
    "WS_ENABLED",
    "WEBVIEW_TRAY_ENABLED",
    "VOLUME_SERIAL_ENABLED",
    "VOLUME_BAUDRATE",
    "VOLUME_SERIAL_BAUDRATE",
    "VOLUME_SERIAL_TIMEOUT",
    "VOLUME_UP_HEX_CODES",
    "VOLUME_DOWN_HEX_CODES",
    "TEST_MODE_ENABLED",
}


def _detect_pcb_on_port(
    port: str,
    baudrate: int,
    *,
    timeout: float = 1.0,
    attempts: int = 2,
    emit=None,
) -> bool:
    """``port``에 PCB 상태 요청(``S``) 프레임을 보내고 유효한 상태 응답이 오면 True."""
    import serial

    from pcb_connection_check import (
        build_status_request_frame,
        extract_fixed_status_frames,
        extract_frames,
        parse_status_response,
    )

    frame = build_status_request_frame()
    try:
        with serial.Serial(
            port=port,
            baudrate=baudrate,
            bytesize=serial.EIGHTBITS,
            stopbits=serial.STOPBITS_ONE,
            parity=serial.PARITY_NONE,
            timeout=0.1,
            write_timeout=1.0,
        ) as ser:
            ser.reset_input_buffer()
            ser.reset_output_buffer()
            for _ in range(max(1, attempts)):
                ser.write(frame)
                ser.flush()
                raw = b""
                deadline = time.monotonic() + timeout
                while time.monotonic() < deadline:
                    chunk = ser.read(ser.in_waiting or 1)
                    if not chunk:
                        continue
                    raw += chunk
                    frames, _ = extract_frames(raw)
                    frames = [*frames, *extract_fixed_status_frames(raw)]
                    if any(parse_status_response(f) is not None for f in frames):
                        return True
                time.sleep(0.2)
    except Exception as exc:  # serial.SerialException 외 권한/사용중 등 포함
        if emit:
            emit(f"  {port}: 열기 실패 ({exc})")
        return False
    return False


def _detect_knob_on_port(
    port: str,
    baudrate: int,
    *,
    listen_seconds: float = 4.0,
    emit=None,
) -> bool:
    """``port``를 열고 노브가 보내는 U/D 바이트를 ``listen_seconds`` 동안 기다린다."""
    import serial

    try:
        with serial.Serial(port=port, baudrate=baudrate, timeout=0.1) as ser:
            ser.reset_input_buffer()
            deadline = time.monotonic() + listen_seconds
            while time.monotonic() < deadline:
                chunk = ser.read(ser.in_waiting or 1)
                if not chunk:
                    continue
                text = chunk.decode(errors="ignore").upper()
                if "U" in text or "D" in text:
                    return True
    except Exception as exc:
        if emit:
            emit(f"  {port}: 열기 실패 ({exc})")
        return False
    return False


class PortScanWorker(QThread):
    """모든 COM 포트를 스캔해서 PCB 응답 포트와 노브 응답 포트를 찾는 백그라운드 워커."""

    progress = pyqtSignal(str)
    result = pyqtSignal(object, object)  # (pcb_port|None, knob_port|None)

    def __init__(
        self,
        pcb_baudrate: int,
        knob_baudrate: int,
        *,
        knob_listen_seconds: float = 4.0,
    ) -> None:
        super().__init__()
        self._pcb_baud = pcb_baudrate
        self._knob_baud = knob_baudrate
        self._knob_listen = knob_listen_seconds

    def run(self) -> None:  # noqa: D401 - QThread 진입점
        from pcb_connection_check import list_port_rows, port_label

        rows = list_port_rows()
        ports = [getattr(r, "device", "") for r in rows if getattr(r, "device", "")]
        if not ports:
            self.progress.emit("감지된 COM 포트가 없습니다. 케이블/전원을 확인하세요.")
            self.result.emit(None, None)
            return

        self.progress.emit(f"감지된 포트 {len(ports)}개:")
        for r in rows:
            self.progress.emit(f"  - {port_label(r)}")

        self.progress.emit("PCB 응답 포트 검색 중... (상태 요청 'S' 프레임 전송)")
        pcb_port: Optional[str] = None
        for p in ports:
            self.progress.emit(f"[PCB] {p} 확인 중...")
            if _detect_pcb_on_port(p, self._pcb_baud, emit=self.progress.emit):
                pcb_port = p
                self.progress.emit(f"[PCB] 응답 확인 → {p}")
                break
        if not pcb_port:
            self.progress.emit("[PCB] 응답하는 포트를 찾지 못했습니다.")

        candidates = [p for p in ports if p != pcb_port]
        knob_port: Optional[str] = None
        if candidates:
            self.progress.emit(
                f"노브 응답 포트 검색 중... 스캔하는 동안 볼륨 노브를 좌우로 계속 돌려주세요. "
                f"(포트당 최대 약 {self._knob_listen:.0f}초)"
            )
            for p in candidates:
                self.progress.emit(f"[노브] {p} 청취 중... (지금 노브를 돌리세요)")
                if _detect_knob_on_port(
                    p, self._knob_baud, listen_seconds=self._knob_listen, emit=self.progress.emit
                ):
                    knob_port = p
                    self.progress.emit(f"[노브] U/D 신호 확인 → {p}")
                    break
            if not knob_port:
                self.progress.emit(
                    "[노브] U/D 신호를 받은 포트가 없습니다. (스캔 중 노브를 돌렸는지 확인)"
                )

        self.result.emit(pcb_port, knob_port)


# ─── 카푸친 모카 컬러셋 ───
CTP_MOCHA = {
    "background": "#1E1E2E",
    "surface0": "#181825",
    "surface1": "#11111B",
    "surface2": "#313244",
    "text": "#CDD6F4",
    "subtext0": "#A6ADC8",
    "overlay0": "#6C7086",
    "overlay1": "#45475A",
    "lavender": "#B4BEFE",
    "sapphire": "#74C7EC",
    "sky": "#89DCEB",
    "green": "#A6E3A1",
    "red": "#F38BA8",
    "red_pressed": "#EBA0AC",
    "base": "#1E1E2E",
}

# macOS에는 Segoe UI가 없고, Windows에는 SF Pro가 없음 → 플랫폼별 스택으로 qt.qpa.fonts 경고·지연 방지
if sys.platform == "darwin":
    _UI_FONT = "'Helvetica Neue', 'Arial', sans-serif"
    _MONO_FONT = "'Menlo', 'Monaco', 'Consolas', monospace"
elif sys.platform == "win32":
    _UI_FONT = "'Segoe UI', 'Tahoma', sans-serif"
    _MONO_FONT = "'Consolas', 'Courier New', monospace"
else:
    _UI_FONT = "'Ubuntu', 'DejaVu Sans', 'Arial', sans-serif"
    _MONO_FONT = "'DejaVu Sans Mono', 'Consolas', monospace"

QSS = f"""
    QMainWindow {{ background-color: {CTP_MOCHA['background']}; }}
    QWidget {{ color: {CTP_MOCHA['text']}; font-family: {_UI_FONT}; font-size: 9pt; }}

    QFrame#ControlFrame {{ background-color: {CTP_MOCHA['surface0']}; border-radius: 8px; padding: 6px; }}
    QFrame#LogFrame {{ background-color: {CTP_MOCHA['surface1']}; border-radius: 8px; }}

    QLabel {{ color: {CTP_MOCHA['lavender']}; font-weight: bold; }}
    QLabel#SubTitle {{ color: {CTP_MOCHA['subtext0']}; font-weight: normal; }}

    QComboBox, QLineEdit, QSpinBox, QDoubleSpinBox {{
        background-color: {CTP_MOCHA['surface1']};
        border: 1px solid {CTP_MOCHA['overlay0']};
        border-radius: 5px; padding: 4px; color: {CTP_MOCHA['text']};
    }}
    QComboBox::drop-down {{ border: 0px; }}
    QComboBox QAbstractItemView {{ background-color: {CTP_MOCHA['surface1']}; selection-background-color: {CTP_MOCHA['overlay0']}; }}

    QPushButton {{
        border-radius: 5px;
        padding: 7px 10px;
        font-weight: bold;
        border: 1px solid transparent;
    }}

    /* Primary — 연결 (active: 강조 라벤더) */
    QPushButton#PrimaryBtn {{
        background-color: {CTP_MOCHA['lavender']};
        color: {CTP_MOCHA['base']};
        border-color: {CTP_MOCHA['lavender']};
    }}
    QPushButton#PrimaryBtn:hover {{
        background-color: {CTP_MOCHA['sapphire']};
        border-color: {CTP_MOCHA['sky']};
        color: {CTP_MOCHA['base']};
    }}
    QPushButton#PrimaryBtn:pressed {{
        background-color: {CTP_MOCHA['overlay0']};
        border-color: {CTP_MOCHA['overlay0']};
    }}
    QPushButton#PrimaryBtn:disabled {{
        background-color: {CTP_MOCHA['surface2']};
        color: {CTP_MOCHA['overlay1']};
        border-color: {CTP_MOCHA['overlay1']};
    }}

    /* Secondary — 보조·유틸 (inactive: 차분한 서피스) */
    QPushButton#SecondaryBtn {{
        background-color: {CTP_MOCHA['surface0']};
        color: {CTP_MOCHA['subtext0']};
        border: 1px solid {CTP_MOCHA['overlay0']};
        font-weight: normal;
    }}
    QPushButton#SecondaryBtn:hover {{
        background-color: {CTP_MOCHA['surface2']};
        color: {CTP_MOCHA['lavender']};
        border-color: {CTP_MOCHA['lavender']};
    }}
    QPushButton#SecondaryBtn:pressed {{
        background-color: {CTP_MOCHA['surface1']};
        color: {CTP_MOCHA['text']};
        border-color: {CTP_MOCHA['overlay0']};
    }}
    QPushButton#SecondaryBtn:disabled {{
        background-color: {CTP_MOCHA['surface1']};
        color: {CTP_MOCHA['overlay1']};
        border-color: {CTP_MOCHA['surface2']};
    }}

    /* Disconnect — 끊기 (active: 레드 강조) */
    QPushButton#DisconnectBtn {{
        background-color: {CTP_MOCHA['surface2']};
        color: {CTP_MOCHA['red']};
        border: 1px solid {CTP_MOCHA['red']};
        font-weight: bold;
    }}
    QPushButton#DisconnectBtn:hover {{
        background-color: {CTP_MOCHA['red']};
        color: {CTP_MOCHA['base']};
        border-color: {CTP_MOCHA['red_pressed']};
    }}
    QPushButton#DisconnectBtn:pressed {{
        background-color: {CTP_MOCHA['red_pressed']};
        color: {CTP_MOCHA['base']};
    }}
    QPushButton#DisconnectBtn:disabled {{
        background-color: {CTP_MOCHA['surface1']};
        color: {CTP_MOCHA['overlay1']};
        border-color: {CTP_MOCHA['surface2']};
    }}

    QGroupBox {{
        color: {CTP_MOCHA['lavender']};
        font-weight: bold;
        border: 1px solid {CTP_MOCHA['overlay0']};
        border-radius: 6px;
        margin-top: 6px;
        padding-top: 6px;
    }}
    QGroupBox::title {{ subcontrol-origin: margin; left: 10px; padding: 0 4px; }}

    QCheckBox {{ color: {CTP_MOCHA['text']}; font-weight: normal; }}

    QTextEdit {{
        background-color: transparent; border: 0px;
        color: {CTP_MOCHA['text']}; font-family: {_MONO_FONT}; font-size: 8pt;
    }}
"""


def _webview_subprocess_cmd(port: str, baud: int) -> list[str]:
    """WebView 전용 자식 프로세스 명령 (해당 프로세스 main thread 에서 pywebview 실행)."""
    args = ["--run-webview", port, str(baud)]
    if getattr(sys, "frozen", False):
        return [sys.executable, *args]
    return [sys.executable, str(Path(__file__).resolve()), *args]


def _main_subprocess_cmd() -> list[str]:
    """GUI는 설정만 저장하고 실제 실행은 main.py 진입점에 위임한다."""
    if getattr(sys, "frozen", False):
        return [sys.executable, "--run-main"]
    return [sys.executable, str(Path(__file__).resolve().with_name("main.py"))]


def _run_main_cli() -> int:
    """``--run-main`` — frozen GUI exe 안에서 main.py의 main() 로직만 실행."""
    setup_system_logging(role="main")
    from main import main as run_main

    return int(run_main())


def _run_webview_cli(port: str, baud: int) -> int:
    """``--run-webview`` — PyQt 없이 통합 WebView 만 main thread 에서 실행."""
    setup_system_logging(role="webview")
    log = logging.getLogger("gui_main.webview")

    # ysoh 2026-06-13: macOS(darwin)도 허용
    if sys.platform not in ("win32", "darwin"):
        log.error("WEBVIEW_ENABLED 모드는 Windows 또는 macOS에서만 지원합니다.")
        return 1

    from kiosk_module.webview_app import run_integrated_app

    log.info("WebView 프로세스 시작 (main thread): port=%r baud=%s", port, baud)
    log.info("적용 config: %s", config)
    try:
        rc = run_integrated_app(port, baud)
        log.info("WebView 프로세스 정상 종료 exit=%s", rc)
        return rc
    except Exception:
        log.exception("WebView 프로세스 비정상 종료")
        return 1


def _float_spin(v: float, min_v: float, max_v: float, step: float) -> QDoubleSpinBox:
    s = QDoubleSpinBox()
    s.setRange(min_v, max_v)
    s.setSingleStep(step)
    s.setDecimals(1)
    s.setValue(float(v))
    return s


def _configure_form_layout(form: QFormLayout) -> None:
    form.setLabelAlignment(Qt.AlignLeft)
    form.setFormAlignment(Qt.AlignLeft | Qt.AlignTop)
    form.setFieldGrowthPolicy(QFormLayout.AllNonFixedFieldsGrow)
    form.setRowWrapPolicy(QFormLayout.WrapLongRows)
    form.setHorizontalSpacing(8)
    form.setVerticalSpacing(6)


class KioskApp(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("JDONE Kiosk — 환경 변수 & 연결")
        self.resize(540, 900)
        self.setMinimumSize(360, 520)

        self._webview_proc: Optional[subprocess.Popen] = None
        self._scan_worker: Optional[PortScanWorker] = None
        self._webview_poll = QTimer(self)
        self._webview_poll.timeout.connect(self._poll_webview_process)
        # WebView 비정상 종료 시 자동 재시작용 상태
        self._webview_last_args: Optional[tuple[str, int]] = None
        self._webview_user_stop = False
        self._webview_restart_count = 0
        self._webview_restart_max = 5
        self._system_log_path = system_log_path()
        self._system_log_offset = (
            self._system_log_path.stat().st_size
            if self._system_log_path.is_file()
            else 0
        )

        main_widget = QWidget()
        self.setCentralWidget(main_widget)
        main_layout = QVBoxLayout(main_widget)
        main_layout.setContentsMargins(12, 12, 12, 12)
        main_layout.setSpacing(10)

        control_frame = QFrame()
        control_frame.setObjectName("ControlFrame")
        control_layout = QVBoxLayout(control_frame)
        control_layout.setSpacing(8)
        control_layout.setContentsMargins(10, 10, 10, 10)

        self.autostart_cb = QCheckBox("재부팅 후 자동 실행")
        if can_manage_autostart():
            self.autostart_cb.setChecked(is_autostart_enabled())
            self.autostart_cb.setToolTip(
                "켜면 OS 로그인(부팅) 시 이 프로그램이 자동으로 실행됩니다.\n"
                "Windows: 로그인 항목 등록 · Linux: ~/.config/autostart/"
            )
        else:
            self.autostart_cb.setEnabled(False)
            self.autostart_cb.setToolTip("이 OS에서는 부팅 자동 실행을 지원하지 않습니다.")
        control_layout.addWidget(self.autostart_cb)

        title_label = QLabel("제어 모듈 설정")
        title_label.setStyleSheet("font-size: 12pt;")
        control_layout.addWidget(title_label)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        scroll_inner = QWidget()
        scroll.setWidget(scroll_inner)
        form_root = QVBoxLayout(scroll_inner)
        form_root.setContentsMargins(0, 0, 0, 0)
        form_root.setSpacing(8)

        # ─── 시리얼 ───
        serial_box = QGroupBox("시리얼")
        serial_outer = QVBoxLayout(serial_box)
        port_row = QHBoxLayout()
        port_row.addWidget(QLabel("포트:"))
        self.port_combo = QComboBox()
        self.port_combo.setEditable(True)
        port_row.addWidget(self.port_combo, stretch=1)
        self.refresh_ports_btn = QPushButton("목록 새로고침")
        self.refresh_ports_btn.setObjectName("SecondaryBtn")
        self.refresh_ports_btn.setToolTip(
            "키워드가 비어 있으면 모든 시리얼 포트를, 있으면 설명에 키워드가 들어간 포트만 드롭다운에 채웁니다."
        )
        port_row.addWidget(self.refresh_ports_btn)
        self.scan_ports_btn = QPushButton("PCB·노브 자동 찾기")
        self.scan_ports_btn.setObjectName("SecondaryBtn")
        self.scan_ports_btn.setToolTip(
            "감지된 모든 COM 포트에 PCB 상태 요청을 보내 응답하는 포트를 찾고,\n"
            "이어서 노브를 돌리는 동안 U/D 신호를 보내는 포트를 찾아 자동으로 선택합니다.\n"
            "실행(연결) 중에는 포트가 사용 중이라 스캔할 수 없습니다."
        )
        port_row.addWidget(self.scan_ports_btn)
        serial_outer.addLayout(port_row)

        kw_row = QHBoxLayout()
        kw_row.addWidget(QLabel("포트 설명 필터:"))
        self.keyword_edit = QLineEdit()
        self.keyword_edit.setReadOnly(True)
        self.keyword_edit.setToolTip("포트 자동 검색 키워드는 USB로 고정됩니다.")
        kw_row.addWidget(self.keyword_edit, stretch=1)
        serial_outer.addLayout(kw_row)

        auto_hint = QLabel(
            "연결 시 선택한 USB 장치의 VID/PID(·시리얼)를 .env에 저장합니다. "
            "리부트 후 COM 번호가 바뀌어도 같은 장치로 자동 연결됩니다. "
            "포트가 비어 있거나 AUTO이면 설명 필터에 맞는 첫 장치를 씁니다."
        )
        auto_hint.setObjectName("SubTitle")
        auto_hint.setWordWrap(True)
        serial_outer.addWidget(auto_hint)

        form_root.addWidget(serial_box)

        # ─── .env 필드 (Config와 1:1) ───
        env_box = QGroupBox(
            "환경 변수 (연결 시 config에 반영 · 연결 후 아래에 실제 적용값이 표시됨)"
        )
        env_form = QFormLayout(env_box)
        _configure_form_layout(env_form)

        self.device_type_combo = QComboBox()
        self.device_type_combo.setEditable(True)
        for value in ("KIOSK", "SMART_POLE", "POLE_N_ED"):
            self.device_type_combo.addItem(value, value)
        env_form.addRow("ASSET_DEVICE_TYPE:", self.device_type_combo)

        self.device_id_edit = QLineEdit()
        env_form.addRow("DEVICE_ID:", self.device_id_edit)

        self.base_url_edit = QLineEdit()
        env_form.addRow("BASE_URL:", self.base_url_edit)

        self.default_url_edit = QLineEdit()
        env_form.addRow("DEFAULT_URL:", self.default_url_edit)

        self.device_api_base_url_edit = QLineEdit()
        env_form.addRow("DEVICE_API_BASE_URL:", self.device_api_base_url_edit)

        self.device_api_timeout_spin = _float_spin(10.0, 0.1, 120.0, 0.5)
        env_form.addRow("DEVICE_API_TIMEOUT:", self.device_api_timeout_spin)

        self.websocket_addr_edit = QLineEdit()
        env_form.addRow("WEBSOCKET_ADDR:", self.websocket_addr_edit)

        self.ws_reconnect_spin = _float_spin(5.0, 0.5, 600.0, 1.0)
        env_form.addRow("WS_RECONNECT_INTERVAL:", self.ws_reconnect_spin)

        self.status_poll_spin = _float_spin(600.0, 60.0, 86400.0, 60.0)
        env_form.addRow("STATUS_POLL_INTERVAL:", self.status_poll_spin)

        self.vacant_idle_spin = _float_spin(20.0, 1.0, 3600.0, 5.0)
        env_form.addRow("VACANT_IDLE_CLOSE_SECONDS:", self.vacant_idle_spin)

        self.browser_timeout_spin = QSpinBox()
        self.browser_timeout_spin.setRange(1, 86400)
        env_form.addRow("BACKGROUND_BROWSER_TIMEOUT_SECONDS:", self.browser_timeout_spin)

        self.log_level_combo = QComboBox()
        for lv in ("DEBUG", "INFO", "WARNING", "ERROR"):
            self.log_level_combo.addItem(lv)
        env_form.addRow("LOG_LEVEL:", self.log_level_combo)

        form_root.addWidget(env_box)

        # ─── 볼륨 노브 시리얼 ───
        volume_box = QGroupBox("볼륨 노브 시리얼")
        volume_form = QFormLayout(volume_box)
        _configure_form_layout(volume_form)

        volume_port_widget = QWidget()
        volume_port_layout = QHBoxLayout(volume_port_widget)
        volume_port_layout.setContentsMargins(0, 0, 0, 0)
        volume_port_layout.setSpacing(6)
        self.volume_port_combo = QComboBox()
        self.volume_port_combo.setEditable(True)
        volume_port_layout.addWidget(self.volume_port_combo, stretch=1)
        self.refresh_volume_ports_btn = QPushButton("새로고침")
        self.refresh_volume_ports_btn.setObjectName("SecondaryBtn")
        self.refresh_volume_ports_btn.setToolTip("볼륨 노브용 시리얼 포트 목록을 다시 읽습니다.")
        volume_port_layout.addWidget(self.refresh_volume_ports_btn)
        volume_form.addRow("VOLUME_SERIAL_PORT:", volume_port_widget)

        form_root.addWidget(volume_box)

        # ─── 통합 WebView (Windows / macOS) ───  # ysoh 2026-06-13
        webview_box = QGroupBox("통합 WebView (Windows / macOS)")
        webview_form = QFormLayout(webview_box)
        _configure_form_layout(webview_form)

        self.webview_enabled_cb = QCheckBox("WEBVIEW_ENABLED")
        self.webview_enabled_cb.setToolTip(
            "켜면 연결 시 설정 GUI 는 이 창에 두고, 통합 WebView(전체화면)는 "
            "별도 프로세스 main thread 에서 실행됩니다. Windows / macOS 지원."
        )
        webview_form.addRow(self.webview_enabled_cb)

        self.webview_devtools_cb = QCheckBox("WEBVIEW_DEVTOOLS")
        webview_form.addRow(self.webview_devtools_cb)

        form_root.addWidget(webview_box)

        form_root.addStretch()

        control_layout.addWidget(scroll)

        btn_layout = QHBoxLayout()
        self.connect_btn = QPushButton("연결")
        self.connect_btn.setObjectName("PrimaryBtn")
        self.disconnect_btn = QPushButton("끊기")
        self.disconnect_btn.setObjectName("DisconnectBtn")
        self.disconnect_btn.setEnabled(False)
        btn_layout.addWidget(self.connect_btn)
        btn_layout.addWidget(self.disconnect_btn)
        control_layout.addLayout(btn_layout)

        # .env 저장 (frozen .exe 에서도 .env 위치를 사용자 디렉터리로 잡아둠)
        util_layout = QHBoxLayout()
        self.save_env_btn = QPushButton(".env 저장")
        self.save_env_btn.setObjectName("SecondaryBtn")
        self.save_env_btn.setToolTip(
            f"현재 화면 값을 .env 파일에 저장합니다:\n{user_env_path()}"
        )
        util_layout.addWidget(self.save_env_btn)
        self.meetone_test_btn = QPushButton("MeetOne 버튼 가정")
        self.meetone_test_btn.setObjectName("SecondaryBtn")
        self.meetone_test_btn.setToolTip(
            "실행 중인 main.py 프로세스에 오른쪽 PCB 버튼 이벤트를 가정해 전달합니다."
        )
        util_layout.addWidget(self.meetone_test_btn)
        util_layout.addStretch()
        control_layout.addLayout(util_layout)

        main_layout.addWidget(control_frame, stretch=4)

        log_frame = QFrame()
        log_frame.setObjectName("LogFrame")
        log_layout = QVBoxLayout(log_frame)
        log_layout.setContentsMargins(10, 10, 10, 10)

        log_header = QHBoxLayout()
        log_header.addWidget(QLabel("통신 로그"))
        sub = QLabel("로그: 하단 고정폭 8pt")
        sub.setObjectName("SubTitle")
        log_header.addWidget(sub, 0, Qt.AlignRight)
        log_layout.addLayout(log_header)

        self.log_text = QTextEdit()
        self.log_text.setReadOnly(True)
        self.log_text.setLineWrapMode(QTextEdit.WidgetWidth)
        self.log_text.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.log_text.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self.log_text.setMinimumHeight(130)
        self.log_text.append(
            "[INFO] 환경 변수를 입력한 뒤 연결을 누르면 실행 중 config에 반영됩니다."
        )
        self.log_text.append(
            f"[INFO] 시스템 로그 파일: {self._system_log_path}"
        )
        log_layout.addWidget(self.log_text)

        main_layout.addWidget(log_frame, stretch=2)

        self.setStyleSheet(QSS)

        self.refresh_ports_btn.clicked.connect(lambda *_: self._populate_ports())
        self.scan_ports_btn.clicked.connect(lambda *_: self._on_scan_ports())
        self.refresh_volume_ports_btn.clicked.connect(
            lambda *_: self._populate_volume_ports()
        )
        self.autostart_cb.stateChanged.connect(self._on_autostart_toggled)
        self.connect_btn.clicked.connect(self._on_connect)
        self.disconnect_btn.clicked.connect(self._on_disconnect)
        self.save_env_btn.clicked.connect(self._on_save_env)
        self.meetone_test_btn.clicked.connect(self._on_meetone_test_button)
        self._sync_ui_from_config()

    def _sync_ui_from_config(self) -> None:
        """메모리 ``config``에 들어 있는 값(실제 사용·적용 기준)을 위젯에 그대로 표시."""
        self.keyword_edit.setText(config.serial_port_description_keyword)
        stored_usb_port = SerialManager.find_port_by_usb(
            config.serial_usb_vid or None,
            config.serial_usb_pid or None,
            config.serial_usb_serial or None,
        )
        select_port = stored_usb_port or config.serial_port
        self._populate_ports(select_device=select_port)
        self._select_device_type(config.asset_device_type)
        self.device_id_edit.setText(config.device_id)
        self.base_url_edit.setText(config.base_url)
        self.default_url_edit.setText(config.default_url)
        self.device_api_base_url_edit.setText(os.getenv("DEVICE_API_BASE_URL", ""))
        self.device_api_timeout_spin.setValue(
            float(os.getenv("DEVICE_API_TIMEOUT", "10.0"))
        )
        self.websocket_addr_edit.setText(config.websocket_addr)
        self.ws_reconnect_spin.setValue(float(config.ws_reconnect_interval))
        self.status_poll_spin.setValue(
            float(os.getenv("STATUS_POLL_INTERVAL", "600"))
        )
        self.webview_enabled_cb.setChecked(config.webview_enabled)
        self.webview_devtools_cb.setChecked(config.webview_devtools)
        self.vacant_idle_spin.setValue(float(config.vacant_idle_close_seconds))
        self.browser_timeout_spin.setValue(
            int(config.background_browser_timeout_seconds)
        )
        self._populate_volume_ports(select_device=config.volume_serial_port)
        lv = (config.log_level or "INFO").strip().upper()
        idx = self.log_level_combo.findText(lv, Qt.MatchFixedString)
        self.log_level_combo.setCurrentIndex(max(0, idx))

    def _select_device_type(self, value: str) -> None:
        v = (value or "").strip().upper() or "KIOSK"
        for i in range(self.device_type_combo.count()):
            if self.device_type_combo.itemData(i) == v:
                self.device_type_combo.setCurrentIndex(i)
                return
        self.device_type_combo.setEditText(v)

    def _resolved_device_type(self) -> str:
        data = self.device_type_combo.currentData()
        if isinstance(data, str) and data.strip():
            return data.strip().upper()
        return self.device_type_combo.currentText().strip().upper() or "KIOSK"

    def _push_ui_to_config(self) -> None:
        """위젯 값 → 전역 ``config`` (디스크 .env는 수정하지 않음)."""
        config.asset_device_type = self._resolved_device_type()
        config.device_id = self.device_id_edit.text().strip()
        config.base_url = self.base_url_edit.text().strip()
        config.default_url = self.default_url_edit.text().strip()
        config.websocket_addr = self.websocket_addr_edit.text().strip()
        if config.serial_usb_vid and config.serial_usb_pid:
            config.serial_port = "AUTO"
        else:
            config.serial_port = self._resolved_port()
        config.serial_baudrate = 115200
        config.serial_port_description_keyword = "USB"
        config.ws_enabled = True
        config.ws_reconnect_interval = float(self.ws_reconnect_spin.value())
        config.webview_enabled = self.webview_enabled_cb.isChecked()
        config.webview_ws_url = config.websocket_addr
        config.webview_devtools = self.webview_devtools_cb.isChecked()
        config.webview_tray_enabled = True
        config.vacant_idle_close_seconds = float(self.vacant_idle_spin.value())
        config.input_monitor_enabled = True
        config.kiosk_browser_cmd = ""
        config.background_browser_timeout_seconds = float(
            self.browser_timeout_spin.value()
        )
        config.volume_serial_enabled = config.asset_device_type == "KIOSK"
        config.volume_serial_port = self._resolved_volume_port()
        config.volume_serial_baudrate = 38400
        config.volume_up_hex_codes = frozenset()
        config.volume_down_hex_codes = frozenset()
        config.log_level = self.log_level_combo.currentText().strip().upper() or "INFO"
        config.test_mode_enabled = False
        lvl = getattr(logging, config.log_level, logging.INFO)
        logging.getLogger().setLevel(lvl)

    def _append_log(self, text: str) -> None:
        self.log_text.append(text)
        self._scroll_log_to_end()

    def _append_raw_log_lines(self, chunk: str) -> None:
        """파일에서 읽은 로그 줄(타임스탬프 포함)을 그대로 표시."""
        for line in chunk.splitlines():
            if line.strip():
                self.log_text.append(line)
        if chunk.strip():
            self._scroll_log_to_end()

    def _scroll_log_to_end(self) -> None:
        self.log_text.verticalScrollBar().setValue(
            self.log_text.verticalScrollBar().maximum()
        )

    def _sync_system_log_to_ui(self) -> None:
        """시스템 로그 파일의 신규 내용을 GUI 로그 패널에 반영."""
        chunk, self._system_log_offset = read_log_from_offset(
            self._system_log_path, self._system_log_offset
        )
        if chunk:
            self._append_raw_log_lines(chunk)

    def _show_webview_log_tail(self, *, max_lines: int = 30) -> None:
        tail = read_log_tail(self._system_log_path, max_lines=max_lines)
        if not tail:
            self._append_log(
                "[WARN] WebView 시스템 로그가 비어 있습니다. "
                f"파일을 확인하세요: {self._system_log_path}"
            )
            return
        self._append_log("[INFO] ---- WebView 시스템 로그 (최근) ----")
        for line in tail:
            self.log_text.append(line)
        self._scroll_log_to_end()
        self._append_log(f"[INFO] 전체 로그: {self._system_log_path}")

    def _populate_ports(self, select_device: Optional[str] = None) -> None:
        if select_device is not None:
            current = select_device
        else:
            current = self.port_combo.currentData()
            if current is None and self.port_combo.currentText():
                current = self.port_combo.currentText().strip()
        kw = self.keyword_edit.text().strip()
        self.port_combo.clear()
        for dev, label in SerialManager.list_port_entries_filtered(kw or None):
            self.port_combo.addItem(label, dev)
        if current:
            self._select_port_if_present(str(current))
        elif self.port_combo.count() == 0:
            self.port_combo.addItem("(포트 없음)", "")

    def _populate_volume_ports(self, select_device: Optional[str] = None) -> None:
        if select_device is not None:
            current = select_device
        else:
            current = self.volume_port_combo.currentData()
            if current is None and self.volume_port_combo.currentText():
                current = self.volume_port_combo.currentText().strip()
        self.volume_port_combo.clear()
        for dev, label in SerialManager.list_port_entries_filtered(None):
            self.volume_port_combo.addItem(label, dev)
        if current:
            for i in range(self.volume_port_combo.count()):
                if self.volume_port_combo.itemData(i) == current:
                    self.volume_port_combo.setCurrentIndex(i)
                    return
            self.volume_port_combo.setEditText(str(current))
        elif self.volume_port_combo.count() == 0:
            self.volume_port_combo.addItem("(포트 없음)", "")

    def _select_port_if_present(self, device: str) -> None:
        for i in range(self.port_combo.count()):
            if self.port_combo.itemData(i) == device:
                self.port_combo.setCurrentIndex(i)
                return
        self.port_combo.setEditText(device)

    def _on_scan_ports(self) -> None:
        """모든 COM 포트를 스캔해 PCB·노브 포트를 백그라운드 스레드에서 찾는다."""
        if self._is_session_active():
            QMessageBox.information(
                self,
                "PCB·노브 자동 찾기",
                "실행 중에는 COM 포트가 사용 중이라 스캔할 수 없습니다.\n"
                "먼저 중지(연결 해제)한 뒤 다시 시도하세요.",
            )
            return
        if self._scan_worker is not None and self._scan_worker.isRunning():
            return

        pcb_baud = int(config.serial_baudrate or 115200)
        knob_baud = int(config.volume_serial_baudrate or 38400)

        self._append_log("[INFO] ---- PCB·노브 포트 자동 찾기 시작 ----")
        self._append_log(
            f"[INFO] PCB {pcb_baud}bps · 노브 {knob_baud}bps 로 스캔합니다. "
            "노브 검색 단계에서는 볼륨 노브를 좌우로 돌려주세요."
        )
        self._set_scanning_ui(True)

        worker = PortScanWorker(pcb_baud, knob_baud)
        worker.progress.connect(lambda m: self._append_log(f"[SCAN] {m}"))
        worker.result.connect(self._on_scan_finished)
        worker.finished.connect(lambda: self._set_scanning_ui(False))
        self._scan_worker = worker
        worker.start()

    def _on_scan_finished(self, pcb_port: Optional[str], knob_port: Optional[str]) -> None:
        if pcb_port:
            self._populate_ports(select_device=pcb_port)
            self._append_log(f"[INFO] PCB 포트 자동 선택 → {pcb_port}")
        else:
            self._append_log("[WARN] PCB 응답 포트를 찾지 못했습니다.")

        if knob_port:
            self._populate_volume_ports(select_device=knob_port)
            self._append_log(
                f"[INFO] 노브 포트 자동 선택 → {knob_port}"
            )
        else:
            self._append_log(
                "[INFO] 노브 포트는 찾지 못했습니다. (스캔 중 노브를 돌렸는지 / 연결을 확인)"
            )

        if pcb_port or knob_port:
            QMessageBox.information(
                self,
                "PCB·노브 자동 찾기",
                f"PCB 포트: {pcb_port or '미발견'}\n"
                f"노브 포트: {knob_port or '미발견'}\n\n"
                "필요하면 '연결'을 눌러 적용하세요.",
            )
        else:
            QMessageBox.warning(
                self,
                "PCB·노브 자동 찾기",
                "PCB·노브 응답 포트를 찾지 못했습니다.\n"
                "케이블·전원, 그리고 스캔 중 노브를 돌렸는지 확인하세요.",
            )

    def _set_scanning_ui(self, scanning: bool) -> None:
        self.scan_ports_btn.setEnabled(not scanning)
        self.scan_ports_btn.setText("스캔 중..." if scanning else "PCB·노브 자동 찾기")
        self.refresh_ports_btn.setEnabled(not scanning)
        self.refresh_volume_ports_btn.setEnabled(not scanning)
        self.connect_btn.setEnabled(not scanning)
        self.port_combo.setEnabled(not scanning)
        self.volume_port_combo.setEnabled(not scanning)

    def _resolved_port(self) -> str:
        dev = self.port_combo.currentData()
        if dev:
            return str(dev)
        return self.port_combo.currentText().strip()

    def _resolved_volume_port(self) -> str:
        dev = self.volume_port_combo.currentData()
        if dev:
            return str(dev)
        text = self.volume_port_combo.currentText().strip()
        return "" if text == "(포트 없음)" else text

    def _resolve_connect_port(self) -> Optional[str]:
        """연결에 쓸 시리얼 포트. 실패 시 ``None``, 스마트폴·웹뷰 전용은 ``""``."""
        if not config.pcb_control_enabled:
            self._append_log(
                f"[INFO] ASSET_DEVICE_TYPE={config.asset_device_type} — "
                "스마트폴 모드, PCB 시리얼 연결을 생략합니다."
            )
            return ""

        if config.test_mode_enabled:
            self._append_log(
                "[INFO] TEST_MODE_ENABLED=true — 가짜 시리얼 사용 (포트 검색 생략)"
            )
            return "FAKE"

        raw = self._resolved_port()
        kw = config.serial_port_description_keyword or "USB"
        usb_vid = config.serial_usb_vid or None
        usb_pid = config.serial_usb_pid or None
        usb_serial = config.serial_usb_serial or None
        by_usb = SerialManager.find_port_by_usb(usb_vid, usb_pid, usb_serial)
        port = SerialManager.resolve_port_choice(
            raw,
            kw,
            usb_vid=usb_vid,
            usb_pid=usb_pid,
            usb_serial=usb_serial,
        )
        if port is None:
            if usb_vid and usb_pid:
                QMessageBox.warning(
                    self,
                    "USB 장치 검색 실패",
                    "저장된 USB 장치를 찾을 수 없습니다.\n"
                    f"VID:PID={usb_vid}:{usb_pid}"
                    f"{(' SER=' + usb_serial) if usb_serial else ''}\n"
                    "케이블·전원을 확인하거나 포트를 다시 선택하세요.",
                )
            else:
                QMessageBox.warning(
                    self,
                    "자동 포트 검색 실패",
                    "포트를 비우거나 AUTO로 두면 키워드로 시리얼을 찾습니다.\n"
                    f"지금 키워드 {kw!r}에 맞는 포트가 없습니다. 키워드를 바꾸거나 "
                    "목록에서 장치를 직접 선택하세요.",
                )
            return None
        if by_usb and port == by_usb:
            self._select_port_if_present(port)
            ser_part = f" SER={usb_serial}" if usb_serial else ""
            self._append_log(
                f"[INFO] 저장된 USB 장치(VID:PID={usb_vid}:{usb_pid}{ser_part}) → {port}"
            )
        else:
            auto_picked = (raw or "").strip().upper() == "AUTO" or not (raw or "").strip()
            if auto_picked:
                self._select_port_if_present(port)
                self._append_log(f"[INFO] 자동 포트 검색(키워드 {kw!r}) → {port}")
        config.serial_port = port
        return port

    def _bind_usb_identity_from_port(self, port: str) -> None:
        """연결한 포트의 USB 식별값을 config·.env에 남깁니다."""
        if not port or port == "FAKE":
            return
        vid, pid, ser = SerialManager.get_port_usb_fields(port)
        if vid is None or pid is None:
            self._append_log(
                f"[WARN] {port} 의 USB VID/PID를 읽지 못했습니다. "
                "COM 번호만 저장됩니다."
            )
            return
        config.serial_usb_vid = f"{vid:04X}"
        config.serial_usb_pid = f"{pid:04X}"
        config.serial_usb_serial = ser or ""
        config.serial_port = "AUTO"
        ser_part = f" SER={ser}" if ser else ""
        self._append_log(
            f"[INFO] USB 장치 기억: "
            f"VID:PID={config.serial_usb_vid}:{config.serial_usb_pid}{ser_part}"
        )

    def _on_autostart_toggled(self, state: int) -> None:
        enabled = state == Qt.Checked
        ok, msg = set_autostart_enabled(enabled)
        level = "INFO" if ok else "ERROR"
        self._append_log(f"[{level}] {msg.replace(chr(10), ' ')}")
        if not ok:
            self.autostart_cb.blockSignals(True)
            self.autostart_cb.setChecked(not enabled)
            self.autostart_cb.blockSignals(False)
            QMessageBox.warning(self, "자동 실행", msg)

    def _set_connecting_ui(self, enabled: bool) -> None:
        self.connect_btn.setEnabled(not enabled)
        self.disconnect_btn.setEnabled(enabled)
        self.refresh_ports_btn.setEnabled(not enabled)
        self.scan_ports_btn.setEnabled(not enabled)
        self.refresh_volume_ports_btn.setEnabled(not enabled)
        self.keyword_edit.setEnabled(not enabled)
        self.port_combo.setEnabled(not enabled)
        self.volume_port_combo.setEnabled(not enabled)

    def _is_session_active(self) -> bool:
        if self._webview_proc is not None and self._webview_proc.poll() is None:
            return True
        return False

    def _launch_main_process(self) -> None:
        """설정 GUI는 이 프로세스에 두고, 실제 실행은 main.py 자식 프로세스에 맡긴다."""
        if not self._persist_env():
            QMessageBox.critical(
                self,
                "실행",
                ".env 저장에 실패해 main.py 프로세스를 시작할 수 없습니다.",
            )
            return

        self._system_log_offset = (
            self._system_log_path.stat().st_size
            if self._system_log_path.is_file()
            else 0
        )

        cmd = _main_subprocess_cmd()
        child_env = os.environ.copy()
        for key in DEPRECATED_GUI_ENV_KEYS:
            child_env.pop(key, None)
        child_env.update({key: value for key, value in self._env_pairs()})
        try:
            self._webview_proc = subprocess.Popen(cmd, env=child_env)
        except Exception as exc:
            self._append_log(f"[ERROR] main.py 프로세스 시작 실패: {exc}")
            QMessageBox.critical(self, "실행", str(exc))
            return

        # 자동 재시작을 위해 마지막 실행 인자/플래그 기록
        self._webview_last_args = ("", int(config.serial_baudrate))
        self._webview_user_stop = False

        self._set_connecting_ui(True)
        self._webview_poll.start(1000)
        self._append_log("[INFO] main.py 별도 프로세스 시작")
        self._append_log(f"[INFO] main.py 명령: {' '.join(cmd)}")
        self._append_log(f"[INFO] 실행 로그: {self._system_log_path}")
        self._append_log(f"[INFO] 적용된 설정: {config}")

    def _poll_webview_process(self) -> None:
        self._sync_system_log_to_ui()
        if self._webview_proc is None:
            self._webview_poll.stop()
            return
        rc = self._webview_proc.poll()
        if rc is None:
            return
        self._sync_system_log_to_ui()
        self._append_log(f"[INFO] main.py 프로세스 종료 (exit={rc})")
        self._show_webview_log_tail()
        self._webview_proc = None
        self._webview_poll.stop()

        # 사용자가 직접 종료한 게 아니면(=창이 죽음) 자동 재시작
        if (
            not self._webview_user_stop
            and self._webview_last_args is not None
            and self._webview_restart_count < self._webview_restart_max
        ):
            self._webview_restart_count += 1
            port, baud = self._webview_last_args
            self._append_log(
                "[WARN] main.py 프로세스가 비정상 종료됨 → 자동 재시작 "
                f"({self._webview_restart_count}/{self._webview_restart_max})"
            )
            QTimer.singleShot(1500, self._launch_main_process)
            return

        if self._webview_restart_count >= self._webview_restart_max:
            self._append_log(
                "[ERROR] main.py 자동 재시작 한도 초과 — 더 이상 재시작하지 않습니다."
            )
        self._set_connecting_ui(False)

    def _stop_webview_process(self) -> None:
        proc = self._webview_proc
        # 사용자 의도 종료 표시 → 자동 재시작 방지
        self._webview_user_stop = True
        if proc is None or proc.poll() is not None:
            return
        self._append_log("[INFO] main.py 프로세스 종료 요청…")
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=3)
        self._webview_proc = None
        self._webview_poll.stop()
        self._set_connecting_ui(False)
        self._sync_system_log_to_ui()
        self._append_log("[INFO] main.py 프로세스 종료")
        self._show_webview_log_tail()

    def _on_connect(self) -> None:
        self._push_ui_to_config()
        self._sync_ui_from_config()

        if config.volume_serial_enabled:
            volume_port = (config.volume_serial_port or "").strip()
            if not volume_port:
                QMessageBox.warning(
                    self,
                    "볼륨 노브 포트",
                    "키오스크 모드에서는 볼륨 노브 시리얼이 항상 켜집니다.\n"
                    "볼륨 노브용 COM 포트를 선택해 주세요.",
                )
                return
            serial_port = (config.serial_port or "").strip()
            if serial_port and volume_port.upper() == serial_port.upper():
                QMessageBox.warning(
                    self,
                    "볼륨 노브 포트",
                    "PCB 제어 포트와 볼륨 노브 포트가 같습니다.\n"
                    "하나의 COM 포트는 동시에 두 기능에서 열 수 없으니 서로 다른 포트를 선택해 주세요.",
                )
                return

        if self._is_session_active():
            return

        self._webview_restart_count = 0
        self._launch_main_process()

    def _on_disconnect(self) -> None:
        if self._webview_proc is not None and self._webview_proc.poll() is None:
            self._stop_webview_process()
            return

    def _env_pairs(self) -> list[tuple[str, str]]:
        return [
            ("ASSET_DEVICE_TYPE", config.asset_device_type or "KIOSK"),
            ("DEVICE_ID", config.device_id or ""),
            ("BASE_URL", config.base_url or ""),
            ("DEFAULT_URL", config.default_url or ""),
            ("DEVICE_API_BASE_URL", self.device_api_base_url_edit.text().strip()),
            ("DEVICE_API_TIMEOUT", f"{float(self.device_api_timeout_spin.value()):g}"),
            ("WEBSOCKET_ADDR", config.websocket_addr or ""),
            ("SERIAL_PORT", config.serial_port or ""),
            ("VOLUME_SERIAL_PORT", config.volume_serial_port or ""),
            ("WS_RECONNECT_INTERVAL", f"{float(config.ws_reconnect_interval):g}"),
            ("WEBVIEW_ENABLED", "true" if config.webview_enabled else "false"),
            ("WEBVIEW_DEVTOOLS", "true" if config.webview_devtools else "false"),
            ("VACANT_IDLE_CLOSE_SECONDS", f"{float(config.vacant_idle_close_seconds):g}"),
            ("INPUT_MONITOR_ENABLED", "true" if config.input_monitor_enabled else "false"),
            ("STATUS_POLL_INTERVAL", f"{float(self.status_poll_spin.value()):g}"),
            ("BACKGROUND_BROWSER_TIMEOUT_SECONDS", str(int(config.background_browser_timeout_seconds))),
            ("LOG_LEVEL", config.log_level or "INFO"),
        ]

    def _persist_env(self) -> bool:
        """현재 ``config`` 를 사용자 ``.env`` 파일에 기록한다."""
        self._push_ui_to_config()
        env_path = user_env_path()
        env_path.parent.mkdir(parents=True, exist_ok=True)
        pairs = self._env_pairs()

        # 기존 .env 의 다른 키(GUI 가 모르는 항목)는 보존하기 위해 머지.
        # python-dotenv 의 dotenv_values 로 읽고, GUI 가 다룬 키만 덮어쓴다.
        from dotenv import dotenv_values

        existing = dotenv_values(env_path) if env_path.is_file() else {}
        # 화면에서 제거한 예전 GUI 키는 저장 시 .env에서도 지운다.
        gui_keys = {k for k, _ in pairs} | DEPRECATED_GUI_ENV_KEYS
        merged: list[tuple[str, str]] = []
        for k, v in existing.items():
            if k in gui_keys:
                continue
            merged.append((k, "" if v is None else v))
        merged.extend(pairs)

        try:
            with env_path.open("w", encoding="utf-8") as fh:
                fh.write("# JDONE Kiosk .env — GUI 저장본\n")
                for k, v in merged:
                    needs_quote = any(ch in v for ch in (" ", "#", "\t", '"', "'", "$"))
                    if needs_quote:
                        escaped = v.replace('"', '\\"')
                        fh.write(f'{k}="{escaped}"\n')
                    else:
                        fh.write(f"{k}={v}\n")
        except Exception as exc:
            self._append_log(f"[ERROR] .env 저장 실패: {exc}")
            return False
        self._append_log(f"[INFO] .env 저장 완료: {env_path}")
        return True

    def _on_save_env(self) -> None:
        """현재 화면 값을 사용자 ``.env`` 파일에 저장한다."""
        if not self._persist_env():
            QMessageBox.critical(self, ".env 저장 실패", "저장에 실패했습니다.")
            return
        QMessageBox.information(
            self, ".env 저장", f"저장되었습니다:\n{user_env_path()}"
        )

    def _webview_process_running(self) -> bool:
        return (
            self._webview_proc is not None and self._webview_proc.poll() is None
        )

    def _on_meetone_test_button(self) -> None:
        if not self._webview_process_running():
            QMessageBox.information(
                self,
                "MeetOne 버튼 가정",
                "먼저 연결을 눌러 main.py 프로세스를 실행해 주세요.",
            )
            return
        enqueue_test_command(CMD_BTN_RIGHT)
        self._append_log("[TEST] MeetOne 버튼 가정 → 오른쪽 PCB 버튼 이벤트 전달")


def main() -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    if len(sys.argv) >= 4 and sys.argv[1] == "--run-webview":
        return _run_webview_cli(sys.argv[2], int(sys.argv[3]))
    if len(sys.argv) >= 2 and sys.argv[1] == "--run-main":
        return _run_main_cli()

    setup_system_logging(role="gui")

    argv = sys.argv[:]
    if "-platformtheme" not in argv:
        argv = argv + ["-platformtheme", "flat"]

    app = QApplication(argv)
    palette = QPalette()
    palette.setColor(QPalette.Window, QColor(CTP_MOCHA["background"]))
    palette.setColor(QPalette.WindowText, QColor(CTP_MOCHA["text"]))
    app.setPalette(palette)

    # 터미널에서 Ctrl+C 시 Qt 이벤트 루프가 SIGINT를 삼키는 경우 완화
    signal.signal(signal.SIGINT, lambda *_: app.quit())
    _sig_timer = QTimer()
    _sig_timer.start(200)
    _sig_timer.timeout.connect(lambda: None)

    window = KioskApp()
    window.show()
    return app.exec_()


if __name__ == "__main__":
    raise SystemExit(main())
