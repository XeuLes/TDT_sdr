#!/usr/bin/env python3
"""Replay a validated field IQ file, using its stored timing and RF destination."""

import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sdr_tools.capture import check_recording, get_recording, list_recordings
from sdr_tools.runtime import DEFAULT_URI, run_graph, tx_sink, validate_transport


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--recording", help="red, blue, blue-ganrao-1, blue-ganrao-2 or blue-ganrao-3")
    parser.add_argument("--recording-dir", type=Path)
    parser.add_argument("--uri", default=DEFAULT_URI)
    parser.add_argument("--attenuation", type=float, default=60)
    parser.add_argument("--duration", type=float, default=0, help="seconds; 0 plays to EOF / Ctrl+C")
    parser.add_argument("--repeat", action="store_true")
    parser.add_argument("--list", action="store_true")
    parser.add_argument("--describe", action="store_true")
    args = parser.parse_args(argv)
    try:
        if args.list:
            print(f"{'recording':<20} {'wave':<6} {'status':<12} {'rate':<9} {'sps':<4} TX_LO_Hz")
            for row in list_recordings(args.recording_dir):
                status = "ready" if row["usable"] else "unconfirmed"
                rate, sps, lo = (row[k] if row[k] is not None else "-" for k in
                                 ("playback_sample_rate_hz", "receiver_sps", "tx_lo_hz"))
                print(f"{row['id']:<20} {row['wave']:<6} {status:<12} {rate:<9} {sps:<4} {lo}")
            return 0
        row = get_recording(args.recording, args.recording_dir)
        validate_transport(args.uri, args.duration, args.attenuation)
        print(json.dumps({"recording": row["id"], "file": row["path"],
                          "sample_rate_hz": row["playback_sample_rate_hz"], "receiver_sps": row["receiver_sps"],
                          "baseband_offset_hz": row["iq_offset_hz"], "tx_lo_hz": row["tx_lo_hz"],
                          "target_rf_hz": row["target_rf_hz"], "uri": args.uri, "attenuation_db": args.attenuation,
                          "repeat": args.repeat, "duration": args.duration}, indent=2, ensure_ascii=False), flush=True)
        if args.describe:
            return 0
        if sys.byteorder != "little":
            raise ValueError("raw CF32 replay requires a little-endian host")
        path = check_recording(row)
        from gnuradio import blocks, gr
        graph = gr.top_block("RM field IQ replay")
        source = blocks.file_source(gr.sizeof_gr_complex, str(path), args.repeat)
        sink = tx_sink(args.uri, row["tx_lo_hz"], args.attenuation)
        graph.connect(source, sink)
        run_graph(graph, args.duration)
        return 0
    except ImportError as exc:
        print(f"Use the Linux GNU Radio + IIO Python environment: {exc}", file=sys.stderr)
        return 2
    except (ValueError, OSError, KeyError, TypeError, RuntimeError) as exc:
        print(f"Replay error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
