#!/usr/bin/env sh
# POSIX launcher for Paper Feed (macOS/Linux).  Mirrors run_web.bat:
#   ./run_web.sh              start/open existing local data, no RSS refresh (default)
#   ./run_web.sh start        same as the default
#   ./run_web.sh run          refresh RSS first (network, may call OpenAI), then start
#   ./run_web.sh refresh      alias for run
#   ./run_web.sh start 8001   use another port (or set PAPER_FEED_PORT)
# Port: optional 2nd argument > PAPER_FEED_PORT > 8000.
set -u

ROOT=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
cd "$ROOT" || exit 1

PYTHON="$ROOT/.venv/bin/python"
PORT="${PAPER_FEED_PORT:-8000}"
MODE=start

usage() {
  echo "Usage / 用法:"
  echo "  $0 [start|run|refresh] [port]"
  echo "  $0              start: open existing local data, no refresh (default) / 打开现有数据，不刷新（默认）"
  echo "  $0 start        same as the default / 同默认"
  echo "  $0 run          refresh RSS first, then start / 先刷新 RSS 再打开"
  echo "  $0 refresh      alias for run / 同 run"
  echo "  $0 start 8001   use port 8001 (or set PAPER_FEED_PORT) / 使用 8001 端口（或设置 PAPER_FEED_PORT）"
  echo
  echo "run/refresh use the network, may call OpenAI (costs money) and rewrite generated files."
  echo "run/refresh 会联网，若已配置 OpenAI 密钥会产生费用，并改写导出文件。"
  echo "More commands / 更多命令: .venv/bin/python -m paper_feed --help"
  exit 1
}

if [ "$#" -gt 2 ]; then usage; fi
case "${1:-start}" in
  start|START) MODE=start ;;
  run|RUN|refresh|REFRESH) MODE=run ;;
  *) usage ;;
esac
if [ "$#" -eq 2 ]; then PORT=$2; fi
case "$PORT" in
  ''|*[!0-9]*)
    echo "Error: invalid port \"$PORT\" (expected a number such as 8001). 端口无效，应为数字，例如 8001。"
    echo
    usage ;;
esac

if [ ! -x "$PYTHON" ]; then
  echo "Error: missing virtual environment interpreter / 缺少项目虚拟环境："
  echo "  $PYTHON"
  echo "Create it with / 请先创建："
  echo "  python3 -m venv .venv"
  echo "  .venv/bin/python -m pip install -r requirements.txt"
  exit 1
fi

# The unified CLI does the work (`.venv/bin/python -m paper_feed --help`):
#   start = serve existing local data and open the browser (no network)
#   run   = refresh RSS (network; OpenAI if a key is configured), then serve and open
# Both reuse an already running Paper Feed on the port (just open it) and refuse
# to start when another program holds the port.
COMMAND=start
if [ "$MODE" = "run" ]; then COMMAND=run; fi

echo "Paper Feed: http://127.0.0.1:$PORT  (Ctrl+C stops the server / 按 Ctrl+C 停止服务)"
if [ "$MODE" = "start" ]; then
  echo "Opening existing local data without refreshing RSS. 使用现有本地数据打开，不刷新 RSS。"
  echo "To fetch new papers first: $0 run   如需先抓取新论文：$0 run"
else
  echo "Refreshing RSS first: this uses the network, may call OpenAI (costs money) and rewrites generated files."
  echo "先刷新 RSS：会联网，若已配置 OpenAI 密钥会产生费用，并改写导出文件。"
fi
exec "$PYTHON" -m paper_feed "$COMMAND" --port "$PORT"
