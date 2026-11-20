"""Protocol, waveform, capture and project-adapter checks without SDR hardware."""

import ast
import contextlib
import copy
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import struct
import sys
import tempfile
import unittest

TOOLS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(TOOLS))

from sdr_tools.capture import check_recording, get_recording, list_recordings
from sdr_tools.profiles import BT, PERIOD_SAMPLES, PROJECT_LO, SAMPLE_RATE, TX_SPS, WAVES, select_waves
from sdr_tools.project import adapted_tree, source_configuration
from sdr_tools.protocol import ApplicationParser, DEFAULT_DATA, application_frame, load_payloads, scheduled_packets
from sdr_tools.runtime import power_setting
from sdr_tools.waveform import official_iq, packet_schedule, timed_symbols

try:
    import numpy as np
except ImportError:
    np = None


def entry(relative):
    spec = importlib.util.spec_from_file_location("tool_" + relative.replace("/", "_"), TOOLS / relative)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def temporary_directory():
    directory = (TOOLS / "outputs" / "tests").resolve()
    if not directory.is_relative_to(TOOLS.resolve()):
        raise ValueError("test temporary directory must stay inside tools")
    directory.mkdir(parents=True, exist_ok=True)
    return tempfile.TemporaryDirectory(dir=directory)


class ProtocolTests(unittest.TestCase):
    def setUp(self):
        self.payloads = load_payloads(DEFAULT_DATA, "red")

    def test_current_payload_lengths_and_buff_layout(self):
        self.assertEqual([len(v) for v in self.payloads.values()], [24, 12, 10, 8, 41, 6])
        self.assertEqual(self.payloads[0x0A05][35:], bytes([1, 0, 0, 0, 0, 0]))
        self.assertEqual(sum(len(v) + 9 for k, v in self.payloads.items() if k != 0x0A06), 140)

    def test_reference_capture_crc_and_arbitrary_stream_boundaries(self):
        golden = bytes.fromhex("A506004E4B060A666359715443472D")
        for split in range(len(golden) + 1):
            parser = ApplicationParser()
            found = parser.feed(golden[:split]) + parser.feed(golden[split:])
            self.assertEqual(found, [(0x0A06, 78, b"fcYqTC")])
        self.assertEqual(application_frame(0x0A06, b"fcYqTC", 78), golden)

    def test_information_fragments_cross_message_and_tick_boundaries(self):
        groups = scheduled_packets(self.payloads, "info")
        self.assertEqual([len(g) for g in groups], [9, 9, 10])
        fragments = b"".join(p[12:] for group in groups for p in group)
        expected = b"".join(application_frame(c, self.payloads[c], tick*5 + i)
                            for tick in range(3) for i, c in enumerate(range(0x0A01, 0x0A06)))
        self.assertEqual(fragments, expected)
        self.assertTrue(all(len(p) == 27 and p[:12].hex() == "2f6f4c74b914492e000f000f"
                            for group in groups for p in group))

    def test_all_interference_levels_have_10hz_key_and_random_padding(self):
        for wave in ("jam1", "jam2", "jam3"):
            groups = scheduled_packets(self.payloads, wave)
            self.assertEqual([len(g) for g in groups], [9, 9, 9])
            parser = ApplicationParser()
            frames = parser.feed(b"".join(p[12:] for g in groups for p in g))
            self.assertEqual(len(frames), 3)
            self.assertTrue(all(f[0] == 0x0A06 and f[2] == b"RED001" for f in frames))
            self.assertEqual(sum(len(p[12:]) for g in groups for p in g), 405)

    def test_corrupted_frame_is_rejected_and_next_frame_recovers(self):
        packet = application_frame(0x0A05, self.payloads[0x0A05], 5)
        bad = bytearray(packet)
        bad[12] ^= 1
        parser = ApplicationParser()
        self.assertEqual(parser.feed(bad + packet), [(0x0A05, 5, self.payloads[0x0A05])])
        self.assertEqual(parser.bad_frame, 1)

    def test_old_36_byte_buff_is_not_generated_as_current_protocol(self):
        with self.assertRaises(ValueError):
            application_frame(0x0A05, bytes(36), 0)

    def test_data_configuration_validation(self):
        original = json.loads(DEFAULT_DATA.read_text(encoding="utf-8"))
        for field, value in (("key", "bad!"), ("hp", [1, 2, 3, 4, 5, 6]),
                             ("sentry_stance", 0), ("positions_cm", [[-1, 0]] * 6)):
            data = copy.deepcopy(original)
            data["red"][field] = value
            with temporary_directory() as directory:
                path = Path(directory) / "data.json"
                path.write_text(json.dumps(data), encoding="utf-8")
                with self.assertRaises(ValueError):
                    load_payloads(path, "red")


