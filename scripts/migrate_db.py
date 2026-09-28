#!/usr/bin/env python3
"""Deprecated alias for the platform bootstrap entry point."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from src.platform.database.bootstrap import main

if __name__ == "__main__":
    args = sys.argv[1:]
    action = "check" if "--check" in args else "apply"
    raise SystemExit(main([action, *[a for a in args if a != "--check"]]))
