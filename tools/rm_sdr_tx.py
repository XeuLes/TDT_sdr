#!/usr/bin/env python3
"""GFSK lab source: software loopback, finite IQ export, or explicit IIO TX.

Defaults match the checked-in RM_sdr receiver's sampling configuration. They
are not a verified official over-the-air profile. --mode tx opens an SDR sink;
the default loopback and the iq mode do not open any hardware.
The same tx mode supports an antenna link or an attenuated coaxial link;
the RF connection is determined by what is attached to the SDR TX port.
"""

import argparse
import json
import math
from pathlib import Path
import signal
import sys
import threading

from radio_test_frames import (
    add_stream_arguments, build_stream, demo_payload, describe,
    parse_application_stream, settings_from_arguments,
)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    add_stream_arguments(parser)
    parser.add_argument(
        "--mode", choices=("describe", "loopback", "iq", "tx"), default="loopback",
        help="tx drives the SDR TX port for either antennas or an attenuated coaxial link",
    )
    parser.add_argument("--uri", help="IIO URI of the transmitting SDR, e.g. ip:192.168.1.102")
    parser.add_argument("--frequency", type=int, help="TX LO in Hz; defaults to the selected RX channel")
    parser.add_argument("--deviation", type=float, default=20_000.0, help="test frequency deviation in Hz")
    parser.add_argument("--bt", type=float, default=0.5, help="test Gaussian BT")
    parser.add_argument("--amplitude", type=float, default=0.2, help="normalized complex amplitude (0, 1]")
    parser.add_argument("--attenuation", type=float, default=60.0, help="TX hardware attenuation in dB")
    parser.add_argument("--tx-channel", type=int, choices=(1, 2), default=1)
    parser.add_argument("--buffer-size", type=int, default=32768)
    parser.add_argument("--duration", type=float, default=10.0, help="TX seconds; 0 means until Ctrl+C")
    parser.add_argument("--output-iq", type=Path, default=Path("outputs/rm_test_position.cf32"))
    args = parser.parse_args()
    try:
        settings = settings_from_arguments(args)
    except ValueError as exc:
        parser.error(str(exc))
    if not math.isfinite(args.deviation) or not 0 < args.deviation < settings.sample_rate / 8:
        parser.error("deviation must be positive and below sample_rate/8 for this test flowgraph")
    if not math.isfinite(args.bt) or args.bt <= 0:
        parser.error("BT must be finite and positive")
    if not math.isfinite(args.amplitude) or not 0 < args.amplitude <= 1:
        parser.error("amplitude must be in (0, 1]")
    if not math.isfinite(args.attenuation) or not 0 <= args.attenuation <= 89.75:
        parser.error("attenuation must be between 0 and 89.75 dB")
    if not math.isfinite(args.duration) or args.duration < 0 or args.buffer_size < 1:
        parser.error("duration must be finite and nonnegative; buffer size must be positive")
    if args.mode == "loopback" and (settings.sps < 4 or settings.sps % 2):
        parser.error("loopback decimates by two and requires an even SPS >= 4")
    if args.mode == "tx" and not args.uri:
        parser.error("--mode tx requires --uri for the transmitting SDR")
    frequency = args.frequency if args.frequency is not None else settings.frequency
    if not 47_000_000 <= frequency <= 6_000_000_000:
        parser.error("TX frequency must be within 47 MHz to 6 GHz and supported by your device")

    metadata = describe(settings)
    metadata.update({
        "mode": args.mode, "frequency_hz": frequency, "deviation_hz": args.deviation,
        "bt": args.bt, "amplitude": args.amplitude,
        "sensitivity": 2 * math.pi * args.deviation / settings.sample_rate,
    })
    print(json.dumps(metadata, indent=2), flush=True)
    if args.mode == "describe":
        return 0

    try:
        from gnuradio import analog, blocks, digital, filter, gr
        from gnuradio.filter import firdes
        if args.mode == "tx":
            from gnuradio import iio
        if args.mode == "loopback":
            from gnuradio import robomaster
    except ImportError as exc:
        print(f"Cannot load GNU Radio/OOT modules: {exc}", file=sys.stderr)
        print("Use the same system Python as the installed GNU Radio and gr-robomaster.", file=sys.stderr)
        return 2

    tb = gr.top_block("RM SDR synthetic GFSK test")
    source = blocks.vector_source_b(list(build_stream(settings)), args.mode == "tx", 1)
    modulator = digital.gfsk_mod(
        samples_per_symbol=settings.sps,
        sensitivity=metadata["sensitivity"], bt=args.bt,
        verbose=False, log=False, do_unpack=True,
    )
    level = blocks.multiply_const_cc(args.amplitude)
    tb.connect(source, modulator, level)

    if args.mode == "tx":
        enabled = [True, True, False, False] if args.tx_channel == 1 else [False, False, True, True]
        sink = iio.fmcomms2_sink_fc32(args.uri, enabled, args.buffer_size, False)
        sink.set_len_tag_key("")
        sink.set_bandwidth(settings.sample_rate)
        sink.set_frequency(frequency)
        sink.set_samplerate(settings.sample_rate)
        sink.set_attenuation(args.tx_channel - 1, args.attenuation)
        sink.set_filter_params("Auto", "", 0, 0)
        tb.connect(level, sink)
        print(f"TX: {args.uri}, TX{args.tx_channel}, {frequency / 1e6:.6f} MHz, "
              f"attenuation={args.attenuation:g} dB, duration={args.duration:g} s", flush=True)
        stopped = threading.Event()

        def stop_requested(_signal, _frame):
            stopped.set()

        signal.signal(signal.SIGINT, stop_requested)
        signal.signal(signal.SIGTERM, stop_requested)
        tb.start()
        try:
            stopped.wait(args.duration if args.duration else None)
        finally:
            tb.stop()
            tb.wait()
        return 0

    if args.mode == "iq":
        args.output_iq.parent.mkdir(parents=True, exist_ok=True)
        sink = blocks.file_sink(gr.sizeof_gr_complex, str(args.output_iq), False)
        tb.connect(level, sink)
        tb.run()
        sink.close()
        args.output_iq.with_suffix(args.output_iq.suffix + ".json").write_text(
            json.dumps(metadata, indent=2) + "\n", encoding="utf-8")
        print(f"IQ written: {args.output_iq.resolve()} (complex float32, sample rate above)")
        return 0

    # The RF source is already centered at baseband, so this digital shift is zero.
    channel = filter.freq_xlating_fir_filter_ccc(
        2, firdes.low_pass(1.0, settings.sample_rate,
                          0.135 * settings.sample_rate, 0.02 * settings.sample_rate),
        0, settings.sample_rate,
    )
    discriminator = analog.quadrature_demod_cf(1.5)
    rx_rate = settings.sample_rate / 2
    post_filter = filter.fir_filter_fff(
        1, firdes.low_pass(1.0, rx_rate, 0.25 * rx_rate, 0.05 * rx_rate)
    )
    threshold_scale = (settings.sps // 2) / 47.0
    if settings.message == "key":
        receiver = robomaster.gfsk_receiver_noise(900 * threshold_scale, settings.sps // 2, 27)
    else:
        receiver = robomaster.gfsk_receiver(1100 * threshold_scale, settings.sps // 2, 27)
    decoded = blocks.vector_sink_b()
    tb.connect(level, channel, discriminator, post_filter, receiver, decoded)
    tb.run()
    frames = parse_application_stream(bytes(decoded.data()))
    command, payload = demo_payload(settings.message)
    expected = [(command, sequence & 0xFF, payload) for sequence in range(settings.packets)]
    if frames != expected:
        print(f"LOOPBACK FAIL: expected {len(expected)} frames, decoded {len(frames)}; "
              f"decoded (command, seq)={[(hex(c), s) for c, s, _ in frames]}", file=sys.stderr)
        return 1
    print(f"LOOPBACK PASS: {len(frames)} frames; command=0x{command:04X}; payload and sequence match.")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (RuntimeError, OSError) as exc:
        print(f"SDR test failed: {exc}", file=sys.stderr)
        raise SystemExit(2)
