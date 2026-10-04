@echo off
setlocal EnableExtensions

rem Switch the console to UTF-8 so the Chinese messages (and Python's UTF-8
rem output) display correctly; the previous code page is restored on exit.
for /f "tokens=2 delims=:" %%c in ('chcp') do set "OLD_CP=%%c"
chcp 65001 >nul

cd /d "%~dp0"

rem Always use the project virtual environment, never a PATH-selected Python.
set "PYTHON=%~dp0.venv\Scripts\python.exe"
rem Double-click = start: no RSS refresh, no network, no OpenAI cost.
set "MODE=start"
rem Port: optional 2nd argument > PAPER_FEED_PORT > 8000.
set "PORT=8000"
if defined PAPER_FEED_PORT set "PORT=%PAPER_FEED_PORT%"

if not "%~3"=="" goto :usage
if /I not "%~1"=="" (
  if /I "%~1"=="start" (
    set "MODE=start"
  ) else if /I "%~1"=="run" (
    set "MODE=run"
  ) else if /I "%~1"=="refresh" (
    set "MODE=run"
  ) else (
    goto :usage
  )
)
if not "%~2"=="" set "PORT=%~2"
echo(%PORT%| findstr /r /x "[0-9][0-9]*" >nul || goto :bad_port

if not exist "%PYTHON%" goto :missing_venv

rem The unified CLI does the work (python -m paper_feed --help):
rem   start = serve existing local data and open the browser (no network)
rem   run   = refresh RSS (network; OpenAI if a key is configured), then serve and open
rem Both reuse an already running Paper Feed on the port (just open it) and
rem refuse to start when another program holds the port.
set "COMMAND=start"
if /I "%MODE%"=="run" set "COMMAND=run"

echo Paper Feed: http://127.0.0.1:%PORT%  (Ctrl+C stops the server / 按 Ctrl+C 停止服务)
if /I "%MODE%"=="start" (
  echo Opening existing local data without refreshing RSS. 使用现有本地数据打开，不刷新 RSS。
  echo To fetch new papers first: %~nx0 run   如需先抓取新论文：%~nx0 run
) else (
  echo Refreshing RSS first: this uses the network, may call OpenAI ^(costs money^) and rewrites generated files.
  echo 先刷新 RSS：会联网，若已配置 OpenAI 密钥会产生费用，并改写导出文件。
)
"%PYTHON%" -m paper_feed %COMMAND% --port %PORT%
set "EXIT_CODE=%ERRORLEVEL%"
if not "%EXIT_CODE%"=="0" pause
goto :finish

:bad_port
echo Error: invalid port "%PORT%" (expected a number such as 8001). 端口无效，应为数字，例如 8001。
echo.
goto :usage

:missing_venv
echo Error: missing virtual environment interpreter / 缺少项目虚拟环境：
echo   "%PYTHON%"
echo Create it with / 请先创建：
echo   py -m venv .venv
echo   .\.venv\Scripts\python.exe -m pip install -r requirements.txt
pause
set "EXIT_CODE=1"
goto :finish

:usage
echo Usage / 用法:
echo   %~nx0 [start^|run^|refresh] [port]
echo   %~nx0              start: open existing local data, no refresh ^(default^) / 打开现有数据，不刷新（默认）
echo   %~nx0 start        same as the default / 同默认
echo   %~nx0 run          refresh RSS first, then start / 先刷新 RSS 再打开
echo   %~nx0 refresh      alias for run / 同 run
echo   %~nx0 start 8001   use port 8001 ^(or set PAPER_FEED_PORT^) / 使用 8001 端口（或设置 PAPER_FEED_PORT）
echo.
echo run/refresh use the network, may call OpenAI ^(costs money^) and rewrite generated files.
echo run/refresh 会联网，若已配置 OpenAI 密钥会产生费用，并改写导出文件。
echo More commands / 更多命令: "%PYTHON%" -m paper_feed --help
set "EXIT_CODE=1"
goto :finish

:finish
if defined OLD_CP chcp %OLD_CP% >nul
exit /b %EXIT_CODE%
