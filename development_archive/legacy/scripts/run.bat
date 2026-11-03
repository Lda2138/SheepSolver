@echo off
chcp 65001 >nul
cd /d %~dp0

echo ============================================
echo   SheepSolver 启动菜单
echo ============================================
echo.
echo  [1] 启动抓包 (mitmdump :6666)
echo  [2] 求解答案 (normal 模式)
echo  [3] 求解答案 (自定义模式)
echo.
set /p choice=请输入选项(1/2/3):

if "%choice%"=="1" goto capture
if "%choice%"=="2" goto solve
if "%choice%"=="3" goto solve_custom
goto end

:capture
echo.
echo [启动抓包] 请在手机/模拟器设置代理为 本机IP:6666
echo [提示] 进入游戏后 online_data.json 会自动更新
echo [退出] 按 Ctrl+C 停止
echo.
"%~dp0venv\Scripts\mitmdump.exe" -p 6666 -s capture.py --set confdir=%~dp0automatic
goto end

:solve
echo.
echo [求解中... 使用 normal 模式]
"%~dp0venv\Scripts\python.exe" solve.py -m normal
goto end

:solve_custom
echo.
echo 可选模式: index-ascending / index-descending / level-bottom / level-top / normal / random
set /p mode=请输入模式名称:
"%~dp0venv\Scripts\python.exe" solve.py -m %mode%
goto end

:end
echo.
pause
