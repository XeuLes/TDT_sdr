#!/usr/bin/env python3
"""Decode recorded complex64 IQ with the existing RM_sdr OOT receiver.

No SDR is opened. --publish optionally sends the original decoded fragments
to the existing ROS bridge's ZeroMQ ports. Default output is CRC-validated
application frames and channel statistics on stdout.
"""

import argparse
import json
import math
from pathlib import Path
import sys
import threading

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sdr_tools.framing import ApplicationParser, describe_frame
from sdr_tools.recordings import (
    RX_PROFILES, add_recording_arguments, catalog_listing, recording_window, resolve_recording,
)
from sdr_tools.runtime import connect_file_source, run_graph


def channel_settings(args, side):
    profile = RX_PROFILES[side]
    configs = []
    for name, cutoff_fraction, post_fraction, threshold, port in (
        ("info", 0.135, 0.25, 1100, 5555),
        ("key1", 0.240, 0.48, 900, 6666),
        ("key2", 0.210, 0.41, 900, 6667),
    ):
        if args.only != "all" and args.only != name:
            continue
        offset = getattr(args, name + "_offset")
        if offset is None:
            offset = profile[name]
        cutoff = args.channel_width / 2 if args.channel_width is not None else cutoff_fraction * args.sample_rate
        transition = 0.02 * args.sample_rate
        if (not math.isfinite(offset) or not math.isfinite(cutoff) or cutoff <= 0
                or abs(offset) + cutoff + transition / 2 >= args.sample_rate / 2):
            raise ValueError(f"{name} passband exceeds IQ Nyquist range; set its offset/--channel-width or sample rate")
        configs.append({"name": name, "offset_hz": offset, "cutoff_hz": cutoff,
                        "transition_hz": transition, "post_cutoff_hz": post_fraction * args.sample_rate / 2,
                        "post_transition_hz": (0.05 if name == "info" else 0.04) * args.sample_rate / 2,
                        "threshold": threshold * args.sps / 47 * args.threshold_scale, "zmq_port": port})
    return configs


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    add_recording_arguments(parser)
    parser.add_argument("--mode", choices=("describe", "decode"), default="decode")
    parser.add_argument("--side", choices=("red", "blue"), help="RX profile; inferred from --recording if omitted")
    parser.add_argument("--only", choices=("all", "info", "key1", "key2"), default="all")
    parser.add_argument("--info-offset", type=float, help="information-wave baseband center in Hz")
    parser.add_argument("--key1-offset", type=float)
    parser.add_argument("--key2-offset", type=float)
    parser.add_argument("--channel-width", type=float, help="override full IQ channel-filter width in Hz")
    parser.add_argument("--sps", type=int, default=47, help="samples per symbol AFTER decimation by 2")
    parser.add_argument("--threshold-scale", type=float, default=1.0)
    parser.add_argument("--fast", action="store_true", help="decode a finite file as fast as possible")
    parser.add_argument("--repeat", action="store_true")
    parser.add_argument("--duration", type=float, default=0, help="wall-clock limit; 0 means EOF/Ctrl+C")
    parser.add_argument("--publish", action="store_true", help="bind localhost 5555/6666/6667 for existing ROS bridge")
    args = parser.parse_args(argv)
    try:
        if args.list:
            print(json.dumps(catalog_listing(args), indent=2, ensure_ascii=False))
            return 0
        if args.sample_rate is None or args.sample_rate <= 0:
            raise ValueError("provide --sample-rate; the raw recording's original rate is unknown")
        if args.sps < 4 or not math.isfinite(args.threshold_scale) or not 0 < args.threshold_scale < 2.7:
            raise ValueError("SPS must be >= 4 and threshold scale must be positive and below 2.7")
        if not math.isfinite(args.duration) or args.duration < 0:
            raise ValueError("duration must be finite and nonnegative")
        if args.fast and (args.repeat or args.publish):
            raise ValueError("--fast is for finite local decoding; omit it with --repeat or --publish")
        path, entry = resolve_recording(args)
        side = args.side or (entry["side"] if entry else None)
        if side is None:
            raise ValueError("--file requires --side red or blue")
        window = recording_window(path, args.start_sample, args.samples)
        channels = channel_settings(args, side)
        config = {"event": "configuration", "file": str(path), "side": side,
                  "sample_rate_hz": args.sample_rate, "start_sample": window.start,
                  "segment_samples": window.count, "sps_after_decimation": args.sps,
                  "repeat": args.repeat, "realtime": not args.fast, "publish": args.publish,
                  "channels": channels,
                  "parameter_note": "current RX profile is a candidate, not confirmed recording metadata"}
        print(json.dumps(config, ensure_ascii=False, allow_nan=False), flush=True)
        if args.mode == "describe":
            return 0
    except (ValueError, OSError, KeyError) as exc:
        parser.error(str(exc))

    try:
        import numpy as np
        from gnuradio import analog, blocks, filter, gr, robomaster
        from gnuradio.fft import window as fft_window
        from gnuradio.filter import firdes
        if args.publish:
            from gnuradio import zeromq
    except ImportError as exc:
        print(f"Use the Linux GNU Radio environment with gr-robomaster and NumPy: {exc}", file=sys.stderr)
        return 2

    print_lock = threading.Lock()

    class FrameMonitor(gr.sync_block):
        def __init__(self, name):
            gr.sync_block.__init__(self, name + " CRC monitor", in_sig=[np.uint8], out_sig=None)
            self.channel = name
            self.parser = ApplicationParser()

        def work(self, input_items, _output_items):
            data = input_items[0]
            for command, sequence, payload in self.parser.feed(bytes(data)):
                report = {"event": "frame", "channel": self.channel,
                          **describe_frame(command, sequence, payload)}
                with print_lock:
                    print(json.dumps(report, ensure_ascii=True), flush=True)
            return len(data)

    tb = gr.top_block("RM SDR recorded IQ receiver")
    source = connect_file_source(tb, blocks, gr, path, window, args.repeat, args.sample_rate,
                                 realtime=not args.fast)
    monitors = []
    for channel in channels:
        channel_filter = filter.freq_xlating_fir_filter_ccc(
            2, firdes.low_pass(1, args.sample_rate, channel["cutoff_hz"], channel["transition_hz"]),
            channel["offset_hz"], args.sample_rate,
        )
        demod = analog.quadrature_demod_cf(1.5)
        post = filter.fir_filter_fff(
            1, firdes.low_pass(1, args.sample_rate / 2, channel["post_cutoff_hz"],
                               channel["post_transition_hz"], fft_window.WIN_BLACKMAN, 6.76),
        )
        factory = robomaster.gfsk_receiver if channel["name"] == "info" else robomaster.gfsk_receiver_noise
        receiver = factory(channel["threshold"], args.sps, 27)
        monitor = FrameMonitor(channel["name"])
        monitors.append(monitor)
        tb.connect(source, channel_filter, demod, post, receiver, monitor)
        if args.publish:
            endpoint = f"tcp://127.0.0.1:{channel['zmq_port']}"
            # Six common positional args work with GNU Radio 3.10.1 and newer.
            publisher = zeromq.pub_sink(gr.sizeof_char, 1, endpoint, 100, False, -1)
            tb.connect(receiver, publisher)
    run_graph(tb, args.duration)
    summaries = {monitor.channel: monitor.parser.summary() for monitor in monitors}
    print(json.dumps({"event": "summary", "channels": summaries}), flush=True)
    return 0 if any(s["crc_valid_frames"] for s in summaries.values()) else 1


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (RuntimeError, ValueError, OSError) as exc:
        print(f"Recording decode failed: {exc}", file=sys.stderr)
        raise SystemExit(2)
