"""High-level model of the A50 Gen 4 base station."""
from __future__ import annotations

import copy
import time
from dataclasses import dataclass, field

from . import protocol as p
from .protocol import Band, Cmd, DeviceError, FirmwareInfo, Preset, Slider
from .transport import Transport

SAVE_BUSY = 2
SAVE_BUSY_DELAY = 1.5


@dataclass
class Settings:
    """Values that exist as a live copy and a copy saved on the headset."""
    presets: list[Preset] = field(default_factory=lambda: [Preset() for _ in range(p.PRESET_COUNT)])
    sliders: dict[int, int] = field(default_factory=dict)
    noise_gate: int = p.NoiseGate.HOME
    balance: int = 128
    alert_volume: int = 100


@dataclass
class State:
    tx: FirmwareInfo | None = None
    tx_dsp: int | None = None
    battery: int = 0
    charging: bool = False
    mix: int = 128
    active_preset: int = 0
    mic_eq: int = p.MicEq.BRIGHT
    brightness: int = p.BRIGHTNESS_MAX
    auto_off: int = 0
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

    def read_all(self) -> State:
        s = self.state
        s.tx = p.parse_tx_info(self.t.transact(Cmd.TX_INFO))
        s.tx_dsp = self.t.transact(Cmd.TX_DSP_VERSION)[0]
        self.read_status()
        for i in range(p.PRESET_COUNT):
            self.read_preset(i)
        for sid in Slider:
            cur, saved = p.parse_pair(self.t.transact(Cmd.GET_SLIDER, bytes([sid])), Cmd.GET_SLIDER, sid)
            s.live.sliders[sid], s.saved.sliders[sid] = cur, saved
        s.live.noise_gate, s.saved.noise_gate = p.parse_noise_gate(self.t.transact(Cmd.GET_NOISE_GATE))
        s.mic_eq = min(self.t.transact(Cmd.GET_MIC_EQ)[0], p.MicEq.WARM)
        s.live.balance = self.t.transact(Cmd.GET_BALANCE, b"\x00")[0]
        s.saved.balance = self.t.transact(Cmd.GET_BALANCE, b"\x01")[0]
        s.live.alert_volume = min(self.t.transact(Cmd.GET_ALERT_VOLUME, b"\x00")[0], p.ALERT_MAX)
        s.saved.alert_volume = min(self.t.transact(Cmd.GET_ALERT_VOLUME, b"\x01")[0], p.ALERT_MAX)
        s.brightness = p.parse_brightness(self.t.transact(Cmd.GET_BRIGHTNESS))
        s.auto_off = p.parse_auto_off(self.t.transact(Cmd.GET_AUTO_OFF))
        return s

    def read_status(self) -> State:
        """Cheap poll: battery, game/voice mix and the active EQ preset."""
        s = self.state
        s.battery, s.charging = p.parse_battery(self.t.transact(Cmd.GET_BATTERY))
        s.mix = self.t.transact(Cmd.GET_MIX)[0]
        active = self.t.transact(Cmd.GET_ACTIVE_EQ)[0]
        s.active_preset = max(1, min(p.PRESET_COUNT, active)) - 1
        return s

    def read_preset(self, index: int) -> None:
        live, saved = self.state.live.presets[index], self.state.saved.presets[index]
        gains, saved_gains = p.parse_eq_gains(self.t.transact(Cmd.GET_EQ_GAINS, bytes([index + 1])), index)
        name = p.parse_eq_name(self.t.transact(Cmd.GET_EQ_NAME, bytes([index + 1, 0])), index)
        saved_name = p.parse_eq_name(self.t.transact(Cmd.GET_EQ_NAME, bytes([index + 1, 1])), index)
        live.name, saved.name = name, saved_name
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

    def _set_raw(self, cmd: int, payload: bytes) -> bytes:
        return self.t.transact(cmd, payload, retries=2)

    def set_active_preset(self, index: int) -> None:
        self._set(Cmd.SET_ACTIVE_EQ, bytes([index + 1]), index + 1)
        self.state.active_preset = index

    def set_gains(self, index: int, gains: list[int]) -> None:
        self._set(Cmd.SET_EQ_GAINS, p.payload_eq_gains(index, gains), index + 1)
        for band, gain in zip(self.state.live.presets[index].bands, gains):
            band.gain = int(gain)

    def set_band(self, index: int, band: int, freq: int, bandwidth: float) -> None:
        if band not in p.PEAKING_BANDS:
            bandwidth = 0.0
        else:
            bandwidth = max(p.BW_MIN, min(p.BW_MAX, bandwidth))
        self._set(Cmd.SET_EQ_BAND, p.payload_eq_band(index, band, freq, bandwidth), index + 1, band + 1)
        target = self.state.live.presets[index].bands[band]
        target.freq = max(p.FREQ_MIN, min(p.FREQ_MAX, int(freq)))
        target.bandwidth = round(bandwidth, 1)

    def set_name(self, index: int, name: str) -> None:
        self._set(Cmd.SET_EQ_NAME, p.payload_eq_name(index, name), index + 1)
        self.state.live.presets[index].name = name.encode("utf-8")[:p.NAME_MAX].decode("utf-8", "ignore")

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
        data = self._set_raw(Cmd.SET_NOISE_GATE, bytes([mode]))
        p.expect_echo(data, mode)
        self.state.live.noise_gate = mode

    def set_mic_eq(self, mode: int) -> None:
        self._set(Cmd.SET_MIC_EQ, bytes([mode]))
        self.state.mic_eq = mode

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
        self._set_raw(Cmd.SET_BRIGHTNESS, payload)  # answers with an empty status-0 report
        self.state.brightness = payload[0]

    def set_auto_off(self, option: int) -> None:
        option = max(0, min(len(p.AUTO_OFF_MINUTES) - 1, int(option)))
        self._set(Cmd.SET_AUTO_OFF, bytes([p.AUTO_OFF_MINUTES[option]]))
        self.state.auto_off = option

    def save(self) -> None:
        """Persist the live settings on the headset (ACC calls this 'sync')."""
        for attempt in range(2):
            data = self.t.transact(Cmd.SAVE, timeout=3.0)
            code = p.expect_echo(data, Cmd.SAVE)[:1]
            if code == b"\x00":
                self.state.saved = copy.deepcopy(self.state.live)
                return
            if code == bytes([SAVE_BUSY]) and attempt == 0:
                time.sleep(SAVE_BUSY_DELAY)
                continue
            raise DeviceError(Cmd.SAVE, code[0] if code else -1, "headset refused to save")

    def revert(self) -> None:
        """Write the saved copy back into the live settings."""
        live, saved = self.state.live, self.state.saved
        for i, (cur, old) in enumerate(zip(live.presets, saved.presets)):
            if [b.gain for b in cur.bands] != [b.gain for b in old.bands]:
                self.set_gains(i, [b.gain for b in old.bands])
            for b, (cb, ob) in enumerate(zip(cur.bands, old.bands)):
                if (cb.freq, cb.bandwidth) != (ob.freq, ob.bandwidth):
                    self.set_band(i, b, ob.freq, ob.bandwidth)
            if cur.name != old.name and old.name:
                self.set_name(i, old.name)
        for sid, value in saved.sliders.items():
            if live.sliders.get(sid) != value:
                self.set_slider(sid, value)
        if live.noise_gate != saved.noise_gate:
            self.set_noise_gate(saved.noise_gate)
        if live.balance != saved.balance:
            self.set_balance(saved.balance)
        if live.alert_volume != saved.alert_volume:
            self.set_alert_volume(saved.alert_volume)
