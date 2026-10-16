"""Wire protocol of the ASTRO A50 Gen 4 base station.

Recovered from ASTRO Command Center (AstroCommandCenter.exe, MSI build
2021-05-05) and verified against a live base station (9886:002c). See
docs/PROTOCOL.md for the evidence behind each command.

All traffic uses the vendor HID collection (usage page 0xFF32) with report ID 2
and 64-byte reports:

    request : [0x02, cmd, seq << 6 | len, payload...]   (zero padded)
    response: [0x02, status, seq << 6 | len, data...]

status 0x02 means success; some setters (brightness) answer with status 0x00
and no data, which ACC also accepts. On failure (status 0x01) data holds a little-endian
u32 error code followed by a NUL-terminated message such as
"HID_ERROR_UNHANDLED_PACKET". Commands with bit 7 set are relayed to the
headset; the base station expects an extra 0x0A byte in front of their payload.
"""
from __future__ import annotations

import struct
from dataclasses import dataclass, field
from enum import IntEnum

VENDOR_ID = 0x9886
PRODUCT_ID = 0x002C
USAGE_PAGE = 0xFF32
REPORT_ID = 0x02
REPORT_SIZE = 64
MAX_PAYLOAD = REPORT_SIZE - 3
STATUS_OK = 0x02
STATUS_EMPTY = 0x00
REMOTE_PREFIX = 0x0A


class Cmd(IntEnum):
    TX_INFO = 0x03
    HEADSET_STATUS = 0x54
    BASE_FW_MINOR = 0x55
    SAVE = 0x61
    SET_SLIDER = 0x62
    SET_EQ_GAINS = 0x63
    SET_NOISE_GATE = 0x64
    SET_ACTIVE_EQ = 0x67
    GET_SLIDER = 0x68
    GET_EQ_GAINS = 0x69
    GET_NOISE_GATE = 0x6A
    GET_ACTIVE_EQ = 0x6C
    SET_EQ_NAME = 0x6D
    GET_EQ_NAME = 0x6E
    SET_EQ_BAND = 0x6F
    GET_EQ_BAND = 0x70
    SET_MIC_EQ = 0x71
    GET_MIX = 0x72
    SET_BALANCE = 0x73
    SET_AUTO_OFF = 0x74
    SET_BRIGHTNESS = 0x75
    SET_ALERT_VOLUME = 0x76
    GET_BALANCE = 0x77
    GET_AUTO_OFF = 0x78
    GET_BRIGHTNESS = 0x79
    GET_ALERT_VOLUME = 0x7A
    GET_MIC_EQ = 0x7B
    GET_BATTERY = 0x7C
    HEADSET_FW_MINOR = 0xD6
    HEADSET_FW_MAJOR = 0xDA


class Slider(IntEnum):
    STREAM_MIC = 0
    STREAM_CHAT = 1
    STREAM_GAME = 2
    STREAM_AUX = 3
    MIC_LEVEL = 4
    SIDETONE = 5


class NoiseGate(IntEnum):
    """Raw noise gate modes, ordered from open to most aggressive."""
    STREAMING = 0
    NIGHT = 1
    HOME = 2
    TOURNAMENT = 3


class MicEq(IntEnum):
    BRIGHT = 0
    AIRY = 1
    WARM = 2


# Ranges taken from the ACC range getters (FUN_00485cb0/db0/f00/f90).
PRESET_COUNT = 3
BAND_COUNT = 5
GAIN_MIN, GAIN_MAX = -12, 12
GAIN_OFFSET = 12
FREQ_MIN, FREQ_MAX = 80, 15000
BW_MIN, BW_MAX = 0.1, 3.0
BW_SCALE = 4096.0
NAME_MAX = 49
SLIDER_MAX = 100
BALANCE_MAX = 255  # game/voice balance: 0 = all voice, 255 = all game
ALERT_MAX = 100
BRIGHTNESS_MIN, BRIGHTNESS_MAX = 20, 70
BRIGHTNESS_ZONES = 9
AUTO_OFF_MINUTES = (10, 15, 20, 25, 30)
PEAKING_BANDS = (1, 2, 3)  # zero-based; bands 0 and 4 are shelves


