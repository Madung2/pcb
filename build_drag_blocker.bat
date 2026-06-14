@echo off
setlocal
cd /d "%~dp0"

echo [build_drag_blocker] Building DragBlocker.exe
uv run pyinstaller drag_blocker.spec --clean --noconfirm || goto :error

echo.
echo [build_drag_blocker] Done: %CD%\dist\DragBlocker.exe
exit /b 0

:error
echo [build_drag_blocker] Failed. errorlevel=%errorlevel%
exit /b 1
