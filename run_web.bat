@echo off
setlocal EnableExtensions

cd /d "%~dp0"

rem Always use the project virtual environment, never a PATH-selected Python.
set "PYTHON=%~dp0.venv\Scripts\python.exe"
set "MODE=refresh"

if not "%~3"=="" goto :usage
if not "%~2"=="" goto :usage
if /I not "%~1"=="" (
  if /I "%~1"=="refresh" (
    set "MODE=refresh"
  ) else if /I "%~1"=="start" (
    set "MODE=start"
  ) else (
    goto :usage
  )
)

if not exist "%PYTHON%" goto :missing_venv

rem The unified CLI does the work (python -m paper_feed --help):
rem   run   = refresh RSS, then serve and open the browser
rem   start = serve existing local data and open the browser (no network)
rem Both reuse an already running Paper Feed on the port (just open it) and
rem refuse to start when another program holds the port.
set "COMMAND=run"
if /I "%MODE%"=="start" set "COMMAND=start"

echo Paper Feed: http://127.0.0.1:8000  (Ctrl+C stops the server)
if /I "%MODE%"=="refresh" (
  echo Refresh is the default and may access RSS networks, call OpenAI, and modify generated files.
)
"%PYTHON%" -m paper_feed %COMMAND% --port 8000
set "EXIT_CODE=%ERRORLEVEL%"
if not "%EXIT_CODE%"=="0" pause
exit /b %EXIT_CODE%

:missing_venv
echo Error: missing virtual environment interpreter:
echo   "%PYTHON%"
echo Create it with:
echo   py -m venv .venv
echo   .\.venv\Scripts\python.exe -m pip install -r requirements.txt
pause
exit /b 1

:usage
echo Usage:
echo   %~nx0           ^(default: refresh RSS, then start/open Paper Feed^)
echo   %~nx0 refresh   ^(explicit alias for refresh-first behavior^)
echo   %~nx0 start     ^(start/open existing local data without refreshing RSS^)
echo.
echo Refresh may use the network, call OpenAI, and modify generated files.
echo More commands: "%PYTHON%" -m paper_feed --help
exit /b 1
