@echo off
setlocal
cd /d "%~dp0"

echo [build_watchdog] uv sync (gui)
uv sync --group gui || goto :error

echo [build_watchdog] Checking PyInstaller
uv add --group dev pyinstaller >nul 2>nul

echo [build_watchdog] Building Watchdog.exe
uv run pyinstaller watchdog_gui.spec --clean --noconfirm || goto :error

echo.
echo [build_watchdog] Done: %CD%\dist\Watchdog.exe
exit /b 0

:error
echo [build_watchdog] Failed. errorlevel=%errorlevel%
exit /b 1
