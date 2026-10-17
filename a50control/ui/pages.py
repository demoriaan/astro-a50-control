"""Microphone, stream port and device settings pages."""
from __future__ import annotations

from gi.repository import Adw, Gtk

from .. import protocol as p
from ..i18n import t
from ..protocol import MicEq, NoiseGate, Slider

# Mode names are the ones printed on the headset and used by ASTRO; only descriptions are translated.
NOISE_GATE_TEXT = {
    NoiseGate.STREAMING: ("Streaming", t("gate_streaming")),
    NoiseGate.NIGHT: ("Night", t("gate_night")),
    NoiseGate.HOME: ("Home", t("gate_home")),
    NoiseGate.TOURNAMENT: ("Tournament", t("gate_tournament")),
}
MIC_EQ_TEXT = {
    MicEq.BRIGHT: ("Bright", t("mic_bright")),
    MicEq.AIRY: ("Airy", t("mic_airy")),
    MicEq.WARM: ("Warm", t("mic_warm")),
}


SCALE_WIDTH = 240


def balance_text(value: int) -> str:
    """Chat:game percentages; 0 = all chat, 255 = all game (verified with the headset buttons)."""
    game = round(value * 100 / p.BALANCE_MAX)
    return f"{100 - game}:{game}"


def headset_text(state) -> str:
    if not state.headset_on:
        return t("battery_off")
    parts = [t("headset_on")]
    if state.docked:
        parts.append(t("in_dock"))
    if state.charging:
        parts.append(t("charging"))
    return ", ".join(parts)


def headset_version_text(state) -> str:
    return state.headset_version or t("version_unknown" if state.headset_on else "version_unknown_off")


class ScaleRow(Adw.ActionRow):
    """Action row with a horizontal scale; calls ``on_change(int)`` on user edits."""

    def __init__(self, title: str, subtitle: str, lo: int, hi: int, on_change, unit: str = "",
                 mark: int | None = None, left: str | None = None, right: str | None = None,
                 fmt=None) -> None:
        super().__init__(title=title, subtitle=subtitle)
        self._sync = False
        self._on_change = on_change
        self._fmt = fmt or (lambda v: f"{v}{unit}")
        box = Gtk.Box(spacing=8, valign=Gtk.Align.CENTER)
        self.scale = Gtk.Scale.new_with_range(Gtk.Orientation.HORIZONTAL, lo, hi, 1)
        self.scale.set_draw_value(False)
        self.scale.set_size_request(SCALE_WIDTH, -1)
        if mark is not None:
            self.scale.add_mark(mark, Gtk.PositionType.BOTTOM, None)
        self.scale.connect("value-changed", self._changed)
        self.value_label = Gtk.Label(width_chars=6, xalign=1, css_classes=["numeric"])
        if left:
            box.append(Gtk.Label(label=left, css_classes=["dim-label", "caption"]))
        box.append(self.scale)
        if right:
            box.append(Gtk.Label(label=right, css_classes=["dim-label", "caption"]))
        box.append(self.value_label)
        self.add_suffix(box)

    def _changed(self, scale: Gtk.Scale) -> None:
        value = int(round(scale.get_value()))
        self.value_label.set_label(self._fmt(value))
        if not self._sync:
            self._on_change(value)

    def set_value(self, value: int) -> None:
        self._sync = True
        try:
            self.scale.set_value(value)
            self.value_label.set_label(self._fmt(int(value)))
        finally:
            self._sync = False


class ChoiceGroup(Adw.PreferencesGroup):
    """Radio-style group of rows."""

    def __init__(self, title: str, description: str, options: dict, on_change) -> None:
        super().__init__(title=title, description=description)
        self._sync = False
        self.checks: dict[int, Gtk.CheckButton] = {}
        first = None
        for value, (name, text) in options.items():
            check = Gtk.CheckButton(valign=Gtk.Align.CENTER)
            if first is None:
                first = check
            else:
                check.set_group(first)
            check.connect("toggled", self._toggled, value, on_change)
            row = Adw.ActionRow(title=name, subtitle=text, activatable_widget=check)
            row.add_prefix(check)
            self.add(row)
            self.checks[value] = check

    def _toggled(self, check: Gtk.CheckButton, value: int, on_change) -> None:
        if check.get_active() and not self._sync:
            on_change(value)

    def set_value(self, value: int) -> None:
        self._sync = True
        try:
            if value in self.checks:
                self.checks[value].set_active(True)
        finally:
            self._sync = False


class MicPage(Adw.PreferencesPage):
    def __init__(self, window) -> None:
        super().__init__()
        self.win = window
        levels = Adw.PreferencesGroup(title=t("levels"))
        self.mic_level = ScaleRow(t("mic_level"), t("mic_level_desc"),
                                  0, p.SLIDER_MAX, lambda v: window.set_slider(Slider.MIC_LEVEL, v))
        self.sidetone = ScaleRow(t("sidetone"), t("sidetone_desc"),
                                 0, p.SLIDER_MAX, lambda v: window.set_slider(Slider.SIDETONE, v))
        levels.add(self.mic_level)
        levels.add(self.sidetone)
        self.add(levels)
        self.noise_gate = ChoiceGroup(t("noise_gate"), t("noise_gate_desc"),
                                      {m: NOISE_GATE_TEXT[m] for m in NoiseGate}, window.set_noise_gate)
        self.add(self.noise_gate)
        self.mic_eq = ChoiceGroup(t("mic_eq"), t("mic_eq_desc"),
                                  {m: MIC_EQ_TEXT[m] for m in MicEq}, window.set_mic_eq)
        self.add(self.mic_eq)

    def update(self, state) -> None:
        self.mic_level.set_value(state.live.sliders.get(Slider.MIC_LEVEL, 0))
        self.sidetone.set_value(state.live.sliders.get(Slider.SIDETONE, 0))
        self.noise_gate.set_value(state.live.noise_gate)
        self.mic_eq.set_value(state.live.mic_eq)


