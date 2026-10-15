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
title %CHARP% - 控制台
cd /d "%~dp0"

if not exist "程序\.venv\Scripts\python.exe" (
  echo.
  echo   [X] 找不到 程序\.venv\Scripts\python.exe
  echo       先把 Python 环境和依赖装好，见 使用说明.txt。
  echo.
  pause
  exit /b 1
)

echo ============================================================
echo   %CHARP% - 可视化控制台
echo ============================================================
echo.
echo   马上会自己开一个网页。在里面可以：
echo.
echo     · 填 API      地址 / Key / 模型 / 温度
echo     · 改人设      persona.md 全文编辑
echo     · 角色        切人物 / 让某个人重新认识你
echo     · 改世界书    世界\核心.md（每轮都带的那张）
echo     · 改你和我    分档位的态度、关系备忘
echo     · 看记忆      她定期复盘攒下来的长期记忆
echo     · 看她的日子  今天几点在干嘛、作息表、事件池、重摇今天
echo     · 调节奏      语音、打字、主动消息、关系快慢
echo.
echo   只在你自己这台机器上跑，API Key 不会传到别处。
echo   关掉这个黑窗口 = 关掉控制台。
echo ------------------------------------------------------------
echo.

"程序\.venv\Scripts\python.exe" "程序\控制台.py" %*

echo.
echo [OK] 控制台已关闭。
pause
