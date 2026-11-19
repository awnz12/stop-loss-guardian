#!/usr/bin/env python3
"""Entry point for the AI signal service.

    python3 run.py                 # dry-run polling loop (nothing is broadcast)
    python3 run.py --once          # one poll, then exit (dry-run)
    python3 run.py --live --once   # one poll; broadcast if the model decides so

See README.md for the full guide.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from signal_service.service import main

if __name__ == "__main__":
    sys.exit(main())
