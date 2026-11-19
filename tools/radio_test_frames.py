"""Synthetic RM application frames and the physical framing used by this receiver.

Application payloads follow the bundled 2026 V1.3.0 protocol. The access codes,
four-byte link header and 15-byte fragmentation follow RM_sdr/bin.py; they are
receiver compatibility assumptions, not a complete official RF specification.
"""

from dataclasses import dataclass
import math
import struct


ACCESS_INFO = bytes.fromhex("2F6F4C74B914492E")
ACCESS_KEY = bytes.fromhex("16E8D377151C712D")
LINK_HEADER = bytes.fromhex("000F000F")
FRAGMENT_BYTES = 15
PHYSICAL_BYTES = 27
PREAMBLE = bytes([0xAA]) * 16

# Hero, engineer, infantry 3, infantry 4, reserved, sentry. Units: cm.
POSITIONS = ((600, 400), (900, 500), (1200, 600), (1500, 700), (0, 0), (2000, 800))
HP = (100, 150, 200, 250, 0, 300)
TEST_KEY = b"TEST01"


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
    if not 0 <= command <= 0xFFFF or len(payload) > 503:
        raise ValueError("command or payload exceeds the receiver's frame limits")
    header = struct.pack("<BHB", 0xA5, len(payload), sequence & 0xFF)
    packet = header + bytes([crc8(header)]) + struct.pack("<H", command) + payload
    return packet + struct.pack("<H", crc16(packet))


def demo_payload(message):
    if message == "position":
        return 0x0A01, struct.pack("<12H", *(v for pair in POSITIONS for v in pair))
    if message == "hp":
        return 0x0A02, struct.pack("<6H", *HP)
    if message == "key":
        return 0x0A06, TEST_KEY
    raise ValueError("message must be position, hp or key")


def physical_frames(packet, access_code=ACCESS_INFO):
    if len(access_code) != 8:
        raise ValueError("access code must contain eight bytes")
    padded = packet + bytes((-len(packet)) % FRAGMENT_BYTES)
    return b"".join(
        access_code + LINK_HEADER + padded[offset:offset + FRAGMENT_BYTES]
        for offset in range(0, len(padded), FRAGMENT_BYTES)
    )


@dataclass(frozen=True)
class StreamSettings:
    message: str = "position"
    side: str = "red"
    packets: int = 3
    sample_rate: int = 2_000_000
    sps: int = 94
    frame_rate: float = 10.0

    def __post_init__(self):
        demo_payload(self.message)
        if self.side not in ("red", "blue"):
            raise ValueError("side must be red or blue")
        if self.packets < 1 or self.sample_rate < 1 or self.sps < 2:
            raise ValueError("packets, sample rate and SPS must be positive; SPS >= 2")
        if not math.isfinite(self.frame_rate) or self.frame_rate <= 0:
            raise ValueError("frame rate must be finite and positive")
        if self.period_bytes < self.minimum_period_bytes:
            raise ValueError(
                "frame rate is too high for this payload, preamble and bit rate; "
                f"need at least {self.minimum_period_bytes} bytes per period"
            )

    @property
    def access_code(self):
        return ACCESS_KEY if self.message == "key" else ACCESS_INFO

    @property
    def frequency(self):
        # Derived from the checked-in RX LO and channel offsets, not a rulebook.
        if self.message == "key":
            return 432_200_000 if self.side == "red" else 434_920_000
        return 433_200_000 if self.side == "red" else 433_920_000

    @property
    def bit_rate(self):
        return self.sample_rate / self.sps

    @property
    def period_bytes(self):
        return max(1, round(self.bit_rate / (8 * self.frame_rate)))

    @property
    def minimum_period_bytes(self):
        command, payload = demo_payload(self.message)
        return len(PREAMBLE) + len(physical_frames(application_frame(command, payload, 0)))

    @property
    def actual_frame_rate(self):
        return self.bit_rate / (8 * self.period_bytes)


def build_stream(settings):
    command, payload = demo_payload(settings.message)
    periods = []
    for sequence in range(settings.packets):
        framed = physical_frames(
            application_frame(command, payload, sequence), settings.access_code
        )
        period = PREAMBLE + framed
        # These zero *bytes* become a steady GFSK low tone, not RF silence.
        periods.append(period + bytes(settings.period_bytes - len(period)))
    return b"".join(periods)


def parse_application_stream(data):
    """Parse demodulated 15-byte fragments, with the RX's SOF/CRC conventions."""
    frames = []
    offset = 0
    while offset + 5 <= len(data):
        if data[offset] != 0xA5 or crc8(data[offset:offset + 4]) != data[offset + 4]:
            offset += 1
            continue
        payload_size = int.from_bytes(data[offset + 1:offset + 3], "little")
        frame_size = payload_size + 9
        if frame_size > 512:
            offset += 1
            continue
        packet = data[offset:offset + frame_size]
        if len(packet) < frame_size:
            break
        if crc16(packet[:-2]) != int.from_bytes(packet[-2:], "little"):
            offset += 1
            continue
        frames.append((int.from_bytes(packet[5:7], "little"), packet[3], packet[7:-2]))
        offset += frame_size
    return frames


def add_stream_arguments(parser):
    parser.add_argument("--message", choices=("position", "hp", "key"), default="position")
    parser.add_argument("--side", choices=("red", "blue"), default="red")
    parser.add_argument("--packets", type=int, default=3)
    parser.add_argument("--sample-rate", type=int, default=2_000_000)
    parser.add_argument("--sps", type=int, default=94)
    parser.add_argument("--frame-rate", type=float, default=10.0)


def settings_from_arguments(args):
    return StreamSettings(**{
        name: getattr(args, name)
        for name in ("message", "side", "packets", "sample_rate", "sps", "frame_rate")
    })


def describe(settings):
    command, payload = demo_payload(settings.message)
    return {
        "message": settings.message,
        "command": f"0x{command:04X}",
        "payload_bytes": len(payload),
        "application_frame_bytes": len(payload) + 9,
        "access_code": settings.access_code.hex().upper(),
        "suggested_frequency_hz": settings.frequency,
        "sample_rate": settings.sample_rate,
        "samples_per_symbol": settings.sps,
        "bit_rate": settings.bit_rate,
        "requested_frame_rate": settings.frame_rate,
        "actual_frame_rate": settings.actual_frame_rate,
        "period_bytes": settings.period_bytes,
        "packets": settings.packets,
        "byte_stream_size": settings.period_bytes * settings.packets,
        "rf_profile": "RM_sdr compatibility test; official physical-layer parameters unverified",
        "idle": "zero bytes modulate to a continuous low tone, not a silent RF gap",
    }
