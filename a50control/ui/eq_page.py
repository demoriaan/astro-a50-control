"""Equalizer page: preset slots, curve editor, per-band controls."""
from __future__ import annotations

import copy

from gi.repository import Adw, Gio, GLib, Gtk

from .. import presets as library
from .. import protocol as p
from .eq_graph import BAND_COLORS, EqGraph

BAND_TITLES = ("Bass", "Band 2", "Band 3", "Band 4", "Höhen")
BAND_KINDS = ("Low-Shelf", "Glocke", "Glocke", "Glocke", "High-Shelf")


class BandColumn(Gtk.Box):
    def __init__(self, page: "EqPage", index: int) -> None:
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=6, hexpand=True)
        self.add_css_class("band-column")
        self.index = index
        dot = Gtk.Label(label="●", css_classes=["band-dot", f"band-{index}"])
        title = Gtk.Label(label=BAND_TITLES[index], css_classes=["heading"])
        head = Gtk.Box(spacing=4, halign=Gtk.Align.CENTER)
        head.append(dot)
        head.append(title)
        self.gain_label = Gtk.Label(css_classes=["numeric", "title-4"])
        self.scale = Gtk.Scale.new_with_range(Gtk.Orientation.VERTICAL, p.GAIN_MIN, p.GAIN_MAX, 1)
        self.scale.set_inverted(True)
        self.scale.set_draw_value(False)
        self.scale.set_vexpand(True)
        self.scale.set_size_request(-1, 150)
        self.scale.set_halign(Gtk.Align.CENTER)
        self.scale.add_mark(0, Gtk.PositionType.RIGHT, None)
        self.scale.connect("value-changed", lambda s: page.on_gain_scale(index, int(round(s.get_value()))))
        self.freq_label = Gtk.Label(css_classes=["dim-label", "caption", "numeric"])
        for widget in (head, self.gain_label, self.scale, self.freq_label):
            self.append(widget)
        click = Gtk.GestureClick()
        click.connect("pressed", lambda *_: page.select_band(index))
        self.add_controller(click)

    def update(self, band: p.Band, selected: bool) -> None:
        self.scale.set_value(band.gain)
        self.gain_label.set_label(f"{band.gain:+d} dB" if band.gain else "0 dB")
        self.freq_label.set_label(f"{band.freq} Hz" + (f" · {band.bandwidth:.1f}" if band.bandwidth else ""))
        if selected:
            self.add_css_class("selected")
        else:
            self.remove_css_class("selected")


