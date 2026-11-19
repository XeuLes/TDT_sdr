#!/usr/bin/env python3
"""RX diagnostic: distinguish a missing RF signal from a decode failure.

Stop the existing receiver before opening the same SDR with this tool. The
reported powers are digital dBFS estimates, not calibrated RF input dBm.
"""

import argparse
import json
import math
import signal
import sys
import threading
import time


def measure_iq(samples, sample_rate, target_offset, bandwidth):
    import numpy as np

    data = np.asarray(samples, dtype=np.complex64)
    if data.ndim != 1 or len(data) < 16 or not np.all(np.isfinite(data)):
        raise ValueError("need at least 16 finite, one-dimensional complex IQ samples")
    if not math.isfinite(sample_rate) or sample_rate <= 0:
        raise ValueError("sample rate must be finite and positive")
    if not all(math.isfinite(v) for v in (target_offset, bandwidth)) or bandwidth <= 0:
        raise ValueError("target offset and bandwidth must be finite; bandwidth must be positive")
    if abs(target_offset) + bandwidth / 2 >= sample_rate / 2:
        raise ValueError("target band must fit inside the sampled Nyquist band")

    window = np.hanning(len(data))
    spectrum = np.abs(np.fft.fftshift(np.fft.fft((data - np.mean(data)) * window))) ** 2
    offsets = np.fft.fftshift(np.fft.fftfreq(len(data), 1.0 / sample_rate))
    target = np.abs(offsets - target_offset) <= bandwidth / 2
    if not np.any(target):
        raise ValueError("target band is narrower than the FFT bin spacing")
    target_peak_index = np.flatnonzero(target)[np.argmax(spectrum[target])]
    global_peak_index = int(np.argmax(spectrum))
    band_power = float(np.sum(spectrum[target]) / (len(data) * np.sum(window * window)))
    total_power = float(np.mean(np.abs(data) ** 2))
    floor_power = 1e-20
    return {
        "iq_power_dbfs": 10 * math.log10(max(total_power, floor_power)),
        "target_power_dbfs": 10 * math.log10(max(band_power, floor_power)),
        "target_peak_offset_hz": float(offsets[target_peak_index]),
        "strongest_offset_hz": float(offsets[global_peak_index]),
        "peak_sample": float(np.max(np.abs(data))),
        "clipped_percent": float(100 * np.mean((np.abs(data.real) >= 0.98) |
                                               (np.abs(data.imag) >= 0.98))),
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--uri", default="ip:192.168.1.11")
    parser.add_argument("--rx-channel", type=int, choices=(1, 2), default=1)
    parser.add_argument("--frequency", type=int, default=432_600_000)
    parser.add_argument("--sample-rate", type=int, default=2_000_000)
    parser.add_argument("--target-offset", type=float, default=600_000)
    parser.add_argument("--bandwidth", type=float, default=200_000)
    parser.add_argument("--gain", type=float, default=40.0, help="fixed RX gain in dB")
    parser.add_argument("--duration", type=float, default=15.0, help="seconds; 0 until Ctrl+C")
    parser.add_argument("--interval", type=float, default=1.0)
    args = parser.parse_args()
    if (not all(math.isfinite(v) for v in
                (args.gain, args.duration, args.interval, args.target_offset, args.bandwidth))
            or not 0 <= args.gain <= 73 or args.duration < 0 or args.interval <= 0
            or args.bandwidth <= 0 or args.sample_rate <= 0
            or abs(args.target_offset) + args.bandwidth / 2 >= args.sample_rate / 2):
        parser.error("invalid gain, duration, interval, sample rate or target band")
    try:
        import numpy as np
        from gnuradio import gr, iio
    except ImportError as exc:
        print(f"Use the Python environment containing GNU Radio IIO and NumPy: {exc}", file=sys.stderr)
        return 2

    class Monitor(gr.sync_block):
        def __init__(self):
            gr.sync_block.__init__(self, "RF IQ probe", in_sig=[np.complex64], out_sig=None)
            self.last_report = time.monotonic()
            self.samples_since_report = 0

        def work(self, input_items, _output_items):
            data = input_items[0]
            self.samples_since_report += len(data)
            now = time.monotonic()
            elapsed = now - self.last_report
            if elapsed >= args.interval and len(data) >= 16:
                result = measure_iq(data[-65536:], args.sample_rate,
                                    args.target_offset, args.bandwidth)
                result["target_peak_rf_hz"] = args.frequency + result["target_peak_offset_hz"]
                result["observed_samples_per_second"] = self.samples_since_report / elapsed
                print(json.dumps({key: round(value, 3) for key, value in result.items()}), flush=True)
                self.last_report = now
                self.samples_since_report = 0
            return len(data)

    tb = gr.top_block("RM SDR raw RF probe")
    enabled = [True, True, False, False] if args.rx_channel == 1 else [False, False, True, True]
    source = iio.fmcomms2_source_fc32(args.uri, enabled, 65536)
    source.set_len_tag_key("")
    source.set_frequency(args.frequency)
    source.set_samplerate(args.sample_rate)
    source.set_gain_mode(args.rx_channel - 1, "manual")
    source.set_gain(args.rx_channel - 1, args.gain)
    source.set_quadrature(True)
    source.set_rfdc(True)
    source.set_bbdc(True)
    source.set_filter_params("Auto", "", 0, 0)
    monitor = Monitor()
    tb.connect(source, monitor)
    print(f"RX probe: {args.uri}, RX{args.rx_channel}, LO={args.frequency} Hz, "
          f"fixed gain={args.gain:g} dB; compare transmitter OFF versus ON.", flush=True)
    stopped = threading.Event()

    def request_stop(_signal, _frame):
        stopped.set()

    signal.signal(signal.SIGINT, request_stop)
    signal.signal(signal.SIGTERM, request_stop)
    tb.start()
    try:
        stopped.wait(args.duration if args.duration else None)
    finally:
        tb.stop()
        tb.wait()
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (RuntimeError, ValueError, OSError) as exc:
        print(f"RF probe failed: {exc}", file=sys.stderr)
        raise SystemExit(2)
