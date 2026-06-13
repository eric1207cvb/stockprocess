#!/bin/zsh
set -e

cd "$(dirname "$0")"

LOG_FILE="$PWD/stock_keyworder.log"
export TK_SILENCE_DEPRECATION=1

echo "Starting Stock Keyworder..."
echo "[$(date '+%Y-%m-%d %H:%M:%S')] Starting Stock Keyworder" >> "$LOG_FILE"

if ! command -v python3 >/dev/null 2>&1; then
  echo "python3 was not found. Install Python 3 first:"
  echo "https://www.python.org/downloads/"
  read -k 1 "?Press any key to close..."
  exit 1
fi

if ! python3 - <<'PY'; then
import sys
raise SystemExit(0 if sys.version_info >= (3, 9) else 1)
PY
  echo "Python 3.9 or newer is required."
  echo "https://www.python.org/downloads/"
  read -k 1 "?Press any key to close..."
  exit 1
fi

set +e
echo "Opening browser UI..."
python3 setup_environment.py --run 2>&1 | tee -a "$LOG_FILE"
status=${pipestatus[1]}
set -e

if [ "$status" -ne 0 ]; then
  echo ""
  echo "Stock Keyworder exited with error code $status."
  echo "Log file: $LOG_FILE"
  read -k 1 "?Press any key to close..."
fi
