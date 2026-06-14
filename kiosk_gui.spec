# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec — JDONE Kiosk GUI(.exe).

빌드 (Windows):
    uv sync --group gui
    uv add --group dev pyinstaller
    uv run pyinstaller kiosk_gui.spec --clean --noconfirm

산출물: dist/JDoneKiosk.exe (--onefile, --windowed)

번들 리소스:
    - .env  → default.env  (첫 실행 시 .exe 옆에 .env 로 복사됨)
    - static/  (트레이 아이콘 등, 있으면 번들)

실행 모델:
    - .exe 옆 디렉터리(=Path(sys.executable).parent)를 사용자 데이터 루트로 사용.
    - 첫 실행 시 번들 default.env → 사용자 .env 자동 복사 (kiosk_module._paths.ensure_user_env).
    - GUI 의 ".env 저장" 버튼이 그 사용자 .env 를 직접 갱신.
"""
from __future__ import annotations

import os
from pathlib import Path

block_cipher = None

# spec 파일 위치 = 레포 루트
ROOT = Path(SPECPATH).resolve()


def _data_if_exists(src: str, dst: str) -> list[tuple[str, str]]:
    p = ROOT / src
    if p.exists():
        return [(str(p), dst)]
    return []


# (소스경로, 번들 내 디렉터리) — onefile 의 경우 sys._MEIPASS 기준
datas: list[tuple[str, str]] = []

# 기본 .env 템플릿 (현재 레포의 .env 를 default.env 라는 이름으로 번들)
if (ROOT / ".env").is_file():
    datas.append((str(ROOT / ".env"), "."))           # .env 그대로
    datas.append((str(ROOT / ".env"), "."))           # 중복 — 아래에서 default.env 로 한 번 더

# PyInstaller 의 datas 는 (src, target_dir) 만 받으므로 이름을 바꾸려면 임시 복사가 필요.
# 간단하게 빌드 직전에 default.env 를 만들어 둔다.
_default_env_src = ROOT / "default.env"
if not _default_env_src.exists() and (ROOT / ".env").exists():
    _default_env_src.write_bytes((ROOT / ".env").read_bytes())
if _default_env_src.is_file():
    datas.append((str(_default_env_src), "."))

# 트레이 아이콘 등 (없으면 코드가 fallback 처리)
datas += _data_if_exists("static/favicon.png", "static")


hiddenimports = [
    # GUI
    "PyQt5.sip",
    # 의존성 중 implicit import 가 있을 수 있는 것들
    "pydantic",
    "pydantic_core",
    "websockets",
    "websockets.legacy",
    "websockets.legacy.client",
    "websockets.legacy.server",
    "dotenv",
    "serial",
    "serial.tools",
    "serial.tools.list_ports",
    "pynput",
    "pynput.keyboard",
    "pynput.mouse",
    "PIL",
    "PIL.Image",
    "pystray",
    "pyproj",
    "geocoder",
    "webview",
]


a = Analysis(
    ["gui_main.py"],
    pathex=[str(ROOT)],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    runtime_hooks=[],
    excludes=[
        # 불필요한 큰 의존성 제외 (필요시 제거)
        "tkinter",
        "test",
        "pytest",
    ],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name="JDoneKiosk",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,                   # --windowed
    disable_windowed_traceback=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=str(ROOT / "static" / "favicon.png") if (ROOT / "static" / "favicon.png").is_file() else None,
)
