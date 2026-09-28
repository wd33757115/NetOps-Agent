#!/usr/bin/env python3
"""Deprecated alias; all database initialization belongs to platform bootstrap."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from src.platform.database.bootstrap import main

if __name__ == "__main__":
    raise SystemExit(main(["apply", *sys.argv[1:]]))
