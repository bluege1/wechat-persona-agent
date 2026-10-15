@echo off
chcp 936 >nul
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
title %CHARP% - 改配置
echo ============================================================
echo    要改的就两个文件
echo ============================================================
echo.
echo   [1] config.yaml   —— 你的 API Key、盯着哪个聊天、说话节奏
echo   [2] persona.md    —— 她的人设（把你自己写的粘进去覆盖）
echo.
echo   改完存盘，重新跑「一键启动.bat」就生效。
echo.
echo ------------------------------------------------------------
echo   config.yaml 里最容易写错的两处，对着看：
echo.
echo     base_url : https://api.siliconflow.cn/v1
echo                是 api，不是 cloud。
echo                cloud.siliconflow.cn 是官网控制台，写它会返回网页，
echo                程序会报 Expecting value: line 1 column 1 (char 0)
echo.
echo     model    : deepseek-ai/DeepSeek-V4-Flash
echo                必须带厂商前缀 deepseek-ai/
echo.
echo   改完不确定对不对？关掉记事本，双击「4-测大脑.bat」验一下。
echo ------------------------------------------------------------
echo.

start "" notepad "%ROOT%\角色\%CHARP%\config.yaml"
timeout /t 1 /nobreak >nul
start "" notepad "%ROOT%\角色\%CHARP%\persona.md"
exit /b 0
