#!/usr/bin/env python3
"""Create or repair the local Python environment, then optionally launch the app."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path


APP_FILE = "stock_keyworder.py"
MIN_PYTHON = (3, 9)
ROOT = Path(__file__).resolve().parent
VENV_DIR = ROOT / ".venv"
REQUIREMENTS = ROOT / "requirements.txt"
READY_FILE = VENV_DIR / ".stock_keyworder_env.json"


def venv_python() -> Path:
    if os.name == "nt":
        return VENV_DIR / "Scripts" / "python.exe"
    return VENV_DIR / "bin" / "python"


def fail(message: str, code: int = 1) -> None:
    print("")
    print(message)
    raise SystemExit(code)


def run(command: list[str], *, check: bool = True) -> subprocess.CompletedProcess[str]:
    print("+ " + " ".join(command), flush=True)
    return subprocess.run(command, cwd=ROOT, check=check, text=True)


def check_host_python() -> None:
    if sys.version_info < MIN_PYTHON:
        current = ".".join(map(str, sys.version_info[:3]))
        required = ".".join(map(str, MIN_PYTHON))
        fail(
            f"Python {required} or newer is required. Current Python is {current}.\n"
            "Download Python from https://www.python.org/downloads/"
        )


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def read_ready_data() -> dict[str, object]:
    try:
        data = json.loads(READY_FILE.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def create_or_repair_venv() -> None:
    python_path = venv_python()
    pyvenv_cfg = VENV_DIR / "pyvenv.cfg"
    if python_path.exists() and pyvenv_cfg.exists():
        return

    print("Creating virtual environment...")
    VENV_DIR.mkdir(exist_ok=True)
    run([sys.executable, "-m", "venv", str(VENV_DIR)])


def ensure_pip() -> None:
    python_path = str(venv_python())
    result = run([python_path, "-m", "pip", "--version"], check=False)
    if result.returncode == 0:
        return
    print("pip is missing inside the virtual environment; running ensurepip...")
    run([python_path, "-m", "ensurepip", "--upgrade"])


def can_import_pillow() -> bool:
    result = run([str(venv_python()), "-c", "from PIL import Image"], check=False)
    return result.returncode == 0


def requirements_changed() -> bool:
    if not REQUIREMENTS.exists():
        fail("requirements.txt was not found.")
    ready_data = read_ready_data()
    return ready_data.get("requirements_sha256") != sha256_file(REQUIREMENTS)


def install_requirements_if_needed() -> None:
    if not requirements_changed() and can_import_pillow():
        print("Environment already ready.")
        return

    print("Installing Python requirements...")
    run([str(venv_python()), "-m", "pip", "install", "-r", str(REQUIREMENTS)])
    READY_FILE.write_text(
        json.dumps(
            {
                "requirements_sha256": sha256_file(REQUIREMENTS),
                "host_python": sys.version.split()[0],
                "venv_python": str(venv_python()),
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )


def setup_environment() -> None:
    check_host_python()
    create_or_repair_venv()
    ensure_pip()
    install_requirements_if_needed()


def launch_app(app_args: list[str]) -> int:
    env = os.environ.copy()
    env.setdefault("PYTHONUTF8", "1")
    env.setdefault("TK_SILENCE_DEPRECATION", "1")
    command = [str(venv_python()), str(ROOT / APP_FILE), *app_args]
    print("Starting Stock Keyworder...")
    return subprocess.call(command, cwd=ROOT, env=env)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Set up Stock Keyworder environment.")
    parser.add_argument("--run", action="store_true", help="Launch the app after setup.")
    parser.add_argument(
        "app_args",
        nargs=argparse.REMAINDER,
        help="Optional arguments passed to stock_keyworder.py after --run.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    app_args = list(args.app_args)
    if app_args and app_args[0] == "--":
        app_args = app_args[1:]

    setup_environment()
    if args.run:
        return launch_app(app_args)

    print("Setup complete. Run start_mac.command or start_windows.bat to launch the app.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
