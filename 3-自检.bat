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
title %CHARP% - 微信自检
set "DPY=%ROOT%\程序\.venv\Scripts\python.exe"
if not exist "%DPY%" set "DPY=python"

echo ============================================================
echo    自检：微信的消息读不读得到
echo ============================================================
echo.
echo   跑之前确认：微信已打开、已登录、没有缩到托盘。
echo   这一步不需要填 API Key。
echo.

"%DPY%" "%ROOT%\程序\check_wechat.py"

echo.
echo ------------------------------------------------------------
echo   如果这一页都是 [OK]，就双击「一键启动.bat」开始跑。
echo   如果出现 [X]，照屏幕上的提示处理。
echo   还搞不定，把这一页内容 和 日志\消息自检-整屏.png 一起发我。
echo ------------------------------------------------------------
echo.
pause
