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
title %CHARP% - 装依赖（第一次跑会慢）
rem ── 临时目录必须跟着包走（和包同一个盘）─────────────────────────
rem    放 C 盘的话 pip 会报 WinError 17「无法将文件移到不同的磁盘驱动器」，
rem    而且会把 pip 自己弄坏（之后全报 No module named pip）。
set "TEMP=%ROOT%\_pip_tmp"
set "TMP=%ROOT%\_pip_tmp"
if not exist "%TEMP%" mkdir "%TEMP%" >nul 2>nul

set "DPY=%ROOT%\程序\.venv\Scripts\python.exe"
set "WPY=%ROOT%\小信使\venv\Scripts\python.exe"

echo ============================================================
echo    %CHARP% - 装依赖
echo ============================================================
echo.
echo   这一步只做两件事：建两个虚拟环境 + 把依赖装进去。
echo   装完就能双击「一键启动.bat」了，以后不用再跑这个。
echo   第一次要下几百兆，慢是正常的。
echo.

rem ── 1. 找解释器：优先 3.12 / 3.11 / 3.10 ───────────────────────
rem    rapidocr-onnxruntime（读图那条链）只支持 3.10~3.12，
rem    3.13 及以上装不上，所以要按版本挑，不能见 python 就用。
set "SYSBOT="
for %%V in (3.12 3.11 3.10) do (
    if not defined SYSBOT (
        py -%%V -c "import sys" >nul 2>nul
        if not errorlevel 1 set "SYSBOT=py -%%V"
    )
)
if not defined SYSBOT (
    python -c "import sys; raise SystemExit(0 if (3,10)<=sys.version_info<(3,13) else 1)" >nul 2>nul
    if not errorlevel 1 set "SYSBOT=python"
)
if not defined SYSBOT (
    echo [X] 没找到能用的 Python。
    echo.
    echo     这个项目要 Python 3.10 / 3.11 / 3.12。
    echo     3.13 及以上装不上读图那两个包。
    echo.
    echo     去 python.org 下个 3.12 装上，安装时勾上 Add python.exe to PATH。
    echo     已经装了 3.13 也不用卸 —— 本脚本会自动挑中 3.12。
    echo.
    pause
    exit /b 1
)
echo [OK] 用这个解释器：%SYSBOT%
echo.

rem ── 2. 两个虚拟环境 ───────────────────────────────────────────
if not exist "%DPY%" (
    echo [注意] 正在建「%CHARP%」的虚拟环境……
    %SYSBOT% -m venv "%ROOT%\程序\.venv"
    if errorlevel 1 (
        echo [X] 建虚拟环境失败，看看上面的报错。
        pause
        exit /b 1
    )
)
echo [OK] %CHARP%的虚拟环境就位

if not exist "%WPY%" (
    echo [注意] 正在建「小信使」的虚拟环境……
    %SYSBOT% -m venv "%ROOT%\小信使\venv"
    if errorlevel 1 (
        echo [X] 建虚拟环境失败，看看上面的报错。
        pause
        exit /b 1
    )
)
echo [OK] 小信使的虚拟环境就位
echo.

rem ── 3. 装依赖（一个源不给包就换下一个：清华，然后阿里，最后官方）──
echo 正在装「%CHARP%」的依赖……
call :pipinstall "%DPY%" "%ROOT%\程序\requirements.txt"
if not defined PIPOK goto pipfail

echo.
echo 正在装「小信使」的依赖……
call :pipinstall "%WPY%" "%ROOT%\小信使\requirements.txt"
if not defined PIPOK goto pipfail

rem ── 4. 复核：真的 import 得进来才算成功（别只看 pip 的退出码）──
echo.
echo 复核一遍……
"%DPY%" -c "import uiautomation, requests, yaml, edge_tts, PIL, rapidocr_onnxruntime, numpy, cv2, comtypes" >nul 2>nul
if errorlevel 1 (
    echo [X] %CHARP%的依赖还不全。把这一屏截图发我。
    pause
    exit /b 1
)
"%WPY%" -c "import uiautomation, pyperclip, pygame, yaml, websocket" >nul 2>nul
if errorlevel 1 (
    echo [X] 小信使的依赖还不全。把这一屏截图发我。
    pause
    exit /b 1
)

rd /s /q "%TEMP%" >nul 2>nul
echo.
echo ============================================================
echo    [OK] 装好了。
echo.
echo    接下来按顺序做：
echo      1. 打开微信、用小号登录，别最小化到托盘
echo      2. 双击「2-改配置.bat」，把 API Key 填进 程序\config.yaml
echo      3. 双击「一键启动.bat」
echo ============================================================
echo.
pause
exit /b 0

:pipfail
echo.
echo [X] 依赖没装上（三个源都试过了）。
echo     常见原因：
echo       1. 网络不通，或者公司代理拦了 pip。
echo          换个网再跑一次；有代理的话先挂上。
echo       2. Python 版本不对。这一屏最上面写着「用这个解释器：xxx」，
echo          要是显示 3.13 或更高，就去装个 3.12。
echo       3. 磁盘满了。
echo     把这一整屏截图发我，能看出来是哪一类。
rd /s /q "%TEMP%" >nul 2>nul
pause
exit /b 1

rem ── 子过程：一个源不给包就换下一个 ─────────────────────────────
:pipinstall
set "PYEXE=%~1"
set "REQ=%~2"
set "PIPOK="
for %%S in ("https://pypi.tuna.tsinghua.edu.cn/simple" "https://mirrors.aliyun.com/pypi/simple" "https://pypi.org/simple") do (
    if not defined PIPOK (
        echo    试 %%~S
        "%PYEXE%" -m pip install --disable-pip-version-check -q -i %%S -r "%REQ%"
        if not errorlevel 1 (
            set "PIPOK=1"
            echo    [OK] 装好了
        ) else (
            echo    [注意] 这个源没给全，换下一个
        )
    )
)
if not defined PIPOK (
    echo    [X] 三个源都不行
)
exit /b 0
