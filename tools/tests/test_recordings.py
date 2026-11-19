"""Real file bounds, IQ corruption, catalog relocation and CLI preflight checks."""

import contextlib
import io
import json
from pathlib import Path
import struct
import sys
import tempfile
import unittest
from unittest import mock

TOOLS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(TOOLS))

from sdr_tools.recordings import (
    DEFAULT_CATALOG, catalog_listing, inspect_recording, load_catalog, recording_window,
    resolve_recording, spectrum_peaks,
)
from tx.rm_sdr_replay import main as replay_main
from rx.rm_sdr_file_rx import main as rx_main

try:
    import numpy as np
except ImportError:
    np = None


class RecordingTest(unittest.TestCase):
    def setUp(self):
        temporary_root = (TOOLS / "outputs").resolve()
        temporary_root.mkdir(exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(dir=temporary_root)
        if not Path(self.temp.name).resolve().is_relative_to(temporary_root):
            raise RuntimeError("test temporary directory escaped tools/outputs")
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name)
        self.path = self.directory / "iq with spaces"
        self.samples = [(0.125, -0.25), (0.5, 0), (-0.75, 0.25), (0, -0.125)]
        self.path.write_bytes(b"".join(struct.pack("<ff", *pair) for pair in self.samples))

    def invoke(self, function, arguments):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            try:
                code = function(arguments)
            except SystemExit as exc:
                code = exc.code
        return code, out.getvalue(), err.getvalue()

    def test_iq_statistics_preserve_interleaving_and_segment_units(self):
        result = inspect_recording(self.path, 8, start=1, count=2)
        self.assertEqual(result["segment_seconds"], 0.25)
        self.assertEqual(result["examined_samples"], 2)
        self.assertEqual(result["nonfinite_samples"], 0)
        self.assertAlmostEqual(result["rms_magnitude"] ** 2, (0.25 + 0.625) / 2)
        self.assertAlmostEqual(result["mean_i"], -0.125)
        self.assertAlmostEqual(result["mean_q"], 0.125)
        self.assertIsNone(inspect_recording(self.path)["segment_seconds"])
        self.assertEqual(recording_window(self.path, 1).count, 3)

    def test_rejects_empty_misaligned_and_out_of_range_segments(self):
        for data in (b"", b"\x00" * 7, b"\x00" * 9):
            self.path.write_bytes(data)
            with self.assertRaises(ValueError):
                recording_window(self.path)
        self.path.write_bytes(bytes(32))
        for start, count in ((-1, 0), (4, 0), (0, -1), (3, 2)):
            with self.assertRaises(ValueError):
                recording_window(self.path, start, count)

    def test_sampled_scan_is_labeled_and_full_scan_finds_hidden_nan(self):
        samples = [(0.1, 0.2)] * 100
        samples[20] = (float("nan"), 0)
        self.path.write_bytes(b"".join(struct.pack("<ff", *pair) for pair in samples))
        sampled = inspect_recording(self.path, block_samples=8)
        self.assertEqual(sampled["inspection_coverage"], "head_middle_tail")
        self.assertEqual(sampled["examined_samples"], 24)
        self.assertEqual(sampled["nonfinite_samples"], 0)
        full = inspect_recording(self.path, full_scan=True, block_samples=8)
        self.assertEqual(full["inspection_coverage"], "full_segment")
        self.assertEqual(full["nonfinite_samples"], 1)
        self.assertEqual(full["examined_samples"], 100)
        json.dumps(full, allow_nan=False)

    def test_catalog_paths_resolve_from_catalog_not_working_directory(self):
        catalog = self.directory / "catalog.json"
        catalog.write_text(json.dumps({"format": "cf32_le", "recording_directory": "missing",
                                      "recordings": [{"id": "r", "filename": self.path.name,
                                                      "side": "red"}]}), encoding="utf-8")
        code, out, _ = self.invoke(replay_main, ["--recording", "r", "--catalog", str(catalog),
                                                "--recording-dir", str(self.directory)])
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(out)["file"], str(self.path.resolve()))
        self.assertIsNone(json.loads(out)["sample_rate_hz"])
        _, entries = load_catalog(DEFAULT_CATALOG)
        self.assertEqual(len(entries), 8)
        self.assertTrue(all(e["sample_rate_hz"] is None and e["center_frequency_hz"] is None
                            for e in entries.values()))

    def test_default_inspection_and_tx_preflight_do_not_require_gnuradio(self):
        code, out, _ = self.invoke(replay_main, ["--file", str(self.path)])
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(out)["mode"], "inspect")
        code, _, error = self.invoke(replay_main, ["--mode", "tx", "--file", str(self.path)])
        self.assertEqual(code, 2)
        self.assertIn("TX requires", error)
        self.path.write_bytes(struct.pack("<ff", float("inf"), 0))
        common = ["--mode", "tx", "--file", str(self.path), "--sample-rate", "2000000",
                  "--frequency", "432600000", "--uri", "ip:example"]
        code, _, error = self.invoke(replay_main, common)
        self.assertEqual(code, 2)
        self.assertIn("nonfinite IQ", error)
        self.path.write_bytes(struct.pack("<ff", 0.75, 0))
        code, _, error = self.invoke(replay_main, common + ["--amplitude", "2"])
        self.assertEqual(code, 2)
        self.assertIn("DAC range", error)

    def test_linux_windows_defaults_and_explicit_directory_override(self):
        from argparse import Namespace

        config = self.directory / "config"
        config.mkdir()
        catalog = config / "catalog.json"
        catalog.write_text(json.dumps({
            "format": "cf32_le", "recording_directory": "../Info",
            "recording_directory_windows": "../WindowsInfo",
            "recordings": [{"id": "r", "filename": "RX_RED", "side": "red"}],
        }), encoding="utf-8")
        args = Namespace(file=None, recording="r", catalog=catalog, recording_dir=None)
        for platform, directory in (("linux", "Info"), ("win32", "WindowsInfo")):
            with mock.patch("sdr_tools.recordings.sys.platform", platform):
                expected = self.directory / directory / "RX_RED"
                self.assertEqual(resolve_recording(args)[0], expected)
                self.assertEqual(catalog_listing(args)[0]["path"], str(expected))
                args.recording_dir = self.directory
                self.assertEqual(resolve_recording(args)[0], self.directory / "RX_RED")
                self.assertEqual(catalog_listing(args)[0]["path"], str(self.directory / "RX_RED"))
                args.recording_dir = None

    def test_rx_describes_profile_and_rejects_invalid_passbands(self):
        arguments = ["--file", str(self.path), "--side", "red", "--sample-rate", "2000000",
                     "--mode", "describe"]
        code, out, _ = self.invoke(rx_main, arguments)
        self.assertEqual(code, 0)
        channels = json.loads(out)["channels"]
        self.assertEqual([(c["name"], c["offset_hz"]) for c in channels],
                         [("info", 600000), ("key1", -400000), ("key2", -100000)])
        for suffix in (["--info-offset", "990000"], ["--channel-width", "nan"],
                       ["--fast", "--publish"], ["--sps", "0"]):
            code, _, _ = self.invoke(rx_main, arguments + suffix)
            self.assertEqual(code, 2)

    def test_receive_profiles_use_correct_sample_stage_and_allow_overrides(self):
        base = ["--file", str(self.path), "--side", "red", "--mode", "describe", "--only", "info"]
        for profile, sample_rate, decimation, sps, offset in (
            ("rm-sdr", 2000000, 2, 47, 600000),
            ("rm-sdr-52", 2000000, 2, 52, 600000),
            ("whu-raw", 3000000, 3, 52, 500000),
            ("info-centered", 1000000, 1, 52, 0),
            ("whu-raw-47", 3000000, 3, 47, 500000),
            ("info-centered-47", 1000000, 1, 47, 0),
        ):
            code, out, error = self.invoke(rx_main, base + ["--profile", profile])
            self.assertEqual(code, 0, error)
            config = json.loads(out)
            self.assertEqual(config["sample_rate_hz"], sample_rate)
            self.assertEqual(config["decimation"], decimation)
            self.assertEqual(config["demod_sample_rate_hz"], 1000000)
            self.assertEqual(config["sps_after_decimation"], sps)
            self.assertEqual(config["channels"][0]["offset_hz"], offset)
        code, out, _ = self.invoke(rx_main, base + ["--profile", "whu-raw", "--decimation", "2",
                                                  "--sps", "78", "--info-offset", "450000"])
        self.assertEqual(code, 0)
        config = json.loads(out)
        self.assertEqual(config["demod_sample_rate_hz"], 1500000)
        self.assertEqual(config["sps_after_decimation"], 78)
        self.assertEqual(config["channels"][0]["offset_hz"], 450000)
        for invalid in (["--decimation", "0"], ["--channel-width", "1200000"],
                        ["--transition-width", "0"]):
            code, _, _ = self.invoke(rx_main, base + ["--profile", "whu-raw"] + invalid)
            self.assertEqual(code, 2)

    @unittest.skipIf(np is None, "NumPy is required for spectrum checks")
    def test_recording_spectrum_recovers_known_tone(self):
        count, sample_rate, frequency = 8192, 2_000_000, 250_000
        data = 0.1 * np.exp(2j * np.pi * frequency * np.arange(count) / sample_rate)
        self.path.write_bytes(data.astype("<c8").tobytes())
        result = spectrum_peaks(self.path, sample_rate)
        self.assertAlmostEqual(result["peaks"][0]["offset_hz"], frequency)
        self.assertAlmostEqual(result["peaks"][0]["fraction_of_sample_rate"], 0.125)


if __name__ == "__main__":
    unittest.main()
