@echo off
chcp 936 >nul
set "PYTHONIOENCODING=gbk:replace"
rem ── ROOT 必须在 cd 之前定下来（顺序反了，用相对路径运行会把 ROOT 算重一层）──
set "ROOT=%~dp0"
if "%ROOT:~-1%"=="\" set "ROOT=%ROOT:~0,-1%"
cd /d "%ROOT%"
rem ── 现在是谁（写在标题上，省得切错了还不知道）──
set "CHARP="
set "DN_PY=%ROOT%\程序\.venv\Scripts\python.exe"
if not exist "%DN_PY%" set "DN_PY=python"
for /f "usebackq delims=" %%i in (`"%DN_PY%" "%ROOT%\程序\角色.py" --who 2^>nul`) do set "CHARP=%%i"
if not defined CHARP set "CHARP=白佰岚"
title %CHARP% - 测发送
set "DPY=%ROOT%\程序\.venv\Scripts\python.exe"
if not exist "%DPY%" set "DPY=python"

echo ============================================================
echo    自检：后台发送通不通（不抢焦点、不动鼠标）
echo ============================================================
echo.
echo   会往「当前打开的会话」发一条带时间戳的测试消息，然后自己 OCR 复核。
echo   只会在当前会话 == config.yaml 里的 target 时发送，不会发错人。
echo.
echo   前提：微信保持「显示」状态，别最小化到托盘。
echo.

"%DPY%" "%ROOT%\程序\check_send.py" %*

echo.
echo ------------------------------------------------------------
echo   [OK] 后台发送可用 —— 微信不会跳前台，鼠标也不会动
echo   [X]  照屏幕上的提示做（多半是在微信窗口上点一下就行）
echo ------------------------------------------------------------
echo.
pause
