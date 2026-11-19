#!/usr/bin/env python3
"""Compatibility entry point; the synthetic transmitter is in tools/tx/."""
from pathlib import Path
import runpy

if __name__ == "__main__":
    runpy.run_path(str(Path(__file__).resolve().parent / "tx/rm_sdr_tx.py"), run_name="__main__")
