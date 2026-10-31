@echo off
chcp 65001 >nul
cd /d "%~dp0"
"%~dp0venv\Scripts\python.exe" "%~dp0desktop_assistant.py" %*
if errorlevel 1 exit /b 1
echo 已生成 output\solution.json 和 output\点击预演.html。此程序不移动鼠标、不激活微信。
