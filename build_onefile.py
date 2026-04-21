"""Build a single-file executable for the blackjack CLI.

The output is a standalone command-line executable. It embeds Python and the
game bytecode in one file, so users do not need blackjack.py or a local Python
installation to run it.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
import os
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent
SOURCE_FILE = PROJECT_ROOT / "blackjack.py"
VENV_PYINSTALLER = PROJECT_ROOT / ".venv" / "bin" / "pyinstaller"
BUILD_DIR = PROJECT_ROOT / "build" / "pyinstaller"
CONFIG_DIR = BUILD_DIR / "config"
SPEC_DIR = BUILD_DIR
DIST_DIR = PROJECT_ROOT / "portable_dist"
EXECUTABLE_NAME = "cardsim"


def require_source() -> None:
    if not SOURCE_FILE.exists():
        raise FileNotFoundError(f"Expected source file not found: {SOURCE_FILE}")


def require_pyinstaller() -> None:
    if not VENV_PYINSTALLER.exists():
        raise FileNotFoundError(
            "PyInstaller is not installed in .venv. Run:\n"
            "  python3 -m venv .venv\n"
            "  .venv/bin/python -m pip install pyinstaller"
        )


def reset_output_dirs() -> None:
    if DIST_DIR.exists():
        shutil.rmtree(DIST_DIR)
    BUILD_DIR.mkdir(parents=True, exist_ok=True)
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)


def build_one_file_executable() -> None:
    command = [
        str(VENV_PYINSTALLER),
        "--onefile",
        "--clean",
        "--name",
        EXECUTABLE_NAME,
        "--distpath",
        str(DIST_DIR),
        "--workpath",
        str(BUILD_DIR),
        "--specpath",
        str(SPEC_DIR),
        str(SOURCE_FILE),
    ]
    env = {
        **os.environ,
        "PYINSTALLER_CONFIG_DIR": str(CONFIG_DIR),
    }
    subprocess.run(command, cwd=PROJECT_ROOT, check=True, env=env)


def main() -> None:
    require_source()
    require_pyinstaller()
    reset_output_dirs()
    build_one_file_executable()

    executable = DIST_DIR / EXECUTABLE_NAME
    print(f"Built one-file executable: {executable}")
    print("Run it with:")
    print(f"  {executable} --auto")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"Build failed: {exc}", file=sys.stderr)
        raise SystemExit(1)
