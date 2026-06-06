@echo off
setlocal

cd /d "%~dp0"

echo Starting Paper Feed Server...
echo Open http://localhost:8000 in your browser.
echo Press Ctrl+C to stop.

powershell -NoProfile -ExecutionPolicy Bypass -Command "$conn = Get-NetTCPConnection -LocalPort 8000 -State Listen -ErrorAction SilentlyContinue; if ($conn) { Write-Host 'Port 8000 is already in use by PID(s):' (($conn | Select-Object -ExpandProperty OwningProcess) -join ', '); exit 1 }"
if errorlevel 1 (
  echo Close the existing Paper Feed server window, then run this script again.
  pause
  exit /b 1
)

echo Generating feed.json (this may take a while)...
python get_RSS.py

set "CACHE_BUSTER=%RANDOM%%RANDOM%"
start "" powershell -NoProfile -WindowStyle Hidden -Command "Start-Sleep -Seconds 2; Start-Process 'http://localhost:8000/?t=%CACHE_BUSTER%'"
python server.py
pause
