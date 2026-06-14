@echo off
setlocal
cd /d "%~dp0"

echo [build_pcb_connection_check] Building PCBConnectionCheck.exe
uv run pyinstaller pcb_connection_check.spec --clean --noconfirm || goto :error

echo.
echo [build_pcb_connection_check] Done: %CD%\dist\PCBConnectionCheck.exe
exit /b 0

:error
echo [build_pcb_connection_check] Failed. errorlevel=%errorlevel%
exit /b 1
