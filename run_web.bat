@echo off
setlocal

cd /d "%~dp0"

echo Starting Paper Feed Server...
echo Open http://localhost:8000 in your browser.
echo Press Ctrl+C to stop.

echo Generating feed.json (this may take a while)...
python get_RSS.py

start "" "http://localhost:8000"
python server.py
pause
