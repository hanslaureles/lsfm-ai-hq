@echo off
title LE SSERAFIM AI HQ — Squad Runner
cd /d "%~dp0"
echo ============================================================
echo   LE SSERAFIM AI HQ — Starting Squad ^& Brain...
echo ============================================================

:: Ensure no duplicate / ghost squad instances are already running
taskkill /F /IM python.exe 2>nul
taskkill /F /IM python3.11.exe 2>nul
taskkill /F /IM python3.exe 2>nul

:: Check if Ollama is running, if not start it minimized
tasklist /fi "imagename eq ollama.exe" 2>nul | findstr /i "ollama.exe" >nul
if errorlevel 1 (
    echo [1/2] Launching Ollama Local Brain on RX 6600 XT...
    start /min "" "%LOCALAPPDATA%\Programs\Ollama\ollama.exe" serve
    ping 127.0.0.1 -n 3 >nul
) else (
    echo [1/2] Ollama Local Brain is already running.
)

echo [2/2] Connecting all 5 Discord Bots to LSFM HQ...
echo ============================================================
python -u run_all.py
pause
