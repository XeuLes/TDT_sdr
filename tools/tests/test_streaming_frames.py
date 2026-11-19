"""Application frames must survive arbitrary scheduler/fragment boundaries."""

from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from radio_test_frames import application_frame, demo_payload
from sdr_tools.framing import ApplicationParser, describe_frame


class StreamingFramesTest(unittest.TestCase):
    def test_every_fragment_boundary_and_multiple_consecutive_frames(self):
        command, payload = demo_payload("position")
        packet = application_frame(command, payload, 7)
        for boundary in range(len(packet) + 1):
            parser = ApplicationParser()
            result = parser.feed(packet[:boundary]) + parser.feed(packet[boundary:])
            self.assertEqual(result, [(command, 7, payload)])
            self.assertEqual(parser.valid_frames, 1)
        parser = ApplicationParser()
        packets = b"".join(application_frame(command, payload, seq) for seq in range(5))
        result = []
        for offset in range(0, len(packets), 15):
            result.extend(parser.feed(packets[offset:offset + 15]))
        self.assertEqual([frame[1] for frame in result], list(range(5)))

    def test_bad_crc_noise_and_truncation_do_not_become_valid_frames(self):
        command, payload = demo_payload("hp")
        packet = application_frame(command, payload, 3)
        bad_header, bad_payload = bytearray(packet), bytearray(packet)
        bad_header[4] ^= 1
        bad_payload[8] ^= 1
        parser = ApplicationParser()
        result = parser.feed(b"\x11\x22" + bad_header + bad_payload + packet + packet[:-1])
        self.assertEqual(result, [(command, 3, payload)])
        self.assertGreaterEqual(parser.bad_header_crc, 1)
        self.assertGreaterEqual(parser.bad_frame_crc, 1)
        self.assertEqual(parser.summary()["pending_bytes"], len(packet) - 1)
        self.assertEqual(parser.feed(packet[-1:]), [(command, 3, payload)])

    def test_channel_parsers_are_independent_and_payloads_are_decoded(self):
        command, payload = demo_payload("position")
        info, key = ApplicationParser(), ApplicationParser()
        packet = application_frame(command, payload, 2)
        info.feed(packet[:15])
        key_command, key_payload = demo_payload("key")
        self.assertEqual(key.feed(application_frame(key_command, key_payload, 8)),
                         [(key_command, 8, key_payload)])
        self.assertEqual(info.feed(packet[15:]), [(command, 2, payload)])
        self.assertEqual(describe_frame(command, 2, payload)["positions_cm"][0], [600, 400])
        self.assertEqual(describe_frame(key_command, 8, key_payload)["key_ascii"], "TEST01")
        self.assertNotIn("positions_cm", describe_frame(command, 2, b"bad"))


if __name__ == "__main__":
    unittest.main()
