#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
BOT_SCRIPT="$ROOT/discord_bot.py"
LOG_FILE="$ROOT/discord_bot.log"

if pgrep -f '[d]iscord_bot.py' >/dev/null; then
    exit 0
fi

cd "$ROOT"
nohup python3 "$BOT_SCRIPT" >> "$LOG_FILE" 2>&1 < /dev/null &