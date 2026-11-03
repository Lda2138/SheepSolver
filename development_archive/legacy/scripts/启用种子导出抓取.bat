@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo Opt-in local game seed exporter. Close the mini-game before installation.
echo Stop capture restores the original package when it has not been updated.
venv\Scripts\python.exe capture_service.py stop
if errorlevel 1 goto done
venv\Scripts\python.exe seed_export_hook.py install
if errorlevel 1 goto done
venv\Scripts\python.exe capture_service.py start --enable-proxy --trust-ca --seed-export
if errorlevel 1 venv\Scripts\python.exe seed_export_hook.py restore
:done
pause
