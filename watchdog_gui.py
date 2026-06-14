"""
JDONE Watchdog - register kiosk/helper programs and restart them if they stop.

Build:
    uv sync --group gui
    uv run pyinstaller watchdog_gui.spec --clean --noconfirm
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import subprocess
import sys
import time
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime
from io import StringIO
from pathlib import Path
from typing import Any

from PyQt5.QtCore import Qt, QThread, pyqtSignal
from PyQt5.QtGui import QCloseEvent, QIcon
from PyQt5.QtWidgets import (
    QAction,
    QApplication,
    QAbstractItemView,
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QFrame,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMenu,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QStyle,
    QSystemTrayIcon,
    QTableWidget,
    QTableWidgetItem,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

APP_NAME = "JDoneWatchdog"
CONFIG_FILE_NAME = "watchdog_config.json"
LOG_FILE_NAME = "watchdog.log"

DEFAULT_CHECK_INTERVAL_SECONDS = 2.0
DEFAULT_RESTART_DELAY_SECONDS = 3


def is_frozen() -> bool:
    return bool(getattr(sys, "frozen", False))


def app_root() -> Path:
    if is_frozen():
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


def config_path() -> Path:
    return app_root() / CONFIG_FILE_NAME


def log_path() -> Path:
    return app_root() / LOG_FILE_NAME


def _clean_str(value: Any) -> str:
    return str(value or "").strip()


def _bool(value: Any, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return default
    return str(value).strip().lower() in {"1", "true", "yes", "on", "y"}


def _int(value: Any, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


@dataclass
class ProgramConfig:
    name: str
    path: str
    args: str = ""
    working_dir: str = ""
    process_name: str = ""
    enabled: bool = True
    restart_delay_seconds: int = DEFAULT_RESTART_DELAY_SECONDS
    id: str = field(default_factory=lambda: uuid.uuid4().hex)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ProgramConfig":
        path = _clean_str(data.get("path"))
        process_name = _clean_str(data.get("process_name")) or Path(path).name
        name = _clean_str(data.get("name")) or Path(path).stem or "프로그램"
        working_dir = _clean_str(data.get("working_dir"))
        if not working_dir and path:
            working_dir = str(Path(path).expanduser().parent)
        restart_delay = max(1, _int(data.get("restart_delay_seconds"), DEFAULT_RESTART_DELAY_SECONDS))
        return cls(
            id=_clean_str(data.get("id")) or uuid.uuid4().hex,
            enabled=_bool(data.get("enabled"), True),
            name=name,
            path=path,
            args=_clean_str(data.get("args")),
            working_dir=working_dir,
            process_name=process_name,
            restart_delay_seconds=restart_delay,
        )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @property
    def display_process_name(self) -> str:
        return self.process_name or Path(self.path).name


@dataclass
class WatchdogConfig:
    programs: list[ProgramConfig] = field(default_factory=list)
    start_monitor_on_launch: bool = False
    start_minimized: bool = True
    check_interval_seconds: float = DEFAULT_CHECK_INTERVAL_SECONDS

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "WatchdogConfig":
        programs = [
            ProgramConfig.from_dict(item)
            for item in data.get("programs", [])
            if isinstance(item, dict)
        ]
        return cls(
            programs=programs,
            start_monitor_on_launch=_bool(data.get("start_monitor_on_launch"), False),
            start_minimized=_bool(data.get("start_minimized"), True),
            check_interval_seconds=max(
                1.0,
                float(data.get("check_interval_seconds", DEFAULT_CHECK_INTERVAL_SECONDS) or 1.0),
            ),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "programs": [program.to_dict() for program in self.programs],
            "start_monitor_on_launch": self.start_monitor_on_launch,
            "start_minimized": self.start_minimized,
            "check_interval_seconds": self.check_interval_seconds,
        }


def _candidate_kiosk_programs() -> list[ProgramConfig]:
    root = app_root()
    candidates = [
        root / "JDoneKiosk.exe",
        root / "dist" / "JDoneKiosk.exe",
    ]
    for candidate in candidates:
        if candidate.is_file():
            return [
                ProgramConfig(
                    name="JDoneKiosk",
                    path=str(candidate),
                    working_dir=str(candidate.parent),
                    process_name=candidate.name,
                    enabled=True,
                    restart_delay_seconds=DEFAULT_RESTART_DELAY_SECONDS,
                )
            ]
    return []


def load_config() -> WatchdogConfig:
    path = config_path()
    if not path.is_file():
        return WatchdogConfig(programs=_candidate_kiosk_programs())
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return WatchdogConfig(programs=_candidate_kiosk_programs())
    return WatchdogConfig.from_dict(data if isinstance(data, dict) else {})


def save_config(config: WatchdogConfig) -> None:
    path = config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(config.to_dict(), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def _windows_reg_path() -> str:
    return r"Software\Microsoft\Windows\CurrentVersion\Run"


def watchdog_launch_command(start_minimized: bool = True) -> str:
    if is_frozen():
        parts = [f'"{Path(sys.executable).resolve()}"']
    else:
        parts = [f'"{Path(sys.executable).resolve()}"', f'"{Path(__file__).resolve()}"']
    parts.append("--monitor")
    if start_minimized:
        parts.append("--minimized")
    return " ".join(parts)


def is_watchdog_autostart_enabled() -> bool:
    if sys.platform != "win32":
        return False
    try:
        import winreg
    except ImportError:
        return False
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _windows_reg_path(), 0, winreg.KEY_READ) as key:
            winreg.QueryValueEx(key, APP_NAME)
            return True
    except OSError:
        return False


def set_watchdog_autostart_enabled(enabled: bool, *, start_minimized: bool = True) -> tuple[bool, str]:
    if sys.platform != "win32":
        return False, "Windows에서만 로그인 자동 실행 등록을 지원합니다."
    try:
        import winreg
    except ImportError:
        return False, "Windows 레지스트리 모듈을 사용할 수 없습니다."
    try:
        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            _windows_reg_path(),
            0,
            winreg.KEY_SET_VALUE,
        ) as key:
            if enabled:
                winreg.SetValueEx(
                    key,
                    APP_NAME,
                    0,
                    winreg.REG_SZ,
                    watchdog_launch_command(start_minimized),
                )
                return True, "Watchdog가 Windows 로그인 자동 실행에 등록되었습니다."
            try:
                winreg.DeleteValue(key, APP_NAME)
            except OSError:
                pass
            return True, "Watchdog 자동 실행 등록이 해제되었습니다."
    except OSError as exc:
        return False, f"자동 실행 등록 처리 실패: {exc}"


def _creationflags_no_window() -> int:
    if sys.platform != "win32":
        return 0
    return getattr(subprocess, "CREATE_NO_WINDOW", 0)


def _windows_process_name_running(process_name: str) -> bool:
    if not process_name:
        return False
    try:
        completed = subprocess.run(
            ["tasklist", "/FI", f"IMAGENAME eq {process_name}", "/FO", "CSV", "/NH"],
            capture_output=True,
            text=True,
            timeout=3,
            creationflags=_creationflags_no_window(),
        )
    except (OSError, subprocess.SubprocessError):
        return False
    output = (completed.stdout or "").strip()
    if not output or "No tasks are running" in output:
        return False
    reader = csv.reader(StringIO(output))
    target = process_name.casefold()
    return any(row and row[0].strip('"').casefold() == target for row in reader)


def process_name_running(process_name: str) -> bool:
    if sys.platform == "win32":
        return _windows_process_name_running(process_name)
    return False


def build_command(program: ProgramConfig) -> str | list[str]:
    exe = str(Path(program.path).expanduser())
    args = program.args.strip()
    if sys.platform == "win32":
        return f'"{exe}" {args}'.strip()
    return [exe, *args.split()] if args else [exe]


def launch_program(program: ProgramConfig) -> subprocess.Popen[Any]:
    exe_path = Path(program.path).expanduser()
    if not exe_path.is_file():
        raise FileNotFoundError(str(exe_path))
    cwd = Path(program.working_dir).expanduser() if program.working_dir else exe_path.parent
    if not cwd.is_dir():
        cwd = exe_path.parent
    return subprocess.Popen(
        build_command(program),
        cwd=str(cwd),
        shell=False,
    )


@dataclass
class ProgramRuntime:
    program: ProgramConfig
    process: subprocess.Popen[Any] | None = None
    next_restart_at: float = 0.0
    restart_count: int = 0


class WatchdogThread(QThread):
    log = pyqtSignal(str)
    status = pyqtSignal(object)

    def __init__(self, programs: list[ProgramConfig], interval: float) -> None:
        super().__init__()
        self._running = True
        self._interval = interval
        self._states = {program.id: ProgramRuntime(program=program) for program in programs}

    def stop(self) -> None:
        self._running = False

    def run(self) -> None:
        self.log.emit("감시 루프를 시작했습니다.")
        while self._running:
            now = time.monotonic()
            for state in self._states.values():
                self._tick_program(state, now)
            self.msleep(max(200, int(self._interval * 1000)))
        self.log.emit("감시 루프를 중지했습니다.")

    def _tick_program(self, state: ProgramRuntime, now: float) -> None:
        program = state.program
        if not program.enabled:
            self.status.emit({"id": program.id, "status": "비활성", "restart_count": state.restart_count})
            return

        if state.process is not None:
            poll = state.process.poll()
            if poll is None:
                self.status.emit(
                    {
                        "id": program.id,
                        "status": f"실행 중 (PID {state.process.pid})",
                        "restart_count": state.restart_count,
                    }
                )
                return
            self.log.emit(f"{program.name} 종료 감지 (exit={poll})")
            state.process = None
            state.next_restart_at = now + program.restart_delay_seconds

        process_name = program.display_process_name
        if process_name and process_name_running(process_name):
            self.status.emit(
                {
                    "id": program.id,
                    "status": f"실행 중 ({process_name})",
                    "restart_count": state.restart_count,
                }
            )
            return

        if now < state.next_restart_at:
            remain = max(1, int(state.next_restart_at - now))
            self.status.emit(
                {
                    "id": program.id,
                    "status": f"{remain}초 후 재시작",
                    "restart_count": state.restart_count,
                }
            )
            return

        try:
            state.process = launch_program(program)
        except Exception as exc:
            state.next_restart_at = now + max(5, program.restart_delay_seconds)
            self.status.emit(
                {
                    "id": program.id,
                    "status": "시작 실패",
                    "restart_count": state.restart_count,
                }
            )
            self.log.emit(f"{program.name} 시작 실패: {exc}")
            return

        state.restart_count += 1
        self.status.emit(
            {
                "id": program.id,
                "status": f"실행 중 (PID {state.process.pid})",
                "restart_count": state.restart_count,
            }
        )
        self.log.emit(f"{program.name} 시작됨 (PID {state.process.pid})")


class ProgramDialog(QDialog):
    def __init__(self, parent: QWidget | None = None, program: ProgramConfig | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("프로그램 등록")
        self.setModal(True)
        self.resize(640, 260)

        self.name_edit = QLineEdit()
        self.path_edit = QLineEdit()
        self.args_edit = QLineEdit()
        self.cwd_edit = QLineEdit()
        self.process_name_edit = QLineEdit()
        self.enabled_cb = QCheckBox("감시 대상 사용")
        self.enabled_cb.setChecked(True)
        self.restart_delay_spin = QSpinBox()
        self.restart_delay_spin.setRange(1, 3600)
        self.restart_delay_spin.setValue(DEFAULT_RESTART_DELAY_SECONDS)

        if program is not None:
            self.name_edit.setText(program.name)
            self.path_edit.setText(program.path)
            self.args_edit.setText(program.args)
            self.cwd_edit.setText(program.working_dir)
            self.process_name_edit.setText(program.display_process_name)
            self.enabled_cb.setChecked(program.enabled)
            self.restart_delay_spin.setValue(program.restart_delay_seconds)
            self._program_id = program.id
        else:
            self._program_id = uuid.uuid4().hex

        browse_exe_btn = QPushButton("찾기")
        browse_exe_btn.clicked.connect(self._browse_exe)
        browse_cwd_btn = QPushButton("찾기")
        browse_cwd_btn.clicked.connect(self._browse_cwd)

        path_row = QHBoxLayout()
        path_row.addWidget(self.path_edit, 1)
        path_row.addWidget(browse_exe_btn)

        cwd_row = QHBoxLayout()
        cwd_row.addWidget(self.cwd_edit, 1)
        cwd_row.addWidget(browse_cwd_btn)

        form = QFormLayout()
        form.addRow("이름", self.name_edit)
        form.addRow("실행 파일", path_row)
        form.addRow("실행 인자", self.args_edit)
        form.addRow("작업 폴더", cwd_row)
        form.addRow("프로세스명", self.process_name_edit)
        form.addRow("재시작 대기(초)", self.restart_delay_spin)
        form.addRow("", self.enabled_cb)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)

        root = QVBoxLayout(self)
        root.addLayout(form)
        root.addWidget(buttons)

    def _browse_exe(self) -> None:
        filename, _ = QFileDialog.getOpenFileName(
            self,
            "실행 파일 선택",
            str(app_root()),
            "Programs (*.exe *.bat *.cmd);;All files (*.*)",
        )
        if not filename:
            return
        path = Path(filename)
        self.path_edit.setText(str(path))
        if not self.name_edit.text().strip():
            self.name_edit.setText(path.stem)
        if not self.cwd_edit.text().strip():
            self.cwd_edit.setText(str(path.parent))
        if not self.process_name_edit.text().strip() and path.suffix.lower() == ".exe":
            self.process_name_edit.setText(path.name)

    def _browse_cwd(self) -> None:
        dirname = QFileDialog.getExistingDirectory(self, "작업 폴더 선택", self.cwd_edit.text() or str(app_root()))
        if dirname:
            self.cwd_edit.setText(dirname)

    def accept(self) -> None:
        if not self.path_edit.text().strip():
            QMessageBox.warning(self, "입력 필요", "실행 파일을 선택해 주세요.")
            return
        super().accept()

    def program(self) -> ProgramConfig:
        path = self.path_edit.text().strip()
        process_name = self.process_name_edit.text().strip() or Path(path).name
        name = self.name_edit.text().strip() or Path(path).stem or "프로그램"
        cwd = self.cwd_edit.text().strip() or str(Path(path).expanduser().parent)
        return ProgramConfig(
            id=self._program_id,
            enabled=self.enabled_cb.isChecked(),
            name=name,
            path=path,
            args=self.args_edit.text().strip(),
            working_dir=cwd,
            process_name=process_name,
            restart_delay_seconds=self.restart_delay_spin.value(),
        )


class MainWindow(QMainWindow):
    def __init__(self, *, auto_monitor: bool = False, minimized: bool = False) -> None:
        super().__init__()
        self.setWindowTitle("JDONE Watchdog")
        self.resize(1080, 720)
        self.config = load_config()
        self.thread: WatchdogThread | None = None
        self._allow_close = False

        if auto_monitor:
            self.config.start_monitor_on_launch = True
        if minimized:
            self.config.start_minimized = True

        self._build_ui()
        self._build_tray()
        self._apply_config_to_ui()
        self._append_log(f"설정 파일: {config_path()}")

        if auto_monitor or self.config.start_monitor_on_launch:
            self.start_monitoring()

    def _build_ui(self) -> None:
        central = QWidget()
        root = QVBoxLayout(central)
        root.setContentsMargins(14, 14, 14, 14)
        root.setSpacing(10)
        self.setCentralWidget(central)

        title = QLabel("JDONE Watchdog")
        title.setObjectName("Title")
        subtitle = QLabel("키오스크 앱과 보조 프로그램을 등록하고 종료되면 자동으로 다시 실행합니다.")
        subtitle.setObjectName("SubTitle")
        root.addWidget(title)
        root.addWidget(subtitle)

        settings = QGroupBox("운영 설정")
        settings_layout = QGridLayout(settings)
        self.autostart_cb = QCheckBox("Windows 로그인 시 Watchdog 자동 실행")
        self.monitor_on_launch_cb = QCheckBox("Watchdog 시작 시 감시 자동 시작")
        self.minimized_cb = QCheckBox("자동 실행 시 트레이로 시작")
        self.interval_spin = QSpinBox()
        self.interval_spin.setRange(1, 60)
        self.interval_spin.setSuffix("초")
        settings_layout.addWidget(self.autostart_cb, 0, 0)
        settings_layout.addWidget(self.monitor_on_launch_cb, 0, 1)
        settings_layout.addWidget(self.minimized_cb, 0, 2)
        settings_layout.addWidget(QLabel("점검 주기"), 1, 0)
        settings_layout.addWidget(self.interval_spin, 1, 1)
        root.addWidget(settings)

        toolbar = QHBoxLayout()
        self.add_btn = QPushButton("등록")
        self.edit_btn = QPushButton("수정")
        self.remove_btn = QPushButton("삭제")
        self.save_btn = QPushButton("저장")
        self.start_btn = QPushButton("감시 시작")
        self.stop_btn = QPushButton("감시 중지")
        self.open_folder_btn = QPushButton("설정 폴더")
        self.start_btn.setObjectName("PrimaryBtn")
        self.stop_btn.setObjectName("DangerBtn")
        for btn in (
            self.add_btn,
            self.edit_btn,
            self.remove_btn,
            self.save_btn,
            self.start_btn,
            self.stop_btn,
            self.open_folder_btn,
        ):
            toolbar.addWidget(btn)
        toolbar.addStretch(1)
        root.addLayout(toolbar)

        self.table = QTableWidget(0, 9)
        self.table.setHorizontalHeaderLabels(
            ["사용", "이름", "상태", "실행 파일", "인자", "작업 폴더", "프로세스명", "대기", "재시작"]
        )
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.verticalHeader().setVisible(False)
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(2, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(3, QHeaderView.Stretch)
        header.setSectionResizeMode(4, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(5, QHeaderView.Stretch)
        header.setSectionResizeMode(6, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(7, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(8, QHeaderView.ResizeToContents)
        root.addWidget(self.table, 1)

        log_frame = QFrame()
        log_frame.setObjectName("LogFrame")
        log_layout = QVBoxLayout(log_frame)
        log_layout.setContentsMargins(10, 10, 10, 10)
        log_title = QLabel("로그")
        self.log_text = QTextEdit()
        self.log_text.setReadOnly(True)
        self.log_text.setMinimumHeight(160)
        log_layout.addWidget(log_title)
        log_layout.addWidget(self.log_text)
        root.addWidget(log_frame)

        self.add_btn.clicked.connect(self.add_program)
        self.edit_btn.clicked.connect(self.edit_program)
        self.remove_btn.clicked.connect(self.remove_program)
        self.save_btn.clicked.connect(self.save_settings)
        self.start_btn.clicked.connect(self.start_monitoring)
        self.stop_btn.clicked.connect(self.stop_monitoring)
        self.open_folder_btn.clicked.connect(self.open_config_folder)
        self.table.doubleClicked.connect(self.edit_program)
        self.autostart_cb.stateChanged.connect(self._on_autostart_changed)
        self.monitor_on_launch_cb.stateChanged.connect(self._on_runtime_setting_changed)
        self.minimized_cb.stateChanged.connect(self._on_runtime_setting_changed)
        self.interval_spin.valueChanged.connect(self._on_runtime_setting_changed)

    def _build_tray(self) -> None:
        self.tray_icon: QSystemTrayIcon | None = None
        if not QSystemTrayIcon.isSystemTrayAvailable():
            return
        icon = self.style().standardIcon(QStyle.SP_ComputerIcon)
        self.tray_icon = QSystemTrayIcon(icon, self)
        self.setWindowIcon(icon)
        menu = QMenu(self)

        show_action = QAction("설정 열기", self)
        show_action.triggered.connect(self.show_from_tray)
        start_action = QAction("감시 시작", self)
        start_action.triggered.connect(self.start_monitoring)
        stop_action = QAction("감시 중지", self)
        stop_action.triggered.connect(self.stop_monitoring)
        quit_action = QAction("종료", self)
        quit_action.triggered.connect(self.quit_app)

        menu.addAction(show_action)
        menu.addAction(start_action)
        menu.addAction(stop_action)
        menu.addSeparator()
        menu.addAction(quit_action)
        self.tray_icon.setContextMenu(menu)
        self.tray_icon.activated.connect(self._on_tray_activated)
        self.tray_icon.show()

    def _apply_config_to_ui(self) -> None:
        self.monitor_on_launch_cb.blockSignals(True)
        self.minimized_cb.blockSignals(True)
        self.interval_spin.blockSignals(True)
        self.autostart_cb.blockSignals(True)

        self.monitor_on_launch_cb.setChecked(self.config.start_monitor_on_launch)
        self.minimized_cb.setChecked(self.config.start_minimized)
        self.interval_spin.setValue(int(self.config.check_interval_seconds))
        self.autostart_cb.setChecked(is_watchdog_autostart_enabled())
        self.autostart_cb.setEnabled(sys.platform == "win32")

        self.monitor_on_launch_cb.blockSignals(False)
        self.minimized_cb.blockSignals(False)
        self.interval_spin.blockSignals(False)
        self.autostart_cb.blockSignals(False)

        self._refresh_table()
        self._refresh_buttons()

    def _refresh_table(self) -> None:
        self.table.setRowCount(0)
        for program in self.config.programs:
            row = self.table.rowCount()
            self.table.insertRow(row)
            values = [
                "ON" if program.enabled else "OFF",
                program.name,
                "대기",
                program.path,
                program.args,
                program.working_dir,
                program.display_process_name,
                f"{program.restart_delay_seconds}초",
                "0",
            ]
            for col, value in enumerate(values):
                item = QTableWidgetItem(value)
                item.setData(Qt.UserRole, program.id)
                if col in {0, 2, 7, 8}:
                    item.setTextAlignment(Qt.AlignCenter)
                self.table.setItem(row, col, item)

    def _refresh_buttons(self) -> None:
        running = self.thread is not None and self.thread.isRunning()
        self.start_btn.setEnabled(not running)
        self.stop_btn.setEnabled(running)
        self.add_btn.setEnabled(not running)
        self.edit_btn.setEnabled(not running)
        self.remove_btn.setEnabled(not running)
        self.save_btn.setEnabled(not running)

    def _selected_program_index(self) -> int | None:
        rows = self.table.selectionModel().selectedRows()
        if not rows:
            return None
        index = rows[0].row()
        if index < 0 or index >= len(self.config.programs):
            return None
        return index

    def add_program(self) -> None:
        dialog = ProgramDialog(self)
        if dialog.exec_() != QDialog.Accepted:
            return
        self.config.programs.append(dialog.program())
        self._refresh_table()

    def edit_program(self) -> None:
        index = self._selected_program_index()
        if index is None:
            QMessageBox.information(self, "선택 필요", "수정할 프로그램을 선택해 주세요.")
            return
        dialog = ProgramDialog(self, self.config.programs[index])
        if dialog.exec_() != QDialog.Accepted:
            return
        self.config.programs[index] = dialog.program()
        self._refresh_table()

    def remove_program(self) -> None:
        index = self._selected_program_index()
        if index is None:
            QMessageBox.information(self, "선택 필요", "삭제할 프로그램을 선택해 주세요.")
            return
        program = self.config.programs[index]
        answer = QMessageBox.question(self, "삭제 확인", f"{program.name} 등록을 삭제할까요?")
        if answer != QMessageBox.Yes:
            return
        del self.config.programs[index]
        self._refresh_table()

    def save_settings(self) -> None:
        self._read_runtime_settings()
        try:
            save_config(self.config)
        except OSError as exc:
            QMessageBox.critical(self, "저장 실패", f"설정 저장 실패: {exc}")
            return
        self._append_log("설정을 저장했습니다.")

    def _read_runtime_settings(self) -> None:
        self.config.start_monitor_on_launch = self.monitor_on_launch_cb.isChecked()
        self.config.start_minimized = self.minimized_cb.isChecked()
        self.config.check_interval_seconds = float(self.interval_spin.value())

    def _on_runtime_setting_changed(self) -> None:
        self._read_runtime_settings()
        if self.autostart_cb.isChecked() and sys.platform == "win32":
            set_watchdog_autostart_enabled(True, start_minimized=self.config.start_minimized)

    def _on_autostart_changed(self, state: int) -> None:
        enabled = state == Qt.Checked
        self._read_runtime_settings()
        ok, message = set_watchdog_autostart_enabled(
            enabled,
            start_minimized=self.config.start_minimized,
        )
        self._append_log(message)
        if not ok:
            QMessageBox.warning(self, "자동 실행 설정 실패", message)
            self.autostart_cb.blockSignals(True)
            self.autostart_cb.setChecked(not enabled)
            self.autostart_cb.blockSignals(False)

    def start_monitoring(self) -> None:
        if self.thread is not None and self.thread.isRunning():
            return
        enabled = [program for program in self.config.programs if program.enabled]
        if not enabled:
            QMessageBox.warning(self, "감시 대상 없음", "사용으로 설정된 프로그램이 없습니다.")
            return
        self.save_settings()
        self.thread = WatchdogThread(enabled, self.config.check_interval_seconds)
        self.thread.log.connect(self._append_log)
        self.thread.status.connect(self._on_status)
        self.thread.finished.connect(self._on_thread_finished)
        self.thread.start()
        self._refresh_buttons()

    def stop_monitoring(self) -> None:
        if self.thread is None:
            return
        self.thread.stop()
        self.thread.wait(3000)
        if self.thread.isRunning():
            self._append_log("감시 루프 종료 대기 중입니다.")
        else:
            self.thread = None
        self._refresh_buttons()

    def _on_thread_finished(self) -> None:
        self.thread = None
        self._refresh_buttons()

    def _on_status(self, payload: object) -> None:
        if not isinstance(payload, dict):
            return
        program_id = str(payload.get("id", ""))
        status = str(payload.get("status", ""))
        restart_count = str(payload.get("restart_count", "0"))
        for row in range(self.table.rowCount()):
            item = self.table.item(row, 0)
            if item is None or item.data(Qt.UserRole) != program_id:
                continue
            self.table.item(row, 2).setText(status)
            self.table.item(row, 8).setText(restart_count)
            break

    def _append_log(self, message: str) -> None:
        line = f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] {message}"
        self.log_text.append(line)
        try:
            with log_path().open("a", encoding="utf-8") as fp:
                fp.write(line + "\n")
        except OSError:
            pass
        if self.tray_icon is not None and ("실패" in message or "종료 감지" in message):
            self.tray_icon.showMessage("JDONE Watchdog", message, QSystemTrayIcon.Warning, 4000)

    def open_config_folder(self) -> None:
        folder = app_root()
        if sys.platform == "win32":
            os.startfile(str(folder))  # type: ignore[attr-defined]
            return
        QMessageBox.information(self, "설정 폴더", str(folder))

    def show_from_tray(self) -> None:
        self.showNormal()
        self.raise_()
        self.activateWindow()

    def _on_tray_activated(self, reason: QSystemTrayIcon.ActivationReason) -> None:
        if reason in {QSystemTrayIcon.DoubleClick, QSystemTrayIcon.Trigger}:
            self.show_from_tray()

    def quit_app(self) -> None:
        self._allow_close = True
        self.stop_monitoring()
        if self.tray_icon is not None:
            self.tray_icon.hide()
        QApplication.instance().quit()

    def closeEvent(self, event: QCloseEvent) -> None:
        running = self.thread is not None and self.thread.isRunning()
        if running and not self._allow_close and self.tray_icon is not None:
            self.hide()
            self.tray_icon.showMessage(
                "JDONE Watchdog",
                "감시는 계속 실행 중입니다. 종료하려면 트레이 메뉴의 종료를 선택하세요.",
                QSystemTrayIcon.Information,
                3500,
            )
            event.ignore()
            return
        self.stop_monitoring()
        if self.tray_icon is not None:
            self.tray_icon.hide()
        event.accept()


QSS = """
QMainWindow { background-color: #1E1E2E; }
QWidget { color: #CDD6F4; font-family: 'Segoe UI', 'Tahoma', sans-serif; font-size: 9pt; }
QLabel#Title { color: #B4BEFE; font-size: 18pt; font-weight: 700; }
QLabel#SubTitle { color: #A6ADC8; font-weight: 400; }
QGroupBox {
    border: 1px solid #45475A;
    border-radius: 8px;
    margin-top: 10px;
    padding: 10px;
}
QGroupBox::title { subcontrol-origin: margin; left: 12px; padding: 0 4px; color: #B4BEFE; }
QLineEdit, QSpinBox, QTextEdit, QTableWidget {
    background-color: #11111B;
    border: 1px solid #45475A;
    border-radius: 5px;
    padding: 4px;
    color: #CDD6F4;
}
QHeaderView::section {
    background-color: #313244;
    color: #CDD6F4;
    border: 0;
    padding: 6px;
}
QTableWidget::item:selected { background-color: #45475A; }
QPushButton {
    background-color: #313244;
    color: #CDD6F4;
    border: 1px solid #6C7086;
    border-radius: 5px;
    padding: 7px 11px;
    font-weight: 600;
}
QPushButton:hover { border-color: #B4BEFE; color: #B4BEFE; }
QPushButton:disabled { color: #6C7086; border-color: #313244; background-color: #181825; }
QPushButton#PrimaryBtn { background-color: #B4BEFE; color: #1E1E2E; border-color: #B4BEFE; }
QPushButton#DangerBtn { color: #F38BA8; border-color: #F38BA8; }
QFrame#LogFrame { background-color: #181825; border-radius: 8px; }
QCheckBox { spacing: 6px; }
"""


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="JDONE Watchdog")
    parser.add_argument("--monitor", action="store_true", help="Start monitoring immediately")
    parser.add_argument("--minimized", action="store_true", help="Start hidden in the tray")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(list(argv or sys.argv[1:]))
    app = QApplication(sys.argv[:1])
    app.setQuitOnLastWindowClosed(False)
    app.setStyleSheet(QSS)
    window = MainWindow(auto_monitor=args.monitor, minimized=args.minimized)
    should_hide = bool(args.minimized or (window.config.start_minimized and window.config.start_monitor_on_launch))
    if should_hide and window.tray_icon is not None:
        window.hide()
    else:
        window.show()
    return app.exec_()


if __name__ == "__main__":
    raise SystemExit(main())
