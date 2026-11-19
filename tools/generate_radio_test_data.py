#!/usr/bin/env python3
"""Write packed-byte test data for RM_SDR_TX.grc. No SDR is opened."""

import argparse
import json
from pathlib import Path

from radio_test_frames import (
    add_stream_arguments, build_stream, describe, settings_from_arguments,
)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    add_stream_arguments(parser)
    parser.add_argument("--output", type=Path, default=Path("rm_test_position.bin"))
    args = parser.parse_args()
    try:
        settings = settings_from_arguments(args)
    except ValueError as exc:
        parser.error(str(exc))
    data = build_stream(settings)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_bytes(data)
    manifest = args.output.with_suffix(args.output.suffix + ".json")
    manifest.write_text(json.dumps(describe(settings), indent=2) + "\n", encoding="utf-8")
    print(f"Packed-byte file: {args.output.resolve()} ({len(data)} bytes)")
    print(f"Manifest: {manifest.resolve()}")
    print("Use a Byte File Source and GFSK Mod with Unpack=Yes; this is not an IQ file.")


if __name__ == "__main__":
    main()
