#!/usr/bin/env python3
"""Inspect real cf32 IQ recordings or replay them through an IIO SDR transmitter.

Default mode is inspection. TX reads complex samples directly, without GFSK
modulation. Sample rate and TX LO must be provided explicitly: raw files do
not contain those parameters. One file/segment is played at a time.
"""

import argparse
import json
import math
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sdr_tools.recordings import (
    RX_PROFILES, add_recording_arguments, catalog_listing, inspect_recording,
    recording_window, resolve_recording, spectrum_peaks,
)
from sdr_tools.runtime import connect_file_source, run_graph


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    add_recording_arguments(parser)
    parser.add_argument("--mode", choices=("inspect", "tx"), default="inspect")
    parser.add_argument("--full-scan", action="store_true", help="inspect every sample in the selected segment")
    parser.add_argument("--spectrum", action="store_true", help="include sampled FFT peaks; requires NumPy and --sample-rate")
    parser.add_argument("--uri", help="transmitting SDR IIO URI, e.g. ip:192.168.1.102")
    parser.add_argument("--frequency", type=int, help="TX LO in Hz; normally the original wideband RX center")
    parser.add_argument("--bandwidth", type=int, help="IIO RF bandwidth in Hz; default is sample rate, up to 40 MHz")
    parser.add_argument("--amplitude", type=float, default=1.0, help="multiply recorded IQ by this value; no normalization")
    parser.add_argument("--attenuation", type=float, default=60.0, help="TX hardware attenuation in dB")
    parser.add_argument("--tx-channel", type=int, choices=(1, 2), default=1)
    parser.add_argument("--buffer-size", type=int, default=32768)
    parser.add_argument("--repeat", action="store_true", help="repeat the selected segment; otherwise stop at EOF")
    parser.add_argument("--duration", type=float, default=0, help="wall-clock time limit; 0 means EOF/Ctrl+C")
    args = parser.parse_args(argv)
    try:
        if args.list:
            if args.mode == "tx":
                raise ValueError("--list does not transmit; remove --mode tx")
            print(json.dumps(catalog_listing(args), indent=2, ensure_ascii=False))
            return 0
        if args.sample_rate is not None and args.sample_rate <= 0:
            raise ValueError("sample rate must be positive")
        if not math.isfinite(args.duration) or args.duration < 0:
            raise ValueError("duration must be finite and nonnegative")
        if not math.isfinite(args.amplitude) or args.amplitude <= 0:
            raise ValueError("amplitude must be finite and positive")
        if not math.isfinite(args.attenuation) or not 0 <= args.attenuation <= 89.75:
            raise ValueError("attenuation must be between 0 and 89.75 dB")
        if args.buffer_size < 1:
            raise ValueError("buffer size must be positive")
        if args.spectrum and args.sample_rate is None:
            raise ValueError("--spectrum requires an explicit --sample-rate")
        if args.mode == "tx":
            if not args.uri or args.sample_rate is None or args.frequency is None:
                raise ValueError("TX requires --uri, --sample-rate and --frequency; recording parameters are unknown")
            if not 520_833 <= args.sample_rate <= 61_440_000:
                raise ValueError("IIO TX sample rate must be 520833..61440000 and supported by the device")
            if not 47_000_000 <= args.frequency <= 6_000_000_000:
                raise ValueError("IIO TX LO must be 47 MHz..6 GHz and supported by the device")
            bandwidth = args.bandwidth if args.bandwidth is not None else min(args.sample_rate, 40_000_000)
            if not 200_000 <= bandwidth <= 40_000_000:
                raise ValueError("IIO RF bandwidth must be 200000..40000000 Hz")
        path, entry = resolve_recording(args)
        window = recording_window(path, args.start_sample, args.samples)
        report = inspect_recording(path, args.sample_rate, window.start, window.count, args.full_scan)
        report.update({"mode": args.mode, "catalog_entry": entry})
        if entry is not None:
            report["candidate_current_rx_profile"] = RX_PROFILES.get(entry["side"])
        if args.spectrum:
            report["spectrum"] = spectrum_peaks(path, args.sample_rate, window.start, window.count)
        if args.mode == "inspect":
            print(json.dumps(report, indent=2, ensure_ascii=False, allow_nan=False))
            return 1 if report["nonfinite_samples"] else 0
        if report["nonfinite_samples"]:
            raise ValueError("nonfinite IQ detected; inspect/repair the recording before TX")
        scaled_peak = report["peak_component"] * args.amplitude
        if not math.isfinite(scaled_peak) or scaled_peak > 1:
            raise ValueError("sampled I/Q components exceed normalized DAC range after scaling; lower --amplitude")
        report["tx"] = {"uri": args.uri, "channel": args.tx_channel, "lo_hz": args.frequency,
                        "sample_rate_hz": args.sample_rate, "bandwidth_hz": bandwidth,
                        "amplitude": args.amplitude, "attenuation_db": args.attenuation,
                        "repeat": args.repeat, "duration_limit_seconds": args.duration,
                        "resampling": False, "modulation": "already in recorded IQ"}
        print(json.dumps(report, indent=2, ensure_ascii=False, allow_nan=False), flush=True)
    except (ValueError, OSError, KeyError) as exc:
        parser.error(str(exc))
    except ImportError as exc:
        print(f"Spectrum inspection needs NumPy: {exc}", file=sys.stderr)
        return 2

    try:
        from gnuradio import blocks, gr, iio
    except ImportError as exc:
        print(f"Use the Linux Python containing GNU Radio IIO for TX: {exc}", file=sys.stderr)
        return 2
    tb = gr.top_block("RM SDR recorded IQ replay")
    source = connect_file_source(tb, blocks, gr, path, window, args.repeat, args.sample_rate)
    level = blocks.multiply_const_cc(args.amplitude)
    enabled = [True, True, False, False] if args.tx_channel == 1 else [False, False, True, True]
    sink = iio.fmcomms2_sink_fc32(args.uri, enabled, args.buffer_size, False)
    sink.set_len_tag_key("")
    sink.set_bandwidth(bandwidth)
    sink.set_frequency(args.frequency)
    sink.set_samplerate(args.sample_rate)
    sink.set_attenuation(args.tx_channel - 1, args.attenuation)
    sink.set_filter_params("Auto", "", 0, 0)
    # Hardware sink supplies the clock. No Throttle or second modulator here.
    tb.connect(source, level, sink)
    run_graph(tb, args.duration)
    print("Replay stopped (EOF, time limit, or Ctrl+C).", flush=True)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (RuntimeError, ValueError, OSError) as exc:
        print(f"IQ replay failed: {exc}", file=sys.stderr)
        raise SystemExit(2)
