@echo off
setlocal

cd /d "%~dp0"

echo Starting Paper Feed Server...
echo Open http://localhost:8000 in your browser.
echo Press Ctrl+C to stop.

start "" "http://localhost:8000"
python server.py
pause