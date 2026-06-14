@echo off
REM JDONE Kiosk — Windows .exe 빌드 (PyInstaller)
REM 사용: build_exe.bat
REM 산출물: dist\JDoneKiosk.exe

setlocal
cd /d "%~dp0"

echo [build_exe] uv sync (gui)
uv sync --group gui || goto :error

echo [build_exe] pyinstaller 설치 확인
uv add --group dev pyinstaller >nul 2>nul

echo [build_exe] 이전 산출물 정리
if exist build rd /s /q build
if exist dist rd /s /q dist

echo [build_exe] 빌드 시작
uv run pyinstaller kiosk_gui.spec --clean --noconfirm || goto :error

echo.
echo [build_exe] 완료: %CD%\dist\JDoneKiosk.exe
exit /b 0

:error
echo [build_exe] 실패 (errorlevel=%errorlevel%)
exit /b 1
