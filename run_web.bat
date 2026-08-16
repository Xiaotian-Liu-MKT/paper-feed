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

echo Starting Paper Feed Server...
echo Open http://127.0.0.1:8000 in your browser.
echo Press Ctrl+C to stop.

set "EXISTING_PAPER_FEED=0"

rem Do not start a second server.  A listener is accepted only when its read-only
rem interaction API returns Paper Feed's three-array state schema.
powershell -NoProfile -ExecutionPolicy Bypass -Command "$conn = Get-NetTCPConnection -LocalPort 8000 -State Listen -ErrorAction SilentlyContinue; if (-not $conn) { exit 0 }; try { $response = Invoke-WebRequest -Uri 'http://127.0.0.1:8000/api/interactions' -UseBasicParsing -TimeoutSec 2; $data = $response.Content | ConvertFrom-Json -ErrorAction Stop; $required = 'favorites','archived','hidden'; $missing = @($required | Where-Object { -not ($data.PSObject.Properties.Name -contains $_) }); $invalid = @($required | Where-Object { $data.$_ -isnot [System.Array] }); if ($null -ne $data -and $missing.Count -eq 0 -and $invalid.Count -eq 0) { exit 10 } } catch {}; Write-Host 'Port 8000 is already in use by PID(s):' (($conn | Select-Object -ExpandProperty OwningProcess -Unique) -join ', '); exit 20"
if errorlevel 20 (
  echo Close the process using port 8000, then run this script again.
  pause
  exit /b 1
)
if errorlevel 10 set "EXISTING_PAPER_FEED=1"

if /I "%MODE%"=="refresh" (
  echo.
  echo Refresh is the default and may access RSS networks, call OpenAI, and modify generated files.
  echo Running RSS refresh before opening Paper Feed...
  "%PYTHON%" "%~dp0get_RSS.py"
  if errorlevel 1 (
    echo Error: Refresh failed. No browser was opened.
    pause
    exit /b 1
  )
)

for /f %%i in ('powershell -NoProfile -Command "[DateTimeOffset]::Now.ToUnixTimeMilliseconds()"') do set "CACHE_BUSTER=%%i"

if "%EXISTING_PAPER_FEED%"=="1" (
  if /I "%MODE%"=="refresh" (
    echo Opening refreshed Paper Feed...
  ) else (
    echo Opening Paper Feed without refreshing RSS...
  )
  start "" "http://127.0.0.1:8000/?t=%CACHE_BUSTER%"
  exit /b 0
)

rem Delay opening the browser until the local server has had time to bind.
start "" powershell -NoProfile -WindowStyle Hidden -Command "Start-Sleep -Seconds 2; Start-Process 'http://127.0.0.1:8000/?t=%CACHE_BUSTER%'"
"%PYTHON%" "%~dp0server.py"
set "SERVER_EXIT=%ERRORLEVEL%"
pause
exit /b %SERVER_EXIT%

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
exit /b 1
