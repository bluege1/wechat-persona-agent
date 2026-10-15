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
title %CHARP% - 声卡自检
set "DPY=%ROOT%\程序\.venv\Scripts\python.exe"
if not exist "%DPY%" set "DPY=python"

echo ============================================================
echo    声卡自检
echo ============================================================
echo.
echo   虚拟声卡是 %CHARP% 发语音条的命门：
echo     她的声音被播放进 CABLE Input，
echo     微信从 CABLE Output 录音，
echo     于是发出去的就是真语音气泡。
echo.

"%DPY%" "%ROOT%\程序\check_audio.py"

echo.
echo ------------------------------------------------------------
echo   如果上面出现 [X]，按这两条修：
echo.
echo   [情况一] 虚拟声卡那两行是 [X]  —— 驱动没装/被卸载了
echo            安装程序在：驱动\VBCable\VBCABLE_Setup_x64.exe
echo            双击它，点 Install Driver，然后重启电脑 ^(只做一次^)
echo.
echo   [情况二] 默认录音设备那几行是 [X] —— 只差选中
echo            设置 - 系统 - 声音 - 输入 - 选「CABLE Output」
echo            或者直接跑这一条：
echo              "%DPY%" "%ROOT%\程序\audio_device.py" set-rec "CABLE Output"
echo.
echo   跑完再双击一次本脚本，确认全变 [OK]。
echo ------------------------------------------------------------
echo.
pause
