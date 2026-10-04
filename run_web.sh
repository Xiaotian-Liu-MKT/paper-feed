#!/usr/bin/env sh
# POSIX launcher for Paper Feed (macOS/Linux).  Mirrors run_web.bat:
#   ./run_web.sh           refresh RSS, then start/open Paper Feed (default)
#   ./run_web.sh refresh   explicit alias for the default
#   ./run_web.sh start     start/open existing local data without refreshing RSS
# The port defaults to 8000 and can be changed with PAPER_FEED_PORT.
set -u

ROOT=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
cd "$ROOT" || exit 1

PYTHON="$ROOT/.venv/bin/python"
PORT="${PAPER_FEED_PORT:-8000}"
MODE=refresh

usage() {
  echo "Usage:"
  echo "  $0           (default: refresh RSS, then start/open Paper Feed)"
  echo "  $0 refresh   (explicit alias for refresh-first behavior)"
  echo "  $0 start     (start/open existing local data without refreshing RSS)"
  echo
  echo "Refresh may use the network, call OpenAI, and modify generated files."
  echo "Set PAPER_FEED_PORT to use a port other than 8000."
  exit 1
}

if [ "$#" -gt 1 ]; then usage; fi
case "${1:-refresh}" in
  refresh|REFRESH) MODE=refresh ;;
  start|START) MODE=start ;;
  *) usage ;;
esac

if [ ! -x "$PYTHON" ]; then
  echo "Error: missing virtual environment interpreter:"
  echo "  $PYTHON"
  echo "Create it with:"
  echo "  python3 -m venv .venv"
  echo "  .venv/bin/python -m pip install -r requirements.txt"
  exit 1
fi

URL="http://127.0.0.1:$PORT"

open_browser() {
  target="$URL/?t=$(date +%s)"
  if command -v open >/dev/null 2>&1 && [ "$(uname)" = "Darwin" ]; then
    open "$target" >/dev/null 2>&1
  elif command -v xdg-open >/dev/null 2>&1; then
    xdg-open "$target" >/dev/null 2>&1
  else
    echo "Open $target in your browser."
  fi
}

# Detect an already running Paper Feed via its read-only interactions API.
existing=0
if command -v curl >/dev/null 2>&1; then
  body=$(curl -fsS --max-time 2 "$URL/api/interactions" 2>/dev/null || true)
  if [ -n "$body" ]; then
    if printf '%s' "$body" | "$PYTHON" -c 'import json,sys; d=json.load(sys.stdin); sys.exit(0 if all(isinstance(d.get(k), list) for k in ("favorites","archived","hidden")) else 1)' 2>/dev/null; then
      existing=1
    else
      echo "Port $PORT is already in use by another program."
      echo "Stop it, or choose another port: PAPER_FEED_PORT=8001 $0 $MODE"
      exit 1
    fi
  fi
fi

echo "Starting Paper Feed Server..."
echo "Open $URL in your browser."

if [ "$MODE" = "refresh" ]; then
  echo
  echo "Refresh is the default and may access RSS networks, call OpenAI, and modify generated files."
  echo "Running RSS refresh before opening Paper Feed..."
  if ! "$PYTHON" "$ROOT/get_RSS.py"; then
    echo
    echo "Warning: Refresh did not publish new data (see the messages above)."
    echo "Exit code 1 = every RSS source failed; 2 = journals.dat or keywords.dat is empty."
    echo "Opening Paper Feed with the existing local data instead."
    echo
  fi
fi

if [ "$existing" = "1" ]; then
  echo "Paper Feed is already running; opening it..."
  open_browser
  exit 0
fi

echo "Press Ctrl+C to stop."
(sleep 2 && open_browser) &
exec "$PYTHON" "$ROOT/server.py" --port "$PORT"
