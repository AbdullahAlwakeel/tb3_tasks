"""Build a source-free launcher for blackjack.py.

This does not make Python code impossible to inspect. It creates a practical
distribution folder that contains Python bytecode plus an executable launcher,
so the plain .py source file is not included in the runnable package.
"""

from __future__ import annotations

import os
import py_compile
import shutil
import stat
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent
SOURCE_FILE = PROJECT_ROOT / "blackjack.py"
DIST_DIR = PROJECT_ROOT / "blackbox_dist"
BYTECODE_FILE = DIST_DIR / "blackjack_game.pyc"
LAUNCHER_FILE = DIST_DIR / "blackjack"
README_FILE = DIST_DIR / "README.txt"


README_TEXT_TEMPLATE = """Blackjack bytecode distribution

Run the game:
  ./blackjack

Run the game automatically:
  ./blackjack --auto

Choose a trace file:
  ./blackjack --auto --trace-file blackjack_trace.txt

Built with:
  {python_executable}
  {python_version}

This folder intentionally does not include blackjack.py. It contains compiled
Python bytecode and a launcher script pinned to the Python executable above.
This hides the source from casual viewing, but it is not strong code protection.
Python bytecode can still be inspected or decompiled by someone determined
enough.

Important: .pyc files are Python-version-specific. If ./blackjack reports
"Bad magic number in .pyc file", rebuild on the same computer/environment where
you plan to run it:
  python3 build_blackbox.py
"""


def ensure_source_exists() -> None:
    if not SOURCE_FILE.exists():
        raise FileNotFoundError(f"Expected source file not found: {SOURCE_FILE}")


def reset_dist_dir() -> None:
    if DIST_DIR.exists():
        shutil.rmtree(DIST_DIR)
    DIST_DIR.mkdir(parents=True)


def compile_bytecode() -> None:
    py_compile.compile(
        str(SOURCE_FILE),
        cfile=str(BYTECODE_FILE),
        doraise=True,
        optimize=2,
    )


def write_launcher() -> None:
    launcher_text = f"""#!/usr/bin/env sh
SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
exec "{sys.executable}" "$SCRIPT_DIR/blackjack_game.pyc" "$@"
"""
    LAUNCHER_FILE.write_text(launcher_text, encoding="utf-8")
    current_mode = LAUNCHER_FILE.stat().st_mode
    LAUNCHER_FILE.chmod(current_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


def write_readme() -> None:
    readme_text = README_TEXT_TEMPLATE.format(
        python_executable=sys.executable,
        python_version=sys.version.replace("\n", " "),
    )
    README_FILE.write_text(readme_text, encoding="utf-8")


def main() -> None:
    ensure_source_exists()
    reset_dist_dir()
    compile_bytecode()
    write_launcher()
    write_readme()

    print(f"Built source-free distribution in: {DIST_DIR}")
    print(f"Executable launcher: {LAUNCHER_FILE}")
    print("Run it with:")
    print(f"  {LAUNCHER_FILE} --auto")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"Build failed: {exc}", file=sys.stderr)
        raise SystemExit(1)
