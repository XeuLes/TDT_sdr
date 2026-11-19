#!/usr/bin/env python3
"""Try candidate IQ layouts through gr-robomaster, without opening SDR hardware.

The test reports CRC-valid application frames. A match validates this software
decode configuration; it cannot recover the historical RF LO from raw IQ.
"""

import argparse
import json
import os
from pathlib import Path
import subprocess
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sdr_tools.recordings import DEFAULT_CATALOG, recording_window, resolve_recording
from sdr_tools.rx_profiles import DEFAULT_TEST_PROFILES, RECEIVE_PROFILES


def decode_log(output):
    configuration = summary = None
    examples = []
    for line in output.splitlines():
        try:
            item = json.loads(line)
        except ValueError:
            continue
        if not isinstance(item, dict):
            continue
        if item.get("event") == "configuration":
            configuration = item
        elif item.get("event") == "summary":
            summary = item
        elif item.get("event") == "frame" and len(examples) < 3:
            examples.append(item)
    return configuration, summary, examples


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--recording", help="catalog ID, e.g. red or blue")
    source.add_argument("--file", type=Path)
    parser.add_argument("--catalog", type=Path, default=DEFAULT_CATALOG)
    parser.add_argument("--recording-dir", type=Path)
    parser.add_argument("--side", choices=("red", "blue"), help="required with --file")
    parser.add_argument("--profiles", nargs="+", choices=tuple(RECEIVE_PROFILES),
                        default=list(DEFAULT_TEST_PROFILES),
                        help="default: three IQ layouts using 47 us/bit; SPS 52 profiles remain selectable")
    parser.add_argument("--start-sample", type=int, default=0)
    parser.add_argument("--samples", type=int, default=0, help="0 tests the entire file")
    args = parser.parse_args(argv)
    try:
        path, entry = resolve_recording(args)
        side = args.side or (entry["side"] if entry else None)
        if side is None:
            raise ValueError("--file requires --side red or blue")
        segment = recording_window(path, args.start_sample, args.samples)
    except (ValueError, OSError, KeyError) as exc:
        parser.error(str(exc))
    try:
        from gnuradio import analog, blocks, filter, gr, robomaster  # noqa: F401
        import numpy  # noqa: F401
    except (ImportError, OSError) as exc:
        print(f"GNU Radio / gr-robomaster cannot load: {exc}", file=sys.stderr)
        print("Run with the same Linux Python used by the existing RM_sdr receiver.", file=sys.stderr)
        return 2

    directory = Path(__file__).resolve().parents[1] / "outputs" / "rx-tests"
    directory.mkdir(parents=True, exist_ok=True)
    basename = "".join(c if c.isalnum() or c in "-_" else "_" for c in path.name)[:80] or "recording"
    results = []
    print(f"FILE: {path}\nSoftware only: File Source -> gr-robomaster -> application CRC", flush=True)
    environment = os.environ.copy()
    environment["PYTHONIOENCODING"] = "utf-8"
    for profile in dict.fromkeys(args.profiles):
        print(f"\nTEST: {profile} ({RECEIVE_PROFILES[profile]['label']})", flush=True)
        command = [sys.executable, "-u", "-B", str(Path(__file__).with_name("rm_sdr_file_rx.py")),
                   "--file", str(path), "--side", side, "--profile", profile, "--only", "info", "--fast",
                   "--start-sample", str(segment.start), "--samples", str(segment.count)]
        completed = subprocess.run(command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                   text=True, encoding="utf-8", errors="replace", env=environment)
        logfile = directory / f"{basename}-{profile}.log"
        logfile.write_text(completed.stdout, encoding="utf-8")
        configuration, summary, examples = decode_log(completed.stdout)
        stats = (summary or {}).get("channels", {}).get("info", {})
        valid = int(stats.get("crc_valid_frames", 0))
        fragment_bytes = int(stats.get("decoded_fragment_bytes", 0))
        result = {"profile": profile, "return_code": completed.returncode,
                  "configuration": configuration, "summary": summary, "frame_examples": examples,
                  "log": str(logfile), "crc_valid_frames": valid,
                  "decoded_fragment_bytes": fragment_bytes}
        results.append(result)
        if completed.returncode not in (0, 1) or summary is None:
            print(f"ERROR: see {logfile}", flush=True)
            print("\n".join(completed.stdout.splitlines()[-5:]), flush=True)
        else:
            print(f"CRC_VALID_FRAMES={valid}  DECODED_FRAGMENT_BYTES={fragment_bytes}", flush=True)
            for example in examples:
                print(json.dumps(example, ensure_ascii=True), flush=True)

    report = directory / f"{basename}-summary.json"
    report.write_text(json.dumps({"file": str(path), "side": side, "results": results,
                                 "note": "candidate decode tests; original RF center remains unverified"},
                                indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    matching = [result["profile"] for result in results if result["crc_valid_frames"] > 0
                and result["return_code"] == 0 and result["summary"] is not None]
    print(f"\nREPORT: {report}", flush=True)
    if matching:
        print("CRC PASS: " + ", ".join(matching), flush=True)
        print("These configurations decode the file; the historical RF LO is not identified by this test.", flush=True)
        return 0
    if any(result["return_code"] not in (0, 1) or result["summary"] is None for result in results):
        print("Test incomplete: resolve the reported software/parameter errors.", flush=True)
        return 2
    print("No CRC-valid frames with these candidates; this does not prove the recording is empty.", flush=True)
    return 1


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        print("\nRecording test cancelled.", file=sys.stderr)
        raise SystemExit(130)
    except (RuntimeError, OSError) as exc:
        print(f"Recording test failed: {exc}", file=sys.stderr)
        raise SystemExit(2)
