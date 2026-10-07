@echo off
title LE SSERAFIM AI HQ - Squad Runner
cd /d "%~dp0"
echo ============================================================
echo   LE SSERAFIM AI HQ - Starting Squad ^& Brain...
echo ============================================================

:: No duplicate squad: run_all.py refuses to start while another bot process holds
:: memory\lsfm-bots.lock (6B). Run stop_squad.bat first to restart.
:: At logon the bots start without a window and without Ollama (7A-3,
:: scripts\register_bot_task.ps1); run this file by hand before !mode local.

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
