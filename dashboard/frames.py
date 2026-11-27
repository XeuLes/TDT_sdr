"""RoboMaster radio application frame codec for Dashboard diagnostics."""

import struct

PAYLOAD_LENGTHS = {0x0A01: 24, 0x0A02: 12, 0x0A03: 10, 0x0A04: 8, 0x0A05: 41, 0x0A06: 6}


def crc8(data):
    value = 0xFF
    for byte in data:
        value ^= byte
        for _ in range(8):
            value = (value >> 1) ^ (0x8C if value & 1 else 0)
    return value


def crc16(data):
    value = 0xFFFF
    for byte in data:
        value ^= byte
        for _ in range(8):
            value = (value >> 1) ^ (0x8408 if value & 1 else 0)
    return value


def application_frame(command, payload, sequence):
    if len(payload) != PAYLOAD_LENGTHS[command]:
        raise ValueError(f"0x{command:04X} requires {PAYLOAD_LENGTHS[command]} payload bytes")
    header = struct.pack("<BHB", 0xA5, len(payload), sequence & 255)
    frame = header + bytes([crc8(header)]) + struct.pack("<H", command) + payload
    return frame + struct.pack("<H", crc16(frame))


class ApplicationParser:
    def __init__(self):
        self.buffer = bytearray()
        self.received = self.valid = self.bad_header = self.bad_frame = 0

    def feed(self, data):
        self.received += len(data)
        self.buffer.extend(data)
        result = []
        while len(self.buffer) >= 5:
            if self.buffer[0] != 0xA5:
                del self.buffer[0]
                continue
            if crc8(self.buffer[:4]) != self.buffer[4]:
                self.bad_header += 1
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
                self.bad_frame += 1
                del self.buffer[0]
                continue
            result.append((int.from_bytes(packet[5:7], "little"), packet[3], bytes(packet[7:-2])))
            self.valid += 1
            del self.buffer[:size]
        return result

    def summary(self):
        return {"fragment_bytes": self.received, "crc_valid_frames": self.valid,
                "bad_header_crc": self.bad_header, "bad_frame_crc": self.bad_frame}


def frame_description(frame):
    command, sequence, payload = frame
    result = {"command": f"0x{command:04X}", "sequence": sequence, "payload_hex": payload.hex().upper()}
    if command == 0x0A06 and len(payload) == 6:
        result["key"] = payload.decode("ascii", errors="replace")
    elif command == 0x0A01 and len(payload) == 24:
        result["positions_cm"] = list(struct.iter_unpack("<HH", payload))
    elif command == 0x0A02 and len(payload) == 12:
        result["hp"] = list(struct.unpack("<6H", payload))
    elif command == 0x0A03 and len(payload) == 10:
        result["ammo"] = list(struct.unpack("<5H", payload))
    elif command == 0x0A04 and len(payload) == 8:
        remaining, total, flags = struct.unpack("<HHI", payload)
        result.update(remaining_gold=remaining, total_gold=total, flags=flags)
    elif command == 0x0A05 and len(payload) in (36, 41):
        result["buffs"] = list(struct.iter_unpack("<BHBBH", payload[:35]))
        result["sentry_stance"] = payload[35]
        if len(payload) == 41:
            result["robot_states"] = list(payload[36:])
    return result
