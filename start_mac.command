#!/bin/zsh
set -e

cd "$(dirname "$0")"

LOG_FILE="$PWD/stock_keyworder.log"
READY_FILE="$PWD/.venv/.stock_keyworder_ready"
export TK_SILENCE_DEPRECATION=1

echo "Starting Stock Keyworder..."
echo "[$(date '+%Y-%m-%d %H:%M:%S')] Starting Stock Keyworder" >> "$LOG_FILE"

if ! command -v python3 >/dev/null 2>&1; then
  echo "python3 was not found. Install Python 3 first:"
  echo "https://www.python.org/downloads/"
  read -k 1 "?Press any key to close..."
  exit 1
fi

if [ ! -d ".venv" ]; then
  echo "Creating virtual environment..."
  python3 -m venv .venv
  rm -f "$READY_FILE"
fi

source .venv/bin/activate

if [ ! -f "$READY_FILE" ]; then
  if ! python - <<'PY'; then
try:
    import PIL
except Exception:
    raise SystemExit(1)
PY
    echo "Installing requirements..."
    python -m pip install -r requirements.txt
  fi
  touch "$READY_FILE"
fi

set +e
echo "Opening browser UI..."
python stock_keyworder.py 2>&1 | tee -a "$LOG_FILE"
status=${pipestatus[1]}
set -e

if [ "$status" -ne 0 ]; then
  echo ""
  echo "Stock Keyworder exited with error code $status."
  echo "Log file: $LOG_FILE"
  read -k 1 "?Press any key to close..."
fi
