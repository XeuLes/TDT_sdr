"""Protocol/fragmentation checks; run without GNU Radio or SDR hardware."""

import importlib.util
from pathlib import Path
import struct
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from radio_test_frames import (  # noqa: E402
    ACCESS_INFO, ACCESS_KEY, HP, POSITIONS, PREAMBLE, TEST_KEY, StreamSettings,
    application_frame, build_stream, crc8, crc16, demo_payload,
    parse_application_stream, physical_frames,
)

# Compare the new bitwise CRC implementation against the existing table-based
# implementation. Importing bin.py does not run its file-generating main().
PROJECT_ROOT = next(parent for parent in Path(__file__).resolve().parents
                    if (parent / "bin.py").is_file())
reference_spec = importlib.util.spec_from_file_location("existing_bin", PROJECT_ROOT / "bin.py")
reference = importlib.util.module_from_spec(reference_spec)
reference_spec.loader.exec_module(reference)


class RadioTestFramesTest(unittest.TestCase):
    def test_position_order_and_units(self):
        command, payload = demo_payload("position")
        self.assertEqual(command, 0x0A01)
        self.assertEqual(len(payload), 24)
        self.assertEqual(struct.unpack("<12H", payload), tuple(v for xy in POSITIONS for v in xy))
        self.assertEqual(struct.unpack_from("<HH", payload, 16), (0, 0))

    def test_other_official_payload_sizes(self):
        command, payload = demo_payload("hp")
        self.assertEqual((command, struct.unpack("<6H", payload)), (0x0A02, HP))
        self.assertEqual(demo_payload("key"), (0x0A06, TEST_KEY))
        self.assertEqual(len(TEST_KEY), 6)

    def test_application_crc_matches_existing_receiver_conventions(self):
        for message in ("position", "hp", "key"):
            command, payload = demo_payload(message)
            packet = application_frame(command, payload, 257)
            self.assertEqual(packet[:4], struct.pack("<BHB", 0xA5, len(payload), 1))
            self.assertEqual(packet[4], reference.get_crc8(packet[:4]))
            self.assertEqual(int.from_bytes(packet[-2:], "little"), reference.get_crc16(packet[:-2]))
            self.assertEqual(parse_application_stream(packet), [(command, 1, payload)])

    def test_crc_for_all_byte_values_matches_table_reference(self):
        data = bytes(range(256))
        self.assertEqual(crc8(data), reference.get_crc8(data))
        self.assertEqual(crc16(data), reference.get_crc16(data))

    def test_physical_fragment_roundtrip_and_access_codes(self):
        for message in ("position", "hp", "key"):
            command, payload = demo_payload(message)
            packet = application_frame(command, payload, 9)
            access = ACCESS_KEY if message == "key" else ACCESS_INFO
            physical = physical_frames(packet, access)
            fragments = [physical[i:i + 27] for i in range(0, len(physical), 27)]
            for fragment in fragments:
                self.assertEqual(len(fragment), 27)
                self.assertEqual(fragment[:12], access + bytes.fromhex("000F000F"))
            recovered = b"".join(fragment[12:] for fragment in fragments)
            self.assertEqual(recovered[:len(packet)], packet)
            self.assertEqual(parse_application_stream(recovered), [(command, 9, payload)])

    def test_timing_rounding_and_sequence_wrap(self):
        settings = StreamSettings(packets=258)
        self.assertEqual(settings.period_bytes, 266)
        self.assertAlmostEqual(settings.actual_frame_rate, 9.998400255959046)
        self.assertEqual(settings.bit_rate, 2_000_000 / 94)
        data = build_stream(settings)
        self.assertEqual(len(data), 258 * 266)
        for index in (0, 1, 255, 256, 257):
            start = index * settings.period_bytes + len(PREAMBLE)
            physical = data[start:start + 81]
            recovered = b"".join(physical[i + 12:i + 27] for i in range(0, 81, 27))
            self.assertEqual(parse_application_stream(recovered)[0][1], index & 255)

    def test_corrupt_crc_and_leading_noise_resynchronize(self):
        command, payload = demo_payload("position")
        packet = application_frame(command, payload, 1)
        bad_header = bytearray(packet)
        bad_header[4] ^= 1
        bad_payload = bytearray(packet)
        bad_payload[8] ^= 1
        mixed = b"\x00\x11\x22" + bad_header + bad_payload + packet + b"\x00" * 12
        self.assertEqual(parse_application_stream(mixed), [(command, 1, payload)])
        self.assertEqual(parse_application_stream(packet[:-1]), [])

    def test_rejects_impossible_frame_rate(self):
        with self.assertRaisesRegex(ValueError, "too high"):
            StreamSettings(frame_rate=1000)
        for bad in (0.0, float("nan"), float("inf")):
            with self.assertRaises(ValueError):
                StreamSettings(frame_rate=bad)

    def test_side_frequency_comes_from_current_rx_channels(self):
        self.assertEqual(StreamSettings(side="red").frequency, 433_200_000)
        self.assertEqual(StreamSettings(side="blue").frequency, 433_920_000)
        self.assertEqual(StreamSettings(message="key", side="red").frequency, 432_200_000)
        self.assertEqual(StreamSettings(message="key", side="blue").frequency, 434_920_000)


if __name__ == "__main__":
    unittest.main()
