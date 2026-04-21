Blackjack bytecode distribution

Run the game:
  ./blackjack

Run the game automatically:
  ./blackjack --auto

Choose a trace file:
  ./blackjack --auto --trace-file blackjack_trace.txt

Built with:
  /usr/local/opt/python@3.12/bin/python3.12
  3.12.4 (main, Jun  7 2024, 04:37:10) [Clang 14.0.0 (clang-1400.0.29.202)]

This folder intentionally does not include blackjack.py. It contains compiled
Python bytecode and a launcher script pinned to the Python executable above.
This hides the source from casual viewing, but it is not strong code protection.
Python bytecode can still be inspected or decompiled by someone determined
enough.

Important: .pyc files are Python-version-specific. If ./blackjack reports
"Bad magic number in .pyc file", rebuild on the same computer/environment where
you plan to run it:
  python3 build_blackbox.py