class ProtocolError(Exception):
    """Malformed or unexpected response."""


class DeviceError(Exception):
    """The base station rejected a request."""

    def __init__(self, cmd: int, code: int, message: str):
        super().__init__(f"command 0x{cmd:02x} failed: {message} (code 0x{code:08x})")
        self.cmd = cmd
        self.code = code
        self.message = message


def encode_request(cmd: int, payload: bytes, seq: int) -> bytes:
    """Build one 64-byte output report including the report ID."""
    body = bytes(payload)
    if cmd & 0x80:
        body = bytes([REMOTE_PREFIX]) + body
    if len(body) > MAX_PAYLOAD:
        raise ValueError(f"payload too long ({len(body)} > {MAX_PAYLOAD})")
    report = bytes([REPORT_ID, cmd & 0xFF, ((seq & 0x03) << 6) | len(body)]) + body
    return report + bytes(REPORT_SIZE - len(report))


def response_seq(report: bytes) -> int:
    return report[2] >> 6


def decode_response(cmd: int, report: bytes) -> bytes:
    """Return the data bytes of a response report or raise DeviceError."""
    if len(report) < 3 or report[0] != REPORT_ID:
        raise ProtocolError(f"unexpected report: {report[:8].hex()}")
    status = report[1]
    data = bytes(report[3:3 + (report[2] & 0x3F)])
    if status in (STATUS_OK, STATUS_EMPTY):
        return data
    code = struct.unpack_from("<I", data)[0] if len(data) >= 4 else -1
    message = data[4:].split(b"\0", 1)[0].decode("ascii", "replace")
    raise DeviceError(cmd, code, message or f"status 0x{status:02x}")


def expect_echo(data: bytes, *prefix: int) -> bytes:
    """Check that a response starts with the given echo bytes; return the rest."""
    if data[:len(prefix)] != bytes(prefix):
        raise ProtocolError(f"echo mismatch: want {bytes(prefix).hex()} got {data[:len(prefix)].hex()}")
    return data[len(prefix):]


@dataclass
class Band:
    gain: int = 0          # dB, -12..12
    freq: int = 1000       # Hz
    bandwidth: float = 0.0  # 0 for shelves, else 0.1..3.0


@dataclass
class Preset:
    name: str = ""
    bands: list[Band] = field(default_factory=lambda: [Band() for _ in range(BAND_COUNT)])


@dataclass
class FirmwareInfo:
    vendor_id: int
    product_id: int
    version: int
    build_date: str


def gain_to_raw(gain: int) -> int:
    return max(GAIN_MIN, min(GAIN_MAX, int(gain))) + GAIN_OFFSET


def raw_to_gain(raw: int) -> int:
    return raw - GAIN_OFFSET


def bw_to_raw(bandwidth: float) -> int:
    return int(bandwidth * BW_SCALE) & 0xFFFF


def raw_to_bw(raw: int) -> float:
    return round(raw / BW_SCALE, 1)


def parse_pair(data: bytes, cmd: int, *params: int) -> tuple[int, int]:
    """Parse '[cmd, params..., current, saved]' responses."""
    rest = expect_echo(data, cmd, *params)
    if len(rest) < 2:
        raise ProtocolError(f"short response for 0x{cmd:02x}: {data.hex()}")
    return rest[0], rest[1]


def parse_eq_gains(data: bytes, preset: int) -> tuple[list[int], list[int]]:
    """GET_EQ_GAINS -> (current gains, saved gains), 5 values each in dB."""
    rest = expect_echo(data, Cmd.GET_EQ_GAINS, preset + 1)
    if len(rest) < 2 * BAND_COUNT:
        raise ProtocolError(f"short EQ gain response: {data.hex()}")
    current = [raw_to_gain(b) for b in rest[:BAND_COUNT]]
    saved = [raw_to_gain(b) for b in rest[BAND_COUNT:2 * BAND_COUNT]]
    return current, saved


