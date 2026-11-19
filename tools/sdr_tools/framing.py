"""Incremental application CRC validation of the OOT receiver's 15-byte fragments."""

import struct

from radio_test_frames import crc8, crc16


class ApplicationParser:
    def __init__(self):
        self.buffer = bytearray()
        self.bytes_received = 0
        self.valid_frames = 0
        self.bad_header_crc = 0
        self.bad_frame_crc = 0

    def feed(self, data):
        self.bytes_received += len(data)
        self.buffer.extend(data)
        frames = []
        while len(self.buffer) >= 5:
            if self.buffer[0] != 0xA5:
                del self.buffer[0]
                continue
            if crc8(self.buffer[:4]) != self.buffer[4]:
                self.bad_header_crc += 1
                del self.buffer[0]
                continue
            size = int.from_bytes(self.buffer[1:3], "little") + 9
            if size > 512:
                del self.buffer[0]
                continue
            if len(self.buffer) < size:
                break
            packet = self.buffer[:size]
            if crc16(packet[:-2]) != int.from_bytes(packet[-2:], "little"):
                self.bad_frame_crc += 1
                del self.buffer[0]
                continue
            frames.append((int.from_bytes(packet[5:7], "little"), packet[3], bytes(packet[7:-2])))
            self.valid_frames += 1
            del self.buffer[:size]
        return frames

    def summary(self):
        return {"decoded_fragment_bytes": self.bytes_received, "crc_valid_frames": self.valid_frames,
                "bad_header_crc": self.bad_header_crc, "bad_frame_crc": self.bad_frame_crc,
                "pending_bytes": len(self.buffer)}


def describe_frame(command, sequence, payload):
    result = {"command": f"0x{command:04X}", "sequence": sequence,
              "payload_bytes": len(payload), "payload_hex": payload.hex().upper()}
    if command == 0x0A01 and len(payload) == 24:
        result["positions_cm"] = [list(pair) for pair in struct.iter_unpack("<HH", payload)]
    elif command == 0x0A02 and len(payload) == 12:
        result["hp"] = list(struct.unpack("<6H", payload))
    elif command == 0x0A06 and len(payload) == 6:
        result["key_ascii"] = payload.decode("ascii", errors="replace")
    return result