@unittest.skipUnless(np is not None, "NumPy required")
class WaveformTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.payloads = load_payloads(DEFAULT_DATA, "red")
        time = np.arange(4*TX_SPS) - 2*TX_SPS
        sigma = TX_SPS * np.sqrt(np.log(2)) / (2*np.pi*BT)
        cls.taps = np.exp(-.5*(time/sigma)**2)
        cls.taps /= cls.taps.sum()

    def test_schedule_has_exact_symbol_clock_and_no_packet_overlap(self):
        for wave, count in (("info", 28), ("jam1", 27)):
            groups = scheduled_packets(self.payloads, wave)
            schedule = packet_schedule(groups)
            self.assertEqual(len(schedule), count)
            nrz, gate = timed_symbols(groups)
            self.assertEqual(len(nrz), PERIOD_SAMPLES)
            self.assertEqual(gate.sum(), count*216*TX_SPS)
            self.assertEqual((gate[0], gate[-1]), (0, 0))
            self.assertTrue(set(np.unique(nrz)).issubset({-1., 0., 1.}))

    def test_rf_mapping_and_steady_frequency_deviation_for_both_sides(self):
        for side in ("red", "blue"):
            for wave, profile in WAVES.items():
                iq = official_iq(self.payloads, side, [wave], self.taps)
                start = (packet_schedule(scheduled_packets(self.payloads, wave))[0][0] - 192000) % PERIOD_SAMPLES
                index = start + 69*TX_SPS
                derivative = np.angle(iq[index+1:index+2*TX_SPS+1] * iq[index:index+2*TX_SPS].conj())
                observed = np.median(derivative) * SAMPLE_RATE / (2*np.pi)
                expected = profile[side] - PROJECT_LO[side] - profile["sensitivity"] * 1000000 / (2*np.pi)
                self.assertAlmostEqual(observed, expected, delta=2)
                self.assertLessEqual(np.max(np.abs(iq)), .200001)

    def test_combination_preserves_50db_ratio_without_clipping(self):
        info = official_iq(self.payloads, "red", ["info"], self.taps)
        jam = official_iq(self.payloads, "red", ["jam1"], self.taps)
        combined = official_iq(self.payloads, "red", ["info", "jam1"], self.taps)
        np.testing.assert_allclose(combined, jam + info * 10**(-50/20), atol=3e-8)
        self.assertLess(np.max(np.abs(combined)), 1)


