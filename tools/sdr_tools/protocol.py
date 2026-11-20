"""RM application CRC, typed configurable payloads and 15-byte fragmentation."""

import json
from pathlib import Path
import random
import re
import struct

ACCESS_INFO = bytes.fromhex("2F6F4C74B914492E")
ACCESS_JAM = bytes.fromhex("16E8D377151C712D")
LINK_HEADER = bytes.fromhex("000F000F")
PAYLOAD_LENGTHS = {0x0A01: 24, 0x0A02: 12, 0x0A03: 10, 0x0A04: 8, 0x0A05: 41, 0x0A06: 6}
DEFAULT_DATA = Path(__file__).resolve().parents[1] / "config" / "messages.json"


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


def unsigned(value, bits, field):
    if type(value) is not int or not 0 <= value < (1 << bits):
        raise ValueError(f"{field} must be an unsigned {bits}-bit integer")
    return value


def numbers(values, length, field):
    if not isinstance(values, list) or len(values) != length:
        raise ValueError(f"{field} requires {length} values")
    return [unsigned(v, 16, field) for v in values]


def load_payloads(path, side):
    document = json.loads(Path(path).read_text(encoding="utf-8"))
    row = document[side]
    positions = row["positions_cm"]
    if not isinstance(positions, list) or len(positions) != 6:
        raise ValueError("positions_cm requires six [x, y] pairs")
    xy = [v for pair in positions for v in numbers(pair, 2, "positions_cm")]
    hp = numbers(row["hp"], 6, "hp")
    if hp[4] != 0:
        raise ValueError("hp[4] is reserved and must be zero")
    ammo = numbers(row["ammo"], 5, "ammo")
    status = row["status"]
    flags = unsigned(status["flags"], 16, "status.flags")
    buffs = row["buffs"]
    if not isinstance(buffs, list) or len(buffs) != 5:
        raise ValueError("buffs requires five [recovery, cooling, defense, vulnerability, attack] rows")
    buff_bytes = bytearray()
    for fields in buffs:
        if not isinstance(fields, list) or len(fields) != 5:
            raise ValueError("each buffs row requires five values")
        widths = (8, 16, 8, 8, 16)
        buff_bytes.extend(struct.pack("<BHBBH", *(unsigned(v, b, "buffs") for v, b in zip(fields, widths))))
    stance = unsigned(row["sentry_stance"], 8, "sentry_stance")
    if stance not in range(1, 7):
        raise ValueError("sentry_stance must be 1..6")
    states = numbers(row["robot_states"], 5, "robot_states")
    if any(v > 3 for v in states):
        raise ValueError("robot_states values must be 0..3")
    buff_bytes.extend(bytes([stance, *states]))
    key = row["key"]
    if not isinstance(key, str) or re.fullmatch(r"[A-Za-z0-9]{6}", key) is None:
        raise ValueError("key must contain six ASCII letters/digits")
    return {
        0x0A01: struct.pack("<12H", *xy), 0x0A02: struct.pack("<6H", *hp),
        0x0A03: struct.pack("<5H", *ammo),
        0x0A04: struct.pack("<HHI", unsigned(status["remaining_gold"], 16, "remaining_gold"),
                            unsigned(status["total_gold"], 16, "total_gold"), flags),
        0x0A05: bytes(buff_bytes), 0x0A06: key.encode("ascii"),
    }


def scheduled_packets(payloads, wave, seed=0):
    """Three 100-ms arrival events; fragments carry across events and app frames."""
    rng = random.Random(seed)
    access = ACCESS_INFO if wave == "info" else ACCESS_JAM
    commands = range(0x0A01, 0x0A06) if wave == "info" else (0x0A06,)
    sequence = 0
    pending = bytearray()
    groups = []
    for _ in range(3):
        data = bytearray()
        for command in commands:
            data.extend(application_frame(command, payloads[command], sequence))
            sequence += 1
        if wave != "info":
            data.extend(rng.randbytes(135 - len(data)))
        if len(data) != (140 if wave == "info" else 135):
            raise ValueError("application data rate does not match the fixed official profile")
        pending.extend(data)
        frames = []
        while len(pending) >= 15:
            frames.append(access + LINK_HEADER + bytes(pending[:15]))
            del pending[:15]
        groups.append(frames)
    if pending:
        raise ValueError("300-ms period must end on a physical fragment boundary")
    return groups


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
    return result
