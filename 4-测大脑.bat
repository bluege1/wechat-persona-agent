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
title %CHARP% - 测大脑
set "DPY=%ROOT%\程序\.venv\Scripts\python.exe"
if not exist "%DPY%" set "DPY=python"

echo ============================================================
echo    自检：大脑（API）通不通
echo ============================================================
echo.
echo   只测 config.yaml 里的 base_url / api_key / model，
echo   跟微信、声卡都没关系。聊天不出话时先跑这一个。
echo.
echo   两个最常见的坑：
echo     base_url 要写 api.siliconflow.cn，不是 cloud.siliconflow.cn
echo     model    要带厂商前缀，如 deepseek-ai/DeepSeek-V4-Flash
echo.

"%DPY%" "%ROOT%\程序\check_llm.py"

echo.
echo ------------------------------------------------------------
echo   看到 [OK] 大脑通了 -^> 去跑「一键启动.bat」
echo   看到 [X]         -^> 照屏幕提示改 config.yaml
echo ------------------------------------------------------------
echo.
pause
