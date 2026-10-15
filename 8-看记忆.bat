@echo off
chcp 936 >nul
rem ── ROOT 必须在 cd 之前定下来（顺序反了，用相对路径运行会把 ROOT 算重一层）──
set "ROOT=%~dp0"
if "%ROOT:~-1%"=="\" set "ROOT=%ROOT:~0,-1%"
cd /d "%ROOT%"
set "PYTHONIOENCODING=gbk:replace"
setlocal enabledelayedexpansion
rem ── 现在是谁（写在标题上，省得切错了还不知道）──
set "CHARP="
set "DN_PY=%ROOT%\程序\.venv\Scripts\python.exe"
if not exist "%DN_PY%" set "DN_PY=python"
for /f "usebackq delims=" %%i in (`"%DN_PY%" "%ROOT%\程序\角色.py" --who 2^>nul`) do set "CHARP=%%i"
if not defined CHARP set "CHARP=白佰岚"
title %CHARP% - 她记住了什么
cd /d "%~dp0"

set "PY=程序\.venv\Scripts\python.exe"
if not exist "%PY%" (
  echo.
  echo   [X] 找不到 %PY%
  echo   先在 程序 目录里装好环境（见 使用说明.txt）。
  echo.
  pause
  exit /b 1
)

:menu
cls
echo ============================================================
echo   %CHARP% - 她记住了什么 / 她那边在发生什么
echo ============================================================
echo.
echo   [1] 她记住什么      长期记忆 + 历次复盘
echo   [2] 你们到哪一步    关系进度（按亲密度，不按天数）
echo   [3] 记忆目录        只看历次复盘的一句话摘要
echo   [4] 她今天在干嘛    日程 / 天气 / 发生了什么
echo   [5] 她的作息表      今天几点在做什么
echo   [6] 某个时段还剩什么  看素材池（默认看上午）
echo   [7] 素材还剩多少 ★  用掉就没了，见底要加料
echo   [8] 她正在演的大事  连续剧演到第几天了
echo   [9] 全部剧本列表    她的剧本，哪些演过、哪些没演
echo.
echo   [a] 世界观卡片库    她的卡片和触发关键词（含共用的）
echo   [b] 试匹配          打一句话，看她会翻出哪些卡
echo   [c] 省了多少        核心卡 / 词条库 / 每轮实际开销
echo.
echo   [d] 她愿意说多少    分享欲：现在第几档、今天能提自己几次
echo   [e] 今天能聊出什么  今天的事挂上了哪些话题
echo   [f] 试联想          打一句他的话，看会想起今天哪件事
echo   [g] 修记忆 ★        清掉她说错的那几句（要先关掉机器人）
echo   [h] 重开 ★★        把记忆全清、从第 1 档重新认识（要先关掉机器人）
echo   [i] 改好感度上限    她每天最多涨多少分（立刻生效，不用重启）
echo   [j] 她自检          她现在在哪、接下来两小时干嘛
echo   [k] 她欠着的事      她答应过你、还没还上的（叫你起床 / 待会再聊）
echo   [l] 今天这一天的偏移  她今天起晚了 / 差点迟到 / 失眠（每天都摇一个）
echo   [m] 五档 + 表白进度  区间 / 每档说多少 / 表白走到哪一步
echo   [0] 退出
echo.
set /p c=输入后回车:

if "%c%"=="1" "%PY%" "程序\journal.py" --show
if "%c%"=="2" "%PY%" "程序\relation.py" --show
if "%c%"=="3" "%PY%" "程序\journal.py" --list
if "%c%"=="4" "%PY%" "程序\daily_life.py" --show
if "%c%"=="5" "%PY%" "程序\daily_life.py" --plan
if "%c%"=="6" "%PY%" "程序\daily_life.py" --pool morning
if "%c%"=="7" "%PY%" "程序\daily_life.py" --stock
if "%c%"=="8" "%PY%" "角色\%CHARP%\大事.py"
if "%c%"=="9" "%PY%" "角色\%CHARP%\大事.py" --list
if /i "%c%"=="a" "%PY%" "程序\世界书.py"
if /i "%c%"=="b" goto wbhit
if /i "%c%"=="c" "%PY%" "程序\世界书.py" --stats
if /i "%c%"=="d" "%PY%" "程序\分享.py"
if /i "%c%"=="e" "%PY%" "程序\分享.py" --events
if /i "%c%"=="f" goto shhit
if /i "%c%"=="g" "%PY%" "程序\修记忆.py"
if /i "%c%"=="h" "%PY%" "程序\重开.py"
if /i "%c%"=="i" goto setcap
if /i "%c%"=="j" "%PY%" "程序\daily_life.py" --self
if /i "%c%"=="k" "%PY%" "程序\约定.py"
if /i "%c%"=="l" "%PY%" "程序\daily_life.py" --day
if /i "%c%"=="m" "%PY%" "程序\relation.py" --stages
if "%c%"=="0" exit /b 0

echo.
echo ------------------------------------------------------------
pause
goto menu

:setcap
echo.
echo   她每天最多涨多少分 —— 只影响「发展快慢」，越小越慢。
echo   默认 40。改完立刻生效，不用重启机器人。
echo.
echo   现在：
"%PY%" "程序\relation.py" --show | findstr "今日已涨"
echo.
set "v="
set /p "v=输入新的上限（直接回车跳过）: "
if not "!v!"=="" "%PY%" "程序\relation.py" --cap !v!
echo.
echo ------------------------------------------------------------
pause
goto menu

:wbhit
echo.
echo   打一句他说的话，看这一轮会翻出哪些卡片。
echo   例：今天在花房调律弹了很久
echo.
set "q="
set /p "q=输入后回车（直接回车跳过）: "
if not "!q!"=="" "%PY%" "程序\世界书.py" --hit "!q!"
echo.
echo ------------------------------------------------------------
pause
goto menu

:shhit
echo.
echo   打一句他说的话，看她会想起今天哪件事。
echo   例：我最近在看一本书
echo.
set "q="
set /p "q=输入后回车（直接回车跳过）: "
if not "!q!"=="" "%PY%" "程序\分享.py" --hit "!q!"
echo.
echo ------------------------------------------------------------
pause
goto menu