class ConfigurationTests(unittest.TestCase):
    def test_project_rf_centers_and_receiver_settings(self):
        for side in ("red", "blue"):
            config = source_configuration(side)
            self.assertEqual(config["sample_rate_hz"], 2000000)
            self.assertEqual(config["sps"], 47)
            for wave in WAVES:
                self.assertEqual(config["lo_hz"] + config["channels"][wave]["offset_hz"], WAVES[wave][side])

    def test_runtime_adapter_updates_actual_cpp_arguments_without_writing_project(self):
        for side in ("red", "blue"):
            config = source_configuration(side)
            source = Path(config["file"])
            before = hashlib.sha256(source.read_bytes()).digest()
            path, tree = adapted_tree(side, 52, "ip:10.0.0.1", True)
            compile(tree, str(path), "exec")
            calls = [n for n in ast.walk(tree) if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)]
            decoders = [n for n in calls if n.func.attr in ("gfsk_receiver", "gfsk_receiver_noise")]
            self.assertEqual(len(decoders), 3)
            self.assertTrue(all(ast.literal_eval(n.args[1]) == 52 for n in decoders))
            self.assertTrue(all(len(n.args) == 6 for n in calls if n.func.attr == "pub_sink"))
            self.assertEqual(hashlib.sha256(source.read_bytes()).digest(), before)

    def test_capture_presets_and_default_directory(self):
        rows = list_recordings()
        self.assertEqual(sum(r["usable"] for r in rows), 5)
        red = get_recording("red")
        self.assertEqual(red["tx_lo_hz"], 432482000)
        self.assertTrue(Path(red["path"]).parent.name.endswith("\u4fe1\u606f\u6ce2\u5f55\u5236")
                        if sys.platform == "win32" else Path(red["path"]).parent.name == "Info")
        for row in rows:
            if row["usable"]:
                self.assertEqual(row["playback_sample_rate_hz"], 2000000)
                self.assertEqual(row["receiver_sps"], 52)
                self.assertEqual(row["tx_lo_hz"] + row["iq_offset_hz"], row["target_rf_hz"])
        with self.assertRaises(ValueError):
            get_recording("red-ganrao-1")

    def test_capture_hash_prevents_using_an_unrelated_file_with_a_known_preset(self):
        with temporary_directory() as directory:
            path = Path(directory) / "iq"
            path.write_bytes(bytes(80))
            row = {"path": str(path), "complex_samples": 10, "sha256": hashlib.sha256(bytes(80)).hexdigest()}
            self.assertEqual(check_recording(row), path)
            path.write_bytes(bytes(79) + b"x")
            with self.assertRaisesRegex(ValueError, "SHA-256"):
                check_recording(row)

    def test_power_calibration_and_manual_bench_mode(self):
        data = {"uri": "test:", "tx_channel": 1, "reference_amplitude": .2,
                "reference_attenuation_db": 20, "measured_burst_dbm": {"red:info": -10, "red:info+jam1": -10}}
        calibrated = power_setting("red", ["info"], "test:", calibration=data)
        self.assertEqual(calibrated["attenuation_db"], 70)
        self.assertEqual(calibrated["predicted_strongest_dbm"], -60)
        self.assertEqual(power_setting("red", ["info", "jam1"], "test:", calibration=data)["attenuation_db"], 20)
        self.assertEqual(power_setting("red", ["info"], "other:", 40)["attenuation_db"], 40)
        with self.assertRaises(ValueError):
            power_setting("red", ["info"], "other:", calibration=data)
        with self.assertRaises(ValueError):
            power_setting("red", ["info"], "test:", 100)

    def test_selection_rejects_multiple_interference_levels(self):
        self.assertEqual(select_waves(["jam2", "info", "info"]), ["info", "jam2"])
        with self.assertRaises(ValueError):
            select_waves(["jam1", "jam3"])

    def test_all_entry_descriptions_work_without_gnuradio_or_hardware(self):
        cases = [("tx/official_tx.py", ["--wave", "info", "jam3", "--describe"]),
                 ("tx/replay_tx.py", ["--recording", "red", "--describe"]),
                 ("rx/project_rx.py", ["--side", "blue", "--sps", "52", "--jam3", "--describe"]),
                 ("test/loopback.py", ["--recording", "blue-ganrao-3", "--describe"])]
        for name, args in cases:
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(entry(name).main(args), 0)

    def test_loopback_checks_payload_in_addition_to_crc(self):
        loopback = entry("test/loopback.py")
        packet = application_frame(0x0A06, b"TEST01", 1)
        self.assertTrue(loopback.evaluate(packet, {0x0A06: b"TEST01"})["passed"])
        self.assertFalse(loopback.evaluate(packet, {0x0A06: b"OTHER1"})["passed"])
        self.assertFalse(loopback.evaluate(bytes(30), None)["passed"])


if __name__ == "__main__":
    unittest.main()
