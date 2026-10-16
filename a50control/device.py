"""High-level model of the A50 Gen 4 base station."""
from __future__ import annotations

import copy
import time
from dataclasses import dataclass, field

from . import protocol as p
from .protocol import Band, Cmd, DeviceError, FirmwareInfo, Preset, Slider
from .transport import Timeout, Transport

SAVE_BUSY = 2
SAVE_BUSY_DELAY = 1.5
LIVE, SAVED = b"\x00", b"\x01"


@dataclass
class Settings:
    """Values that exist as a live copy and a copy saved on the headset."""
    presets: list[Preset] = field(default_factory=lambda: [Preset() for _ in range(p.PRESET_COUNT)])
    sliders: dict[int, int] = field(default_factory=dict)
    noise_gate: int = p.NoiseGate.HOME
    mic_eq: int = p.MicEq.BRIGHT
    balance: int = 128
    alert_volume: int = 100
    brightness: int = p.BRIGHTNESS_MAX
    auto_off: int = 0


@dataclass
class State:
    base: FirmwareInfo | None = None
    base_minor: int | None = None
    headset_version: str | None = None
    headset_on: bool = False
    docked: bool = False
    battery: int = 0
    charging: bool = False
    mix: int = 128
    active_preset: int = 0
    live: Settings = field(default_factory=Settings)
    saved: Settings = field(default_factory=Settings)

    @property
    def dirty(self) -> bool:
        return self.live != self.saved


