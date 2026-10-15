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
title %CHARP% - 查语音
set "DPY=%ROOT%\程序\.venv\Scripts\python.exe"
if not exist "%DPY%" set "DPY=python"

echo ============================================================
echo    自检：语音条发不出去 / 对方语音听不懂
echo ============================================================
echo.
echo   1  环境检查（安全，不动鼠标、不抢前台）         [直接回车]
echo   2  环境检查 + 实测快捷键（自动比 当前配置/左Alt/右Alt，约 12 秒）
echo   3  环境检查 + 真发一条测试语音（需微信和小信使都开着）
echo.

set "MODE="
set /p "MODE=请选 1 / 2 / 3，回车默认 1： "

if "%MODE%"=="2" goto probe
if "%MODE%"=="3" goto send
goto base

:probe
"%DPY%" "%ROOT%\程序\check_voice.py" --probe
goto done

:send
"%DPY%" "%ROOT%\程序\check_voice.py" --send
goto done

:base
"%DPY%" "%ROOT%\程序\check_voice.py"

:done
echo.
echo ------------------------------------------------------------
echo  【先分清两个功能，别按错】
echo    - 语音输入文字：按住 Ctrl+Win，或点【输入框下方工具栏】里那个话筒，
echo      产物是文字。这是 4.1.8 就有的。
echo    - 发送语音消息：点【聊天框右下角、发送按钮旁边】那个小话筒，
echo      或按住 Alt，产物是语音条。这是 4.1.9 才有的，我们要的是这个。
echo.
echo  【语音条发不出去】按顺序排查：
echo    1) 微信版本低于 4.1.9 -- 这版之前电脑端根本不能发语音，
echo       去微信官网下新版覆盖安装。
echo    2) 快捷键对不上。程序默认按【右 Alt】；选 2 实测会自动对比
echo       当前配置 / 左 Alt / 右 Alt，哪个有反应就用哪个。
echo       用环境变量指定（LAlt / RAlt / Ctrl+Win 都认）：
echo           set "DANIYA_VOICE_HOTKEY=RAlt"
echo       也可以反过来，在 微信 - 设置 - 快捷键 里改成程序按的键。
echo    3) 麦克风：设置 - 隐私和安全性 - 麦克风 - 让桌面应用访问你的麦克风；
echo       微信 - 设置 - 语音视频 - 麦克风 - 选 CABLE Output。
echo.
echo  【收到的语音听不懂】她只能看到 语音 2秒 这种字样：
echo    微信 - 左下角三条横线 - 设置 - 通用 -
echo    勾上【聊天中的语音消息自动转成文字】
echo    勾上之后语音条下面会直接显示文字，她就能读懂了。
echo    万一找不到这个开关，就右键点收到的语音选【转文字】试一次，
echo    能转出来说明是设置位置问题，转不出来说明版本不支持。
echo    注意：你自己发言语能转文字，那是【语音输入】，和读懂收到的语音
echo    不是一回事。真没这个功能也不影响，她会礼貌请你打字，不瞎猜。
echo.
echo  【实在不想折腾】把 程序\config.yaml 里
echo    voice:
echo      enabled: true    改成 false
echo    她就只发文字，纯后台、零干扰，语音收发都不碰。
echo ------------------------------------------------------------
echo.
pause
