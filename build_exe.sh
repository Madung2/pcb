#!/usr/bin/env bash
# JDONE Kiosk — 개발자용 빌드 헬퍼 (Windows 에서는 build_exe.bat 사용).
# 이 스크립트는 Linux/macOS 에서 spec 검증/리허설용. Windows .exe 는 Windows 머신에서만 만들어진다.
set -e
cd "$(dirname "$0")"

if [[ "$OSTYPE" != "msys" && "$OSTYPE" != "cygwin" && "$OSTYPE" != "win32" ]]; then
  echo "[build_exe] 경고: 현재 OS($OSTYPE) 에서는 Windows .exe 를 만들 수 없습니다."
  echo "[build_exe] 그래도 PyInstaller 가 spec 을 파싱하는지만 검증합니다."
fi

uv sync --group gui
uv add --group dev pyinstaller >/dev/null 2>&1 || true

rm -rf build dist
uv run pyinstaller kiosk_gui.spec --clean --noconfirm
echo "[build_exe] 완료: $(pwd)/dist/"
