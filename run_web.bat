@echo off
setlocal

cd /d "%~dp0"

echo Starting Paper Feed Server...
echo Open http://localhost:8000 in your browser.
echo Press Ctrl+C to stop.

set "EXISTING_PAPER_FEED=0"

powershell -NoProfile -ExecutionPolicy Bypass -Command "$conn = Get-NetTCPConnection -LocalPort 8000 -State Listen -ErrorAction SilentlyContinue; if (-not $conn) { exit 0 }; try { $response = Invoke-WebRequest -Uri 'http://127.0.0.1:8000/' -UseBasicParsing -TimeoutSec 2; if ($response.Content -like '*<title>Paper Feed</title>*' -or $response.Content -like '*Paper Feed*') { exit 10 } } catch {}; Write-Host 'Port 8000 is already in use by PID(s):' (($conn | Select-Object -ExpandProperty OwningProcess -Unique) -join ', '); exit 20"
if errorlevel 20 (
  echo Close the process using port 8000, then run this script again.
  pause
  exit /b 1
)
if errorlevel 10 set "EXISTING_PAPER_FEED=1"

echo Generating feed.json (this may take a while)...
python get_RSS.py

for /f %%i in ('powershell -NoProfile -Command "[DateTimeOffset]::Now.ToUnixTimeMilliseconds()"') do set CACHE_BUSTER=%%i

if "%EXISTING_PAPER_FEED%"=="1" (
  echo Existing Paper Feed server detected. Opening refreshed page...
  start "" "http://localhost:8000/?t=%CACHE_BUSTER%"
  exit /b 0
)

start "" powershell -NoProfile -WindowStyle Hidden -Command "Start-Sleep -Seconds 2; Start-Process 'http://localhost:8000/?t=%CACHE_BUSTER%'"
python server.py
pause
