#!/usr/bin/env python3
"""Software-only official/capture IQ -> project filter settings -> gr-robomaster."""

import argparse
from collections import Counter
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sdr_tools.capture import check_recording, get_recording
from sdr_tools.profiles import SAMPLE_RATE, WAVES, official_description, select_waves
from sdr_tools.project import source_configuration
from sdr_tools.protocol import ApplicationParser, DEFAULT_DATA, frame_description, load_payloads
from sdr_tools.waveform import official_iq


def receive_branch(graph, source, settings, sps, wave):
    from gnuradio import analog, blocks, filter, robomaster
    from gnuradio.fft import window
    from gnuradio.filter import firdes
    channel = filter.freq_xlating_fir_filter_ccc(
        settings["decimation"], firdes.low_pass(1, SAMPLE_RATE, settings["cutoff_hz"], settings["transition_hz"]),
        settings["offset_hz"], SAMPLE_RATE)
    discriminator = analog.quadrature_demod_cf(1.5)
    post = filter.fir_filter_fff(1, firdes.low_pass(
        1, SAMPLE_RATE / settings["decimation"], settings["post_cutoff_hz"],
        settings["post_transition_hz"], window.WIN_BLACKMAN, 6.76))
    factory = robomaster.gfsk_receiver if wave == "info" else robomaster.gfsk_receiver_noise
    receiver = factory(settings["threshold"] * sps / 47, sps, 27)
    sink = blocks.vector_sink_b()
    graph.connect(source, channel, discriminator, post, receiver, sink)
    return sink


def evaluate(decoded, expected=None):
    parser = ApplicationParser()
    frames = parser.feed(decoded)
    matches = bool(frames)
    if expected is not None:
        matches = matches and set(expected).issubset({command for command, _, _ in frames})
        matches = matches and all(command in expected and payload == expected[command]
                                  for command, _, payload in frames)
    return {"passed": matches, **parser.summary(),
            "commands": {f"0x{k:04X}": v for k, v in Counter(f[0] for f in frames).items()},
            "examples": [frame_description(f) for f in frames[:3]]}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--side", choices=("red", "blue"), default="red")
    parser.add_argument("--wave", nargs="+", choices=tuple(WAVES), default=["info"])
    parser.add_argument("--data", type=Path, default=DEFAULT_DATA)
    parser.add_argument("--recording", help="use a validated field IQ file instead of generated IQ")
    parser.add_argument("--recording-dir", type=Path)
    parser.add_argument("--describe", action="store_true")
    args = parser.parse_args(argv)
    try:
        row = get_recording(args.recording, args.recording_dir) if args.recording else None
        side = row["side_label"] if row else args.side
        config = source_configuration(side)
        if config["sample_rate_hz"] != SAMPLE_RATE:
            raise ValueError("project RX sample rate changed; review the software-loopback adapter")
        waves = [row["wave"]] if row else select_waves(args.wave)
        payloads = None if row else load_payloads(args.data, side)
        sps = row["receiver_sps"] if row else config["sps"]
        source_description = ({"recording": row["id"], "file": row["path"],
                               "sample_rate_hz": row["playback_sample_rate_hz"],
                               "baseband_offset_hz": row["iq_offset_hz"], "wave": row["wave"]}
                              if row else official_description(side, waves))
        description = {"source": source_description, "receiver_project": config["file"],
                       "decoder": "gnuradio.robomaster", "sps": sps, "hardware": False}
        print(json.dumps(description, indent=2, ensure_ascii=False), flush=True)
        if args.describe:
            return 0
        from gnuradio import blocks, gr
        # This entry never imports/constructs IIO, Qt or ZeroMQ blocks.
        graph = gr.top_block("RM gr-robomaster software loopback")
        if row:
            path = check_recording(row)
            if sys.byteorder != "little":
                raise ValueError("raw CF32 playback requires a little-endian host")
            source = blocks.file_source(gr.sizeof_gr_complex, str(path), False)
        else:
            import numpy as np
            iq = official_iq(payloads, side, waves)
            # Several complete periods recover app frames straddling period edges.
            samples = np.r_[np.tile(iq, 3), np.zeros(40000, dtype=np.complex64)]
            source = blocks.vector_source_c(samples.tolist(), False, 1)
        sinks = {}
        for wave in waves:
            settings = dict(config["channels"][wave])
            if row:
                settings.update(offset_hz=row["iq_offset_hz"], cutoff_hz=row["channel_width_hz"] / 2,
                                transition_hz=20000, decimation=row["receiver_decimation"])
            sinks[wave] = receive_branch(graph, source, settings, sps, wave)
        graph.run()
        results = {}
        for wave, sink in sinks.items():
            expected = None if row else ({k: v for k, v in payloads.items() if k != 0x0A06}
                                        if wave == "info" else {0x0A06: payloads[0x0A06]})
            results[wave] = evaluate(bytes(sink.data()), expected)
        passed = all(row["passed"] for row in results.values())
        print(json.dumps({"event": "loopback", "passed": passed, "channels": results}, indent=2), flush=True)
        return 0 if passed else 1
    except ImportError as exc:
        print(f"Use the Linux GNU Radio + gr-robomaster Python environment: {exc}", file=sys.stderr)
        return 2
    except (ValueError, OSError, KeyError, TypeError, RuntimeError, StopIteration) as exc:
        print(f"Loopback error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
