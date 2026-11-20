#!/usr/bin/env python3
"""Launch the existing project RX graph; adapt SPS/URI/jam3 in memory only."""

import argparse
import json
import os
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sdr_tools.project import adapted_tree, source_configuration


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--side", choices=("red", "blue"), default="red")
    parser.add_argument("--sps", type=int, choices=(47, 52), help="47 official profile; 52 validated field recordings")
    parser.add_argument("--uri", help="override the existing receiver IIO URI")
    parser.add_argument("--jam3", action="store_true", help="retune the second existing jam branch from jam2 to jam3")
    parser.add_argument("--describe", action="store_true")
    args = parser.parse_args(argv)
    try:
        config = source_configuration(args.side)
        active = ("info", "jam1", "jam3" if args.jam3 else "jam2")
        description = {"project_file": config["file"], "lo_hz": config["lo_hz"],
                       "sample_rate_hz": config["sample_rate_hz"], "sps": args.sps or config["sps"],
                       "uri": args.uri or "use project setting",
                       "channels": {name: {"offset_hz": config["channels"][name]["offset_hz"],
                                            "zmq_port": port}
                                    for name, port in zip(active, (5555, 6666, 6667))}}
        path, tree = adapted_tree(args.side, args.sps, args.uri, args.jam3)
        print(json.dumps(description, indent=2), flush=True)
        if args.describe:
            return 0
        if sys.platform.startswith("linux") and not (os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY")):
            os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        sys.path.insert(0, str(path.parent))
        namespace = {"__name__": "rm_existing_receiver", "__file__": str(path)}
        exec(compile(tree, str(path), "exec"), namespace)
        previous = sys.argv
        try:
            sys.argv = [str(path)]
            namespace["main"]()
        finally:
            sys.argv = previous
        return 0
    except ImportError as exc:
        print(f"Use the project's Linux GNU Radio + Qt + IIO + gr-robomaster environment: {exc}", file=sys.stderr)
        return 2
    except (ValueError, OSError, KeyError, RuntimeError, StopIteration) as exc:
        print(f"Project RX error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