def parse_eq_band(data: bytes, preset: int, band: int) -> tuple[Band, Band]:
    """GET_EQ_BAND -> (current, saved) frequency/bandwidth of one band."""
    rest = expect_echo(data, Cmd.GET_EQ_BAND, preset + 1, band + 1)
    if len(rest) < 8:
        raise ProtocolError(f"short EQ band response: {data.hex()}")
    bw_cur, bw_saved, f_cur, f_saved = struct.unpack_from("<HHHH", rest)
    return Band(freq=f_cur, bandwidth=raw_to_bw(bw_cur)), Band(freq=f_saved, bandwidth=raw_to_bw(bw_saved))


def parse_eq_name(data: bytes, preset: int) -> str:
    rest = expect_echo(data, Cmd.GET_EQ_NAME, preset + 1)
    return rest.split(b"\0", 1)[0].decode("utf-8", "replace")


def parse_noise_gate(data: bytes) -> tuple[int, int]:
    rest = expect_echo(data, Cmd.GET_NOISE_GATE)
    if len(rest) < 2:
        raise ProtocolError(f"unsupported noise gate response: {data.hex()}")
    return rest[0], rest[1]


def parse_u32(data: bytes) -> int:
    if len(data) < 4:
        raise ProtocolError(f"short u32 response: {data.hex()}")
    return struct.unpack_from("<I", data)[0]


def parse_headset_status(data: bytes) -> tuple[bool, bool]:
    """HEADSET_STATUS -> (switched on, docked); ACC FUN_0048f070 reads the same bits."""
    raw = data[0] if data else 0
    return bool(raw & 0x02), bool(raw & 0x01)


def parse_battery(data: bytes) -> tuple[int, bool]:
    """GET_BATTERY -> (percent, charging)."""
    if not data:
        raise ProtocolError("empty battery response")
    raw = data[0]
    return min(raw & 0x7F, 100), bool(raw & 0x80)


def parse_brightness(data: bytes) -> int:
    zones = data[:BRIGHTNESS_ZONES] or b"\0"
    return round(sum(zones) / len(zones))


def parse_auto_off(data: bytes) -> int:
    """Map the minute value onto the nearest supported option index."""
    if not data:
        raise ProtocolError("empty auto-off response")
    minutes = data[0]
    return min(range(len(AUTO_OFF_MINUTES)), key=lambda i: abs(AUTO_OFF_MINUTES[i] - minutes))


def parse_tx_info(data: bytes) -> FirmwareInfo:
    if len(data) < 0x19:
        raise ProtocolError(f"short info response: {data.hex()}")
    vid, pid = struct.unpack_from("<HH", data, 4)
    year, month, day, hour, minute, second = struct.unpack_from("<HBBBBB", data, 8)
    version = struct.unpack_from("<I", data, 0x15)[0]
    date = f"{year:04d}-{month:02d}-{day:02d} {hour:02d}:{minute:02d}:{second:02d}"
    return FirmwareInfo(vid, pid, version, date)


def payload_eq_gains(preset: int, gains: list[int]) -> bytes:
    return bytes([preset + 1] + [gain_to_raw(g) for g in gains])


def payload_eq_band(preset: int, band: int, freq: int, bandwidth: float) -> bytes:
    freq = max(FREQ_MIN, min(FREQ_MAX, int(freq)))
    return bytes([preset + 1, band + 1]) + struct.pack("<HH", bw_to_raw(bandwidth), freq)


def clip_name(name: str) -> str:
    """Shorten a name to NAME_MAX UTF-8 bytes without splitting a character."""
    return name.encode("utf-8")[:NAME_MAX].decode("utf-8", "ignore")


def payload_eq_name(preset: int, name: str) -> bytes:
    raw = clip_name(name).encode("utf-8")
    if not raw:
        raise ValueError("preset name must not be empty")
    return bytes([preset + 1, len(raw) + 1]) + raw + b"\0"


def payload_brightness(level: int) -> bytes:
    level = max(BRIGHTNESS_MIN, min(BRIGHTNESS_MAX, int(level)))
    return bytes([level] * BRIGHTNESS_ZONES)
