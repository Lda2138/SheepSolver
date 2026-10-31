@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo Enable temporary game HTTPS capture for this Windows user.
echo This trusts the project CA and uses proxy 127.0.0.1:6666.
echo Stop script restores the previous proxy and removes the new CA.
venv\Scripts\python.exe capture_service.py stop
venv\Scripts\python.exe capture_service.py start --enable-proxy --trust-ca
pause
