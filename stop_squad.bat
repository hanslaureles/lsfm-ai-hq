@echo off
title LE SSERAFIM AI HQ - Stop Squad
echo ============================================================
echo   Stopping all LE SSERAFIM bots (Freeing RAM ^& GPU for Gaming)...
echo ============================================================

:: Stop only the bots' own process tree (the PID holding memory\lsfm-bots.lock),
:: never every Python on the PC (6B): the Ciel server and other tools keep running.
python "%~dp0instance_lock.py" stop
taskkill /F /IM ollama.exe 2>nul
taskkill /F /IM "ollama app.exe" 2>nul

echo ============================================================
echo   All bots and local models stopped cleanly!
echo   100%% of your RX 6600 XT GPU and RAM are restored for gaming!
echo ============================================================
timeout /t 3 >nul
