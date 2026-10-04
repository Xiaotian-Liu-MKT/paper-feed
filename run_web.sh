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
  echo "More commands: .venv/bin/python -m paper_feed --help"
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

# The unified CLI does the work (`.venv/bin/python -m paper_feed --help`):
#   run   = refresh RSS, then serve and open the browser
#   start = serve existing local data and open the browser (no network)
# Both reuse an already running Paper Feed on the port (just open it) and refuse
# to start when another program holds the port.
COMMAND=run
if [ "$MODE" = "start" ]; then COMMAND=start; fi

echo "Paper Feed: http://127.0.0.1:$PORT  (Ctrl+C stops the server)"
if [ "$MODE" = "refresh" ]; then
  echo "Refresh is the default and may access RSS networks, call OpenAI, and modify generated files."
fi
exec "$PYTHON" -m paper_feed "$COMMAND" --port "$PORT"
