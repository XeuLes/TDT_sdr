#!/usr/bin/env python3
"""Compatibility entry point; the hardware RX probe is in tools/rx/."""
from rx.sdr_rx_probe import main, measure_iq

if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (RuntimeError, ValueError, OSError) as exc:
        import sys
        print(f"RF probe failed: {exc}", file=sys.stderr)
        raise SystemExit(2)
