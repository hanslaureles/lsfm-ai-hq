@echo off
title LE SSERAFIM AI HQ — Squad Runner
cd /d "%~dp0"
echo ============================================================
echo   LE SSERAFIM AI HQ — Starting Squad ^& Brain...
echo ============================================================

:: No duplicate squad: run_all.py refuses to start while another bot process holds
:: memory\lsfm-bots.lock (6B). Run stop_squad.bat first to restart.

:: Logon task (7A-2, scripts\register_bot_task.ps1): no Ollama (cloud models are the
:: default; run this file by hand before !mode local), no pause, output appended to
:: memory\bots.log. ponytail: the log never rotates; trim it when it passes a few MB.
if /i not "%~1"=="--autostart" goto by_hand
echo ===== %date% %time% logon start>> "memory\bots.log"
python -u run_all.py >> "memory\bots.log" 2>&1
exit /b %errorlevel%

:by_hand

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
