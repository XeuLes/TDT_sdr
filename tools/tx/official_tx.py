#!/usr/bin/env python3
"""Transmit selectable official-profile RM information/interference waveforms."""

import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sdr_tools.profiles import PROJECT_LO, WAVES, official_description, select_waves
from sdr_tools.protocol import DEFAULT_DATA, load_payloads
from sdr_tools.runtime import DEFAULT_URI, power_setting, run_graph, tx_sink, validate_transport
from sdr_tools.waveform import official_iq


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--side", choices=("red", "blue"), default="red")
    parser.add_argument("--wave", nargs="+", choices=tuple(WAVES), default=["info"], help="one wave, or info plus one jam level")
    parser.add_argument("--data", type=Path, default=DEFAULT_DATA)
    parser.add_argument("--uri", default=DEFAULT_URI, help="transmitting IIO SDR")
    parser.add_argument("--duration", type=float, default=10, help="seconds; 0 until Ctrl+C")
    parser.add_argument("--attenuation", type=float, help="manual bench attenuation; otherwise use power.json calibration")
    parser.add_argument("--describe", action="store_true", help="print settings without GNU Radio or hardware")
    args = parser.parse_args(argv)
    try:
        waves = select_waves(args.wave)
        payloads = load_payloads(args.data, args.side)
        validate_transport(args.uri, args.duration, args.attenuation)
        power = power_setting(args.side, waves, args.uri, args.attenuation)
        description = official_description(args.side, waves)
        description.update(data=str(args.data), uri=args.uri, duration=args.duration, power=power)
        print(json.dumps(description, indent=2), flush=True)
        if args.describe:
            return 0
        if power["attenuation_db"] is None:
            raise ValueError("fill config/power.json for official dBm targets; explicit --attenuation enables uncalibrated bench TX")
        from gnuradio import blocks, gr
        iq = official_iq(payloads, args.side, waves)
        graph = gr.top_block("RM official-profile transmitter")
        source = blocks.vector_source_c(iq.tolist(), True, 1)
        sink = tx_sink(args.uri, PROJECT_LO[args.side], power["attenuation_db"])
        graph.connect(source, sink)
        run_graph(graph, args.duration)
        return 0
    except ImportError as exc:
        print(f"Use the Linux GNU Radio + IIO Python environment: {exc}", file=sys.stderr)
        return 2
    except (ValueError, OSError, KeyError, TypeError, RuntimeError) as exc:
        print(f"TX error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
