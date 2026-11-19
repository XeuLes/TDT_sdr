"""Read application CRC results while tolerating GNU Radio/OOT console logs."""

import json
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from rx.test_recording import decode_log
from sdr_tools.rx_profiles import DEFAULT_TEST_PROFILES, RECEIVE_PROFILES


class RecordingLogTest(unittest.TestCase):
    def test_default_profiles_use_47_microsecond_bit_timing(self):
        for name in DEFAULT_TEST_PROFILES:
            profile = RECEIVE_PROFILES[name]
            decoder_rate = profile["sample_rate"] / profile["decimation"]
            self.assertEqual(decoder_rate, 1000000)
            self.assertEqual(profile["sps"], 47)
            self.assertAlmostEqual(profile["sps"] / decoder_rate, 47e-6)

    def test_summary_and_frames_survive_mixed_console_output(self):
        configuration = {"event": "configuration", "sample_rate_hz": 3000000, "decimation": 3}
        summary = {"event": "summary", "channels": {"info": {
            "crc_valid_frames": 7, "decoded_fragment_bytes": 180}}}
        lines = ["GNU Radio started", json.dumps(configuration), "Find: 12, Good: 10, Bad: 2",
                 "{not a JSON object}"]
        lines.extend(json.dumps({"event": "frame", "sequence": number}) for number in range(7))
        lines.extend(["[]", json.dumps(summary)])
        config, recovered, examples = decode_log("\n".join(lines))
        self.assertEqual(config, configuration)
        self.assertEqual(recovered, summary)
        self.assertEqual([frame["sequence"] for frame in examples], [0, 1, 2])

    def test_missing_summary_stays_incomplete(self):
        config, summary, examples = decode_log('ImportError: no gnuradio\n{"event":"frame"}')
        self.assertIsNone(config)
        self.assertIsNone(summary)
        self.assertEqual(len(examples), 1)


if __name__ == "__main__":
    unittest.main()
