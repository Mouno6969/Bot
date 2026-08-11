#!/usr/bin/env bash
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$PROJECT_DIR"

if [[ ! -f .env ]]; then
  echo "Missing .env. Copy .env.example to .env and set the required values." >&2
  exit 1
fi

set -a
# shellcheck disable=SC1091
source .env
set +a

if [[ "${1:-}" == "--foreground" ]]; then
  exec python3 -u fb_bot.py
fi

if [[ -f bot_pid.txt ]] && kill -0 "$(cat bot_pid.txt)" 2>/dev/null; then
  echo "Bot is already running with PID $(cat bot_pid.txt)." >&2
  exit 1
fi

nohup python3 -u fb_bot.py > bot_logs.txt 2>&1 &
echo $! > bot_pid.txt
echo "Bot started with PID $(cat bot_pid.txt). Logs: $PROJECT_DIR/bot_logs.txt"
