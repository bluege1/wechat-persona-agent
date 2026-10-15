@echo off
chcp 936 >nul
set "PYTHONIOENCODING=gbk:replace"
setlocal enabledelayedexpansion
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
title %CHARP% - 微信机器人
set "MIRROR=https://pypi.tuna.tsinghua.edu.cn/simple"

set "WPY=%ROOT%\小信使\venv\Scripts\python.exe"
set "DPY=%ROOT%\程序\.venv\Scripts\python.exe"

echo ============================================================
echo    %CHARP% - 微信机器人     一键启动
echo ============================================================
echo.

rem ---------- 0. 找到可用的 python ----------
set "SYSBOT="
where python >nul 2>nul
if not errorlevel 1 set "SYSBOT=python"

rem ---------- 1. 小信使环境 ----------
if not exist "%WPY%" (
    if "%SYSBOT%"=="" (
        echo [X] 系统里找不到 python，请先安装 Python 3.10 以上版本。
        pause
        exit /b 1
    )
    echo [!] 正在创建小信使的虚拟环境，第一次会慢一点……
    %SYSBOT% -m venv "%ROOT%\小信使\venv"
)
"%WPY%" -c "import uiautomation, pyperclip, pygame, yaml, websocket" >nul 2>nul
if errorlevel 1 (
    echo [!] 小信使依赖不全，正在补装（走清华镜像）……
    "%WPY%" -m pip install -q -i %MIRROR% -r "%ROOT%\小信使\requirements.txt"
)
echo [OK] 小信使环境就位

rem ---------- 2. 运行环境（三个角色共用这一套）----------
if not exist "%DPY%" (
    if "%SYSBOT%"=="" (
        echo [X] 系统里找不到 python，请先安装 Python 3.10 以上版本。
        pause
        exit /b 1
    )
    echo [!] 正在创建运行环境，第一次会慢一点……
    %SYSBOT% -m venv "%ROOT%\程序\.venv"
)
"%DPY%" -c "import uiautomation, requests, yaml, edge_tts, PIL, rapidocr_onnxruntime, numpy, cv2, comtypes" >nul 2>nul
if errorlevel 1 (
    echo [!] 依赖不全，正在补装（走清华镜像）……
    "%DPY%" -m pip install -q -i %MIRROR% -r "%ROOT%\程序\requirements.txt"
)
echo [OK] 环境就位

rem ---------- 3. 声卡自检 ----------
echo.
"%DPY%" "%ROOT%\程序\check_audio.py"
if errorlevel 1 (
    echo.
    echo [X] 声卡没配好，语音条发不出去。
    echo     双击「1-声卡自检.bat」看修法。
    echo     只想先用文字聊的话，按任意键继续；否则请关掉本窗口。
    pause >nul
)

rem ---------- 4. 配置检查（ASCII 占位符，避开 GBK/UTF-8 不匹配）----------
echo.
findstr /c:"YOUR-KEY-HERE" "%ROOT%\角色\%CHARP%\config.yaml" >nul 2>nul
if not errorlevel 1 (
    echo [X] 还没填 API Key。
    echo     请打开  「角色\%CHARP%\config.yaml」
    echo       · llm.api_key  换成你自己的 Key
    echo       · target       换成你大号在小号里的显示名
    echo.
    pause
    exit /b 1
)
findstr /r /c:"^ *base_url:.*cloud.siliconflow.cn" "%ROOT%\角色\%CHARP%\config.yaml" >nul 2>nul
if not errorlevel 1 (
    echo [!] config.yaml 里的 base_url 写成了 cloud.siliconflow.cn。
    echo     那是官网控制台，会返回网页，回复时会报 Expecting value。
    echo     正确的是 https://api.siliconflow.cn/v1
    echo     程序会临时纠正并继续，但建议你自己改过来。
    echo.
)
echo [OK] 配置已填写

rem ---------- 4.5 大脑通行性检查（不通也能启动，只是提醒）----------
"%DPY%" "%ROOT%\程序\check_llm.py" >nul 2>nul
if errorlevel 1 (
    echo.
    echo [!] 大脑（API）没通 —— 她收得到你的消息，但回不出话。
    echo     双击「4-测大脑.bat」看具体是地址、模型名还是 Key 的问题。
    echo     按任意键仍然继续启动……
    pause >nul
) else (
    echo [OK] 大脑（API）已连通
)

rem ---------- 5. 微信在不在 ----------
tasklist /fi "imagename eq Weixin.exe" 2>nul | find /i "Weixin.exe" >nul
if errorlevel 1 (
    tasklist /fi "imagename eq WeChat.exe" 2>nul | find /i "WeChat.exe" >nul
)
if errorlevel 1 (
    echo.
    echo [X] 微信没在运行。
    echo     请先打开微信、用小号登录，并且不要最小化到托盘。
    echo.
    pause
    exit /b 1
)
echo [OK] 微信在运行

rem ---------- 6. 拉起小信使 ----------
echo.
echo ------------------------------------------------------------
echo   正在拉起小信使（负责发消息的那一半）……
echo ------------------------------------------------------------
start "小信使-别关这个窗口" /d "%ROOT%\小信使" cmd /k ""%WPY%" main.py"

echo   等它把 9877 端口起起来，最多 3 分钟（冷启动导依赖会慢，别急）
set /a tries=0

:waitloop
set /a tries+=1
>"%TEMP%\dn_health.ps1" echo try{Invoke-WebRequest -Uri 'http://127.0.0.1:9877/health' -UseBasicParsing -TimeoutSec 3 ^| Out-Null; exit 0}catch{exit 1}
powershell -NoProfile -ExecutionPolicy Bypass -File "%TEMP%\dn_health.ps1" >nul 2>nul
if not errorlevel 1 goto ready
if !tries! GEQ 36 goto failed
timeout /t 2 /nobreak >nul
goto waitloop

:ready
echo [OK] 小信使已就绪：http://127.0.0.1:9877
echo.
echo ------------------------------------------------------------
echo   她这会儿在干嘛（开机先看一眼，心里有数）
echo ------------------------------------------------------------
"%DPY%" "%ROOT%\程序\daily_life.py" --self
echo.
echo ------------------------------------------------------------
echo   %CHARP% 开始监听。想停下来就按 Ctrl+C
echo ------------------------------------------------------------
echo.
"%DPY%" "%ROOT%\程序\bridge.py" run
echo.
echo 已停止。
pause
exit /b 0

:failed
echo.
echo [X] 等了很久还是连不上小信使的 HTTP 接口。
echo     先看一眼「小信使-别关这个窗口」里的报错，常见原因：
echo       1. 小信使\config.yaml 里 http.enabled 不是 true
echo       2. 9877 端口被别的程序占了
echo       3. 小信使窗口里有 Python 报错
echo     如果那边还在滚动 pip / 导依赖，就是冷启动没完 ——
echo     按任意键继续等它 3 分钟；确实起不来就直接关本窗口。
pause >nul
set /a tries=0
goto waitloop