class A50:
    def __init__(self, transport: Transport):
        self.t = transport
        self.state = State()

    def close(self) -> None:
        self.t.close()

    # -- reading -----------------------------------------------------------

    def _byte(self, cmd: int, payload: bytes = b"") -> int:
        data = self.t.transact(cmd, payload)
        if not data:
            raise p.ProtocolError(f"empty response to 0x{cmd:02x}")
        return data[0]

    def read_all(self) -> State:
        s = self.state
        s.base = p.parse_tx_info(self.t.transact(Cmd.TX_INFO))
        s.base_minor = self._byte(Cmd.BASE_FW_MINOR)
        self.read_status()
        for i in range(p.PRESET_COUNT):
            self.read_preset(i)
        for sid in Slider:
            cur, saved = p.parse_pair(self.t.transact(Cmd.GET_SLIDER, bytes([sid])), Cmd.GET_SLIDER, sid)
            s.live.sliders[sid], s.saved.sliders[sid] = cur, saved
        s.live.noise_gate, s.saved.noise_gate = p.parse_noise_gate(self.t.transact(Cmd.GET_NOISE_GATE))
        for copy_sel, target in ((LIVE, s.live), (SAVED, s.saved)):
            target.mic_eq = min(self._byte(Cmd.GET_MIC_EQ, copy_sel), p.MicEq.WARM)
            target.balance = self._byte(Cmd.GET_BALANCE, copy_sel)
            target.alert_volume = min(self._byte(Cmd.GET_ALERT_VOLUME, copy_sel), p.ALERT_MAX)
            target.brightness = p.parse_brightness(self.t.transact(Cmd.GET_BRIGHTNESS, copy_sel))
            target.auto_off = p.parse_auto_off(self.t.transact(Cmd.GET_AUTO_OFF, copy_sel))
        return s

    def read_status(self) -> State:
        """Cheap poll: headset power/dock, battery, game/voice mix and active EQ preset."""
        s = self.state
        s.headset_on, s.docked = p.parse_headset_status(self.t.transact(Cmd.HEADSET_STATUS))
        s.battery, s.charging = p.parse_battery(self.t.transact(Cmd.GET_BATTERY))
        s.mix = self._byte(Cmd.GET_MIX)
        s.active_preset = max(1, min(p.PRESET_COUNT, self._byte(Cmd.GET_ACTIVE_EQ))) - 1
        if not s.headset_on:
            s.headset_version = None
        elif s.headset_version is None:
            self.read_headset_version()
        return s

    def read_headset_version(self) -> None:
        """Relayed to the headset; only answers while it is switched on."""
        s = self.state
        if not s.headset_on:
            return
        try:
            major = self.t.transact(Cmd.HEADSET_FW_MAJOR, timeout=2.5, retries=0)
            minor = self.t.transact(Cmd.HEADSET_FW_MINOR, timeout=2.5, retries=0)
            s.headset_version = f"{p.parse_u32(major)}.{minor[0]}"
        except (DeviceError, p.ProtocolError, IndexError, Timeout):
            s.headset_version = ""  # unknown; do not retry until the headset reconnects

    def read_preset(self, index: int) -> None:
        live, saved = self.state.live.presets[index], self.state.saved.presets[index]
        gains, saved_gains = p.parse_eq_gains(self.t.transact(Cmd.GET_EQ_GAINS, bytes([index + 1])), index)
        live.name = p.parse_eq_name(self.t.transact(Cmd.GET_EQ_NAME, bytes([index + 1, 0])), index)
        saved.name = p.parse_eq_name(self.t.transact(Cmd.GET_EQ_NAME, bytes([index + 1, 1])), index)
        for b in range(p.BAND_COUNT):
            data = self.t.transact(Cmd.GET_EQ_BAND, bytes([index + 1, b + 1]))
            cur, old = p.parse_eq_band(data, index, b)
            live.bands[b] = Band(gains[b], cur.freq, cur.bandwidth)
            saved.bands[b] = Band(saved_gains[b], old.freq, old.bandwidth)

    # -- writing -----------------------------------------------------------

    def _set(self, cmd: int, payload: bytes, *echo: int) -> None:
        """Send a setter; the response normally echoes [cmd, *echo]."""
        data = self.t.transact(cmd, payload, retries=2)
        p.expect_echo(data, cmd, *echo)

    def set_active_preset(self, index: int) -> None:
        self._set(Cmd.SET_ACTIVE_EQ, bytes([index + 1]), index + 1)
        self.state.active_preset = index

    def set_gains(self, index: int, gains: list[int]) -> None:
        self._set(Cmd.SET_EQ_GAINS, p.payload_eq_gains(index, gains), index + 1)
        for band, gain in zip(self.state.live.presets[index].bands, gains):
            band.gain = max(p.GAIN_MIN, min(p.GAIN_MAX, int(gain)))

    def set_band(self, index: int, band: int, freq: int, bandwidth: float) -> None:
        if band not in p.PEAKING_BANDS:
            bandwidth = 0.0
        else:
            bandwidth = round(max(p.BW_MIN, min(p.BW_MAX, bandwidth)), 1)
        self._set(Cmd.SET_EQ_BAND, p.payload_eq_band(index, band, freq, bandwidth), index + 1, band + 1)
        target = self.state.live.presets[index].bands[band]
        target.freq = max(p.FREQ_MIN, min(p.FREQ_MAX, int(freq)))
        target.bandwidth = bandwidth

    def set_name(self, index: int, name: str) -> None:
        name = p.clip_name(name)
        self._set(Cmd.SET_EQ_NAME, p.payload_eq_name(index, name), index + 1)
        self.state.live.presets[index].name = name

    def apply_preset(self, index: int, preset: Preset) -> None:
        """Write a complete preset (gains, bands, name) into slot ``index``."""
        self.set_gains(index, [b.gain for b in preset.bands])
        for b, band in enumerate(preset.bands):
            self.set_band(index, b, band.freq, band.bandwidth)
        if preset.name:
            self.set_name(index, preset.name)

    def set_slider(self, slider: int, value: int) -> None:
        value = max(0, min(p.SLIDER_MAX, int(value)))
        self._set(Cmd.SET_SLIDER, bytes([slider, value]), slider)
        self.state.live.sliders[slider] = value

    def set_noise_gate(self, mode: int) -> None:
        # Unlike the other setters this one echoes the mode instead of the command.
        data = self.t.transact(Cmd.SET_NOISE_GATE, bytes([mode]), retries=2)
        p.expect_echo(data, mode)
        self.state.live.noise_gate = mode

    def set_mic_eq(self, mode: int) -> None:
        self._set(Cmd.SET_MIC_EQ, bytes([mode]))
        self.state.live.mic_eq = mode

    def set_balance(self, value: int) -> None:
        value = max(0, min(p.BALANCE_MAX, int(value)))
        self._set(Cmd.SET_BALANCE, bytes([value]))
        self.state.live.balance = value

    def set_alert_volume(self, value: int) -> None:
        value = max(0, min(p.ALERT_MAX, int(value)))
        self._set(Cmd.SET_ALERT_VOLUME, bytes([value]))
        self.state.live.alert_volume = value

    def set_brightness(self, value: int) -> None:
        payload = p.payload_brightness(value)
        self.t.transact(Cmd.SET_BRIGHTNESS, payload, retries=2)  # empty status-0 answer
        self.state.live.brightness = payload[0]

    def set_auto_off(self, option: int) -> None:
        option = max(0, min(len(p.AUTO_OFF_MINUTES) - 1, int(option)))
        self._set(Cmd.SET_AUTO_OFF, bytes([p.AUTO_OFF_MINUTES[option]]))
        self.state.live.auto_off = option

    def save(self) -> None:
        """Persist the live settings on the headset (ACC calls this 'sync')."""
        for attempt in range(2):
            data = self.t.transact(Cmd.SAVE, timeout=5.0)
            code = p.expect_echo(data, Cmd.SAVE)[:1]
            if code == b"\x00":
                self.state.saved = copy.deepcopy(self.state.live)
                return
            if code == bytes([SAVE_BUSY]) and attempt == 0:
                time.sleep(SAVE_BUSY_DELAY)
                continue
            raise DeviceError(Cmd.SAVE, code[0] if code else -1, "headset refused to save")

    def revert(self) -> None:
        """Write the whole saved copy back into the live settings.

        Everything is rewritten (about 35 requests) rather than only the values that
        differ in our model: a write whose answer got lost may still have landed.
        """
        saved = copy.deepcopy(self.state.saved)
        for i, preset in enumerate(saved.presets):
            self.set_gains(i, [b.gain for b in preset.bands])
            for b, band in enumerate(preset.bands):
                self.set_band(i, b, band.freq, band.bandwidth)
            if preset.name:
                self.set_name(i, preset.name)
        for sid, value in saved.sliders.items():
            self.set_slider(sid, value)
        self.set_noise_gate(saved.noise_gate)
        self.set_mic_eq(saved.mic_eq)
        self.set_balance(saved.balance)
        self.set_alert_volume(saved.alert_volume)
        self.set_brightness(saved.brightness)
        self.set_auto_off(saved.auto_off)