class EqPage(Gtk.ScrolledWindow):
    def __init__(self, window) -> None:
        super().__init__(hscrollbar_policy=Gtk.PolicyType.NEVER)
        self.win = window
        self.selected = 0
        self._sync = False
        self.bands: list[p.Band] = [p.Band() for _ in range(p.BAND_COUNT)]
        self.preset_index = 0

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=18,
                      margin_top=18, margin_bottom=24, margin_start=12, margin_end=12)
        self.set_child(Adw.Clamp(maximum_size=980, child=box))

        top = Gtk.Box(spacing=12)
        self.preset_buttons: list[Gtk.ToggleButton] = []
        slots = Gtk.Box(css_classes=["linked"], hexpand=True)
        for i in range(p.PRESET_COUNT):
            btn = Gtk.ToggleButton(hexpand=True, css_classes=["preset-button"])
            if self.preset_buttons:
                btn.set_group(self.preset_buttons[0])
            btn.connect("toggled", self._on_preset_toggled, i)
            self.preset_buttons.append(btn)
            slots.append(btn)
        top.append(slots)
        top.append(self._library_button())
        box.append(top)

        self.graph = EqGraph()
        self.graph.connect("band-changed", lambda _g, i: self._on_graph_changed(i))
        self.graph.connect("band-selected", lambda _g, i: self.select_band(i))
        frame = Gtk.Frame(child=self.graph, css_classes=["eq-card"])
        box.append(frame)
        hint = Gtk.Label(label="Punkte ziehen: Pegel & Frequenz · Mausrad: Bandbreite (Bänder 2–4)",
                         css_classes=["dim-label", "caption"], halign=Gtk.Align.START)
        box.append(hint)

        strip = Gtk.Box(spacing=6, homogeneous=True, css_classes=["band-strip"])
        self.columns = [BandColumn(self, i) for i in range(p.BAND_COUNT)]
        for col in self.columns:
            strip.append(col)
        box.append(strip)

        self.detail = Adw.PreferencesGroup()
        self.freq_row = Adw.SpinRow.new_with_range(p.FREQ_MIN, p.FREQ_MAX, 1)
        self.freq_row.set_title("Frequenz")
        self.freq_row.set_subtitle("Hz")
        self.freq_row.connect("notify::value", self._on_freq_row)
        self.bw_row = Adw.SpinRow.new_with_range(p.BW_MIN, p.BW_MAX, 0.1)
        self.bw_row.set_digits(1)
        self.bw_row.set_title("Bandbreite")
        self.bw_row.set_subtitle("größer = breiter")
        self.bw_row.connect("notify::value", self._on_bw_row)
        self.detail.add(self.freq_row)
        self.detail.add(self.bw_row)
        box.append(self.detail)

        name_group = Adw.PreferencesGroup(title="Preset")
        self.name_row = Adw.EntryRow(title="Name", show_apply_button=True)
        self.name_row.set_max_length(p.NAME_MAX)
        self.name_row.connect("apply", self._on_name_apply)
        name_group.add(self.name_row)
        box.append(name_group)

    # -- building blocks ---------------------------------------------------

    def _library_button(self) -> Gtk.MenuButton:
        menu = Gio.Menu()
        factory = Gio.Menu()
        for i, preset in enumerate(library.factory_presets()):
            factory.append(preset.name.title(), f"eq.factory({i})")
        menu.append_submenu("Werks-Preset laden", factory)
        menu.append("Neutral (alles 0 dB)", "eq.flat")
        files = Gio.Menu()
        files.append("Aus Datei importieren…", "eq.import")
        files.append("Als Datei exportieren…", "eq.export")
        menu.append_section(None, files)

        group = Gio.SimpleActionGroup()
        act = Gio.SimpleAction.new("factory", GLib.VariantType.new("i"))
        act.connect("activate", lambda _a, v: self._apply(library.factory_presets()[v.get_int32()]))
        group.add_action(act)
        for name, cb in (("flat", self._apply_flat), ("import", self._import), ("export", self._export)):
            a = Gio.SimpleAction.new(name, None)
            a.connect("activate", lambda *_a, cb=cb: cb())
            group.add_action(a)
        self.insert_action_group("eq", group)
        return Gtk.MenuButton(icon_name="view-more-symbolic", menu_model=menu, tooltip_text="Preset-Bibliothek",
                              valign=Gtk.Align.CENTER)

    # -- state -------------------------------------------------------------

    def update(self, state) -> None:
        self._sync = True
        try:
            self.preset_index = state.active_preset
            for i, btn in enumerate(self.preset_buttons):
                btn.set_label(state.live.presets[i].name or f"EQ {i + 1}")
                btn.set_active(i == state.active_preset)
            preset = state.live.presets[self.preset_index]
            self.bands = copy.deepcopy(preset.bands)
            self.name_row.set_text(preset.name)
            self._refresh()
        finally:
            self._sync = False

    def _refresh(self) -> None:
        was = self._sync
        self._sync = True
        try:
            self.graph.set_bands(self.bands)
            self.graph.select(self.selected)
            for i, col in enumerate(self.columns):
                col.update(self.bands[i], i == self.selected)
            band = self.bands[self.selected]
            self.detail.set_title(f"{BAND_TITLES[self.selected]} · {BAND_KINDS[self.selected]}")
            self.freq_row.set_value(band.freq)
            peaking = self.selected in p.PEAKING_BANDS
            self.bw_row.set_sensitive(peaking)
            self.bw_row.set_value(band.bandwidth if peaking else p.BW_MIN)
        finally:
            self._sync = was

    def select_band(self, index: int) -> None:
        self.selected = index
        self._refresh()

    # -- edits -------------------------------------------------------------

    def _store(self) -> list[p.Band]:
        live = self.win.state.live.presets[self.preset_index]
        live.bands = copy.deepcopy(self.bands)
        self.win.mark_changed()
        return live.bands

    def _send_gains(self) -> None:
        idx, gains = self.preset_index, [b.gain for b in self.bands]
        self._store()
        self.win.run(("gains", idx), lambda d: d.set_gains(idx, gains))

    def _send_band(self, b: int) -> None:
        idx, band = self.preset_index, copy.copy(self.bands[b])
        self._store()
        self.win.run(("band", idx, b), lambda d: d.set_band(idx, b, band.freq, band.bandwidth))

    def on_gain_scale(self, index: int, gain: int) -> None:
        if self._sync or self.bands[index].gain == gain:
            return
        self.bands[index].gain = gain
        self.selected = index
        self._refresh()
        self._send_gains()

    def _on_graph_changed(self, index: int) -> None:
        old = self.bands[index]
        new = self.graph.bands[index]
        gain_changed = old.gain != new.gain
        band_changed = (old.freq, old.bandwidth) != (new.freq, new.bandwidth)
        self.bands[index] = copy.copy(new)
        self.selected = index
        self._refresh()
        if gain_changed:
            self._send_gains()
        if band_changed:
            self._send_band(index)

    def _on_freq_row(self, row, _pspec) -> None:
        value = int(row.get_value())
        if self._sync or value == self.bands[self.selected].freq:
            return
        self.bands[self.selected].freq = value
        self._refresh()
        self._send_band(self.selected)

    def _on_bw_row(self, row, _pspec) -> None:
        value = round(row.get_value(), 1)
        if self._sync or self.selected not in p.PEAKING_BANDS or value == self.bands[self.selected].bandwidth:
            return
        self.bands[self.selected].bandwidth = value
        self._refresh()
        self._send_band(self.selected)

    def _on_preset_toggled(self, btn: Gtk.ToggleButton, index: int) -> None:
        if self._sync or not btn.get_active():
            return
        self.win.activate_preset(index)

    def _on_name_apply(self, row: Adw.EntryRow) -> None:
        name = row.get_text().strip()
        if not name:
            self.win.toast("Der Name darf nicht leer sein")
            return
        idx = self.preset_index
        self.win.state.live.presets[idx].name = name
        self.preset_buttons[idx].set_label(name)
        self.win.mark_changed()
        self.win.run(("name", idx), lambda d: d.set_name(idx, name))

    # -- library -----------------------------------------------------------

    def _apply(self, preset: p.Preset) -> None:
        self.win.apply_preset(self.preset_index, preset)

    def _apply_flat(self) -> None:
        current = self.win.state.live.presets[self.preset_index]
        flat = p.Preset(current.name, [p.Band(0, b.freq, b.bandwidth) for b in current.bands])
        self._apply(flat)

    def _import(self) -> None:
        dialog = Gtk.FileDialog(title="EQ-Preset importieren", filters=_ini_filters())

        def done(dlg, result):
            try:
                file = dlg.open_finish(result)
            except GLib.Error:
                return
            try:
                self._apply(library.load_ini(file.get_path()))
            except (OSError, ValueError, KeyError) as exc:
                self.win.toast(f"Import fehlgeschlagen: {exc}")

        dialog.open(self.win, None, done)

    def _export(self) -> None:
        preset = copy.deepcopy(self.win.state.live.presets[self.preset_index])
        dialog = Gtk.FileDialog(title="EQ-Preset exportieren", filters=_ini_filters(),
                                initial_name=f"{preset.name or 'preset'}.ini")

        def done(dlg, result):
            try:
                file = dlg.save_finish(result)
            except GLib.Error:
                return
            try:
                library.save_ini(preset, file.get_path())
                self.win.toast(f"„{preset.name}“ exportiert")
            except OSError as exc:
                self.win.toast(f"Export fehlgeschlagen: {exc}")

        dialog.save(self.win, None, done)


def _ini_filters() -> Gio.ListStore:
    flt = Gtk.FileFilter(name="ASTRO EQ-Presets (*.ini)")
    flt.add_pattern("*.ini")
    store = Gio.ListStore.new(Gtk.FileFilter)
    store.append(flt)
    return store


def band_css() -> str:
    return "\n".join(f".band-{i} {{ color: {c}; }}" for i, c in enumerate(BAND_COLORS))
