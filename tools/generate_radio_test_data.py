#!/usr/bin/env python3
"""Compatibility entry point; the byte generator is in tools/tx/."""
from pathlib import Path
import runpy

if __name__ == "__main__":
    runpy.run_path(str(Path(__file__).resolve().parent / "tx/generate_radio_test_data.py"), run_name="__main__")