class StreamPage(Adw.PreferencesPage):
    ROWS = ((Slider.STREAM_GAME, "stream_game"),
            (Slider.STREAM_CHAT, "stream_chat"),
            (Slider.STREAM_MIC, "stream_mic"),
            (Slider.STREAM_AUX, "stream_aux"))

    def __init__(self, window) -> None:
        super().__init__()
        group = Adw.PreferencesGroup(title=t("stream_port"), description=t("stream_port_desc"))
        self.rows = {}
        for slider, key in self.ROWS:
            row = ScaleRow(t(key), t(f"{key}_desc"), 0, p.SLIDER_MAX, lambda v, s=slider: window.set_slider(s, v))
            group.add(row)
            self.rows[slider] = row
        self.add(group)

    def update(self, state) -> None:
        for slider, row in self.rows.items():
            row.set_value(state.live.sliders.get(slider, 0))


class DevicePage(Adw.PreferencesPage):
    def __init__(self, window) -> None:
        super().__init__()
        self._sync = False
        live = Adw.PreferencesGroup(title=t("status"))
        self.battery_row = Adw.ActionRow(title=t("battery"))
        self.battery_bar = Gtk.LevelBar(min_value=0, max_value=100, valign=Gtk.Align.CENTER)
        self.battery_bar.set_size_request(SCALE_WIDTH, -1)
        self.battery_label = Gtk.Label(width_chars=6, xalign=1, css_classes=["numeric"])
        self.battery_row.add_suffix(self.battery_bar)
        self.battery_row.add_suffix(self.battery_label)
        live.add(self.battery_row)
        self.mix_row = Adw.ActionRow(title=t("current_balance"), subtitle=t("current_balance_desc"))
        self.mix_bar = Gtk.LevelBar(min_value=0, max_value=p.BALANCE_MAX, valign=Gtk.Align.CENTER,
                                    mode=Gtk.LevelBarMode.CONTINUOUS)
        self.mix_bar.set_size_request(SCALE_WIDTH, -1)
        for offset in ("low", "high", "full"):
            self.mix_bar.remove_offset_value(offset)
        self.mix_label = Gtk.Label(width_chars=6, xalign=1, css_classes=["numeric"])
        self.mix_row.add_suffix(Gtk.Label(label=t("chat"), css_classes=["dim-label", "caption"]))
        self.mix_row.add_suffix(self.mix_bar)
        self.mix_row.add_suffix(Gtk.Label(label=t("game"), css_classes=["dim-label", "caption"]))
        self.mix_row.add_suffix(self.mix_label)
        live.add(self.mix_row)
        self.add(live)

        settings = Adw.PreferencesGroup(title=t("settings"))
        self.balance = ScaleRow(t("default_balance"), t("default_balance_desc"),
                                0, p.BALANCE_MAX, window.set_balance, mark=p.BALANCE_MAX // 2 + 1,
                                left=t("chat"), right=t("game"), fmt=balance_text)
        self.alert = ScaleRow(t("alert_tones"), t("alert_tones_desc"),
                              0, p.ALERT_MAX, window.set_alert_volume, unit=" %")
        self.brightness = ScaleRow(t("led_brightness"), t("led_brightness_desc"),
                                   p.BRIGHTNESS_MIN, p.BRIGHTNESS_MAX, window.set_brightness)
        self.auto_off = Adw.ComboRow(title=t("auto_off"), subtitle=t("auto_off_desc"),
                                     model=Gtk.StringList.new([t("after_minutes", minutes=m)
                                                               for m in p.AUTO_OFF_MINUTES]))
        self.auto_off.connect("notify::selected", self._on_auto_off, window)
        for row in (self.balance, self.alert, self.brightness, self.auto_off):
            settings.add(row)
        self.add(settings)

        info = Adw.PreferencesGroup(title=t("base_station"))
        self.fw_row = Adw.ActionRow(title=t("base_firmware"), css_classes=["property"])
        self.build_row = Adw.ActionRow(title=t("build_date"), css_classes=["property"])
        self.headset_fw_row = Adw.ActionRow(title=t("headset_firmware"), css_classes=["property"])
        self.node_row = Adw.ActionRow(title=t("device"), css_classes=["property"])
        for row in (self.fw_row, self.build_row, self.headset_fw_row, self.node_row):
            row.set_subtitle_selectable(True)
            info.add(row)
        self.add(info)

    def _on_auto_off(self, row, _pspec, window) -> None:
        if not self._sync:
            window.set_auto_off(row.get_selected())

    def update(self, state, node: str = "") -> None:
        self.update_status(state)
        self.balance.set_value(state.live.balance)
        self.alert.set_value(state.live.alert_volume)
        self.brightness.set_value(state.live.brightness)
        self._sync = True
        try:
            self.auto_off.set_selected(state.live.auto_off)
        finally:
            self._sync = False
        if state.base:
            self.fw_row.set_subtitle(f"{state.base.version}.{state.base_minor}")
            self.build_row.set_subtitle(state.base.build_date)
        self.headset_fw_row.set_subtitle(headset_version_text(state))
        self.node_row.set_subtitle(f"ASTRO A50 Gen 4 · {node}" if node else "ASTRO A50 Gen 4")

    def update_status(self, state) -> None:
        self.battery_bar.set_value(state.battery)
        self.battery_label.set_label(f"{state.battery} %")
        self.battery_row.set_subtitle(headset_text(state))
        self.headset_fw_row.set_subtitle(headset_version_text(state))
        self.mix_bar.set_value(state.mix)
        self.mix_label.set_label(balance_text(state.mix))
