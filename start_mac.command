#!/bin/zsh
set -e

cd "$(dirname "$0")"

LOG_FILE="$PWD/stock_keyworder.log"
export TK_SILENCE_DEPRECATION=1

echo "Starting Stock Keyworder..."
echo "[$(date '+%Y-%m-%d %H:%M:%S')] Starting Stock Keyworder" >> "$LOG_FILE"

PYTHON_CMD=""

python_ok() {
  "$1" - <<'PY' >/dev/null 2>&1
import sys
raise SystemExit(0 if sys.version_info >= (3, 9) else 1)
PY
}

refresh_brew_path() {
  if [ -x "/opt/homebrew/bin/brew" ]; then
    eval "$(/opt/homebrew/bin/brew shellenv)"
  elif [ -x "/usr/local/bin/brew" ]; then
    eval "$(/usr/local/bin/brew shellenv)"
  fi
}

find_python() {
  refresh_brew_path
  if command -v python3 >/dev/null 2>&1 && python_ok "$(command -v python3)"; then
    PYTHON_CMD="$(command -v python3)"
    return 0
  fi
  return 1
}

install_python() {
  echo "Python 3.9 or newer was not found."

  refresh_brew_path
  if ! command -v brew >/dev/null 2>&1; then
    echo "Homebrew is required for automatic Python installation on macOS."
    echo "The official Homebrew installer will explain what it does before making changes."
    read -r "reply?Install Homebrew and Python automatically now? [y/N] "
    if [[ ! "$reply" =~ ^[Yy]$ ]]; then
      echo "Cancelled. Install Python manually from https://www.python.org/downloads/"
      read -k 1 "?Press any key to close..."
      exit 1
    fi
    if ! command -v curl >/dev/null 2>&1; then
      echo "curl was not found. Install Python manually from https://www.python.org/downloads/"
      read -k 1 "?Press any key to close..."
      exit 1
    fi
    /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
    refresh_brew_path
  fi

  if ! command -v brew >/dev/null 2>&1; then
    echo "Homebrew installation was not detected. Install Python manually from https://www.python.org/downloads/"
    read -k 1 "?Press any key to close..."
    exit 1
  fi

  echo "Installing Python with Homebrew..."
  brew install python
}

if ! find_python; then
  install_python
fi

if ! find_python; then
  echo "Python installation completed, but Python 3.9+ was not found in PATH."
  echo "Please reopen Terminal or install Python manually from https://www.python.org/downloads/"
  read -k 1 "?Press any key to close..."
  exit 1
fi

set +e
echo "Opening browser UI..."
"$PYTHON_CMD" setup_environment.py --run 2>&1 | tee -a "$LOG_FILE"
status=${pipestatus[1]}
set -e

if [ "$status" -ne 0 ]; then
  echo ""
  echo "Stock Keyworder exited with error code $status."
  echo "Log file: $LOG_FILE"
  read -k 1 "?Press any key to close..."
fi
