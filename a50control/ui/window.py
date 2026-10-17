"""Main window: connection lifecycle, header bar and page switching."""
from __future__ import annotations

import copy
import time

from gi.repository import Adw, Gdk, Gio, GLib, Gtk

from .. import protocol as p
from ..device import A50
from ..transport import DeviceBusy, DeviceGone, DevicePermissionError, ForeignTraffic, Timeout, Transport, \
    find_devices
from ..worker import Worker
from .eq_page import EqPage
from .pages import DevicePage, MicPage, StreamPage

POLL_MS = 2000
FOREIGN_TEXT = ("Ein anderes Programm spricht gleichzeitig mit der Basisstation, zum Beispiel ein Browser-Tab "
                "mit der A50-Control-Web-App. Schließe es und verbinde dann neu – sonst können Antworten "
                "vertauscht werden.")
POLL_FAILURES_BEFORE_RESET = 3
TOAST_REPEAT_S = 8.0
UDEV_HINT = ("sudo install -m644 data/70-astro-a50.rules /etc/udev/rules.d/ && "
             "sudo udevadm control --reload && sudo udevadm trigger --subsystem-match=hidraw")


class MainWindow(Adw.ApplicationWindow):
    def __init__(self, app: Adw.Application) -> None:
        super().__init__(application=app, title="A50 Control", default_width=1000, default_height=820)
        self.set_size_request(360, 480)
        self.worker = Worker()
        self.state = None
        self.node = ""
        self._connecting = False
        self._paused = False  # no automatic reconnect after foreign traffic until the user asks
        self._preset_gen = 0
        self._poll_failures = 0
        self._last_toast = ("", 0.0)
        self._writes = 0  # bumped per queued write; full re-reads only apply if unchanged
        self._saving = False

        self.view_stack = Adw.ViewStack()
        self.eq_page = EqPage(self)
        self.mic_page = MicPage(self)
        self.stream_page = StreamPage(self)
        self.device_page = DevicePage(self)
        for page, name, title, icon in (
                (self.eq_page, "eq", "Equalizer", "a50-equalizer-symbolic"),
                (self.mic_page, "mic", "Mikrofon", "audio-input-microphone-symbolic"),
                (self.stream_page, "stream", "Stream-Port", "a50-stream-symbolic"),
                (self.device_page, "device", "Gerät", "audio-headphones-symbolic")):
            self.view_stack.add_titled_with_icon(page, name, title, icon)

        self.header = Adw.HeaderBar()
        self.switcher = Adw.ViewSwitcher(stack=self.view_stack, policy=Adw.ViewSwitcherPolicy.WIDE)
        self.title = Adw.WindowTitle(title="A50 Control")
        self.header.set_title_widget(self.title)
        self.battery_icon = Gtk.Image(icon_name="battery-missing-symbolic")
        self.battery_label = Gtk.Label(css_classes=["numeric", "caption-heading"])
        battery = Gtk.Box(spacing=4, tooltip_text="Akku des Headsets")
        battery.append(self.battery_icon)
        battery.append(self.battery_label)
        self.header.pack_start(battery)
        self.header.pack_end(self._menu_button())
        self.save_button = Gtk.Button(label="Speichern", css_classes=["suggested-action"],
                                      tooltip_text="Einstellungen dauerhaft im Headset speichern (Strg+S)",
                                      action_name="win.save")
        self.revert_button = Gtk.Button(icon_name="edit-undo-symbolic",
                                        tooltip_text="Ungespeicherte Änderungen verwerfen", action_name="win.revert")
        self.header.pack_end(self.save_button)
        self.header.pack_end(self.revert_button)

        self.switcher_bar = Adw.ViewSwitcherBar(stack=self.view_stack)
        self.status_page = Adw.StatusPage(icon_name="audio-headphones-symbolic")
        self.status_button = Gtk.Button(label="Erneut suchen", halign=Gtk.Align.CENTER,
                                        css_classes=["pill", "suggested-action"])
        self.status_button.connect("clicked", self._on_status_button)
        self._status_mode = "search"
        self.status_page.set_child(self.status_button)
        self.root = Gtk.Stack(transition_type=Gtk.StackTransitionType.CROSSFADE)
        self.root.add_named(self.status_page, "status")
        self.root.add_named(self.view_stack, "main")

        view = Adw.ToolbarView(content=self.root)
        view.add_top_bar(self.header)
        view.add_bottom_bar(self.switcher_bar)
        self.toasts = Adw.ToastOverlay(child=view)
        self.set_content(self.toasts)

        narrow = Adw.Breakpoint.new(Adw.BreakpointCondition.parse("max-width: 640sp"))
        narrow.add_setter(self.switcher_bar, "reveal", True)
        narrow.add_setter(self.switcher, "visible", False)
        self.add_breakpoint(narrow)

        self._add_actions()
        self._show_status("Suche Basisstation …", "Verbinde die ASTRO A50 Basisstation per USB.", busy=True)
        self.connect("close-request", self._on_close)
        self._tick_source = GLib.timeout_add(POLL_MS, self._tick)
        GLib.idle_add(self._first_tick)

    # -- chrome ------------------------------------------------------------

    def _menu_button(self) -> Gtk.MenuButton:
        menu = Gio.Menu()
        menu.append("Neu einlesen", "win.refresh")
        menu.append("Über A50 Control", "app.about")
        return Gtk.MenuButton(icon_name="open-menu-symbolic", menu_model=menu, primary=True,
                              tooltip_text="Hauptmenü")

    def _add_actions(self) -> None:
        for name, cb, accel in (("save", self.save, "<Control>s"), ("revert", self.revert, None),
                                ("refresh", self.refresh, "F5")):
            action = Gio.SimpleAction.new(name, None)
            action.connect("activate", lambda *_a, cb=cb: cb())
            self.add_action(action)
            if accel:
                self.get_application().set_accels_for_action(f"win.{name}", [accel])

    def toast(self, text: str) -> None:
        last_text, last_time = self._last_toast
        now = time.monotonic()
        if text == last_text and now - last_time < TOAST_REPEAT_S:
            return
        self._last_toast = (text, now)
        self.toasts.add_toast(Adw.Toast(title=GLib.markup_escape_text(text), timeout=4))

    def _show_status(self, title: str, text: str, busy: bool = False, button: str | None = "Erneut suchen") -> None:
        self._status_mode = "search"
        self.status_page.set_title(title)
        self.status_page.set_description(text)
        self.status_page.set_icon_name("content-loading-symbolic" if busy else "audio-headphones-symbolic")
        self.status_button.set_visible(button is not None)
        if button:
            self.status_button.set_label(button)
        self.root.set_visible_child_name("status")
        self.header.set_title_widget(self.title)
        self.switcher_bar.set_visible(False)
        self.mark_changed()
        self.battery_label.set_label("")
        self.battery_icon.set_from_icon_name("battery-missing-symbolic")

    def _show_main(self) -> None:
        self.root.set_visible_child_name("main")
        self.header.set_title_widget(self.switcher)
        self.switcher_bar.set_visible(True)
        self.mark_changed()

    # -- connection --------------------------------------------------------

    def _first_tick(self) -> bool:
        self._tick()
        return GLib.SOURCE_REMOVE

    def _tick(self) -> bool:
        if self.worker.device is None:
            if not self._paused:
                self._connect()
        elif not self.worker.pending("poll"):
            gen = self._preset_gen
            self.worker.submit("poll", lambda d: copy.deepcopy(d.read_status()),
                               lambda status: self._on_poll(status, gen), self._on_poll_error)
        return GLib.SOURCE_CONTINUE

    def _connect(self) -> None:
        if self._connecting or self.worker.device is not None:
            return
        paths = find_devices()
        if not paths:
            self.state = None
            self._show_status("Keine Basisstation gefunden",
                              "Schließe die ASTRO A50 (Gen 4) Basisstation per USB an. "
                              "Sie wird automatisch erkannt.")
            return
        self._connecting = True
        path = paths[0]

        def job(_dev):
            dev = A50(Transport(path))
            try:
                state = dev.read_all()
            except Exception:
                dev.close()
                raise
            self.worker.device = dev
            return copy.deepcopy(state), dev

        self.worker.submit("connect", job, lambda res: self._on_connected(*res, path), self._on_connect_error,
                           needs_device=False)

    def _on_connected(self, state, dev, path: str) -> None:
        self._connecting = False
        self._paused = False
        if dev is not self.worker.device:
            return  # dropped again before this callback ran (e.g. the first poll failed)
        self.node = path
        self.state = state
        self.sync_all()
        self._show_main()
        self.toast("Basisstation verbunden")

    def _on_connect_error(self, exc: Exception, _dev=None) -> None:
        self._connecting = False
        if self.worker.device is not None:
            return  # a stale attempt failed while another one succeeded
        if isinstance(exc, ForeignTraffic):
            self._pause_for_foreign()
            return
        if isinstance(exc, DevicePermissionError):
            self._show_status("Kein Zugriff auf die Basisstation",
                              f"Linux erlaubt den Zugriff auf {exc.path} nur root. Installiere einmalig die "
                              f"udev-Regel aus dem Projektordner:\n\n<tt>{GLib.markup_escape_text(UDEV_HINT)}</tt>",
                              button="Befehl kopieren")
            self._status_mode = "permission"
            return
        if isinstance(exc, DeviceBusy):
            self._show_status("Basisstation belegt",
                              "Ein anderes Programm (z. B. die Kommandozeile von A50 Control) "
                              "spricht gerade mit der Basisstation. Es wird automatisch erneut versucht.")
            return
        self._show_status("Verbindung fehlgeschlagen", GLib.markup_escape_text(str(exc)))

    def _on_status_button(self, _button) -> None:
        if self._status_mode == "permission":
            Gdk.Display.get_default().get_clipboard().set(UDEV_HINT)
            self.toast("Befehl in die Zwischenablage kopiert")
        else:
            self._paused = False
            self.refresh()

    def _pause_for_foreign(self) -> None:
        if self.worker.device is not None:
            self._disconnect("Andere Software greift auf die Basisstation zu")
        self._paused = True
        self._show_status("Basisstation wird mitbenutzt", FOREIGN_TEXT, button="Erneut verbinden")

    def _on_close(self, *_args) -> bool:
        GLib.source_remove(self._tick_source)
        self.worker.shutdown()  # sends queued writes (bounded), then closes the device
        return False

    def _disconnect(self, message: str) -> None:
        dev, self.worker.device = self.worker.device, None
        self.worker.clear()
        self._connecting = False
        self._poll_failures = 0
        if dev is not None:
            # Close on the worker so the GTK thread never waits for a running transaction.
            self.worker.submit(("close", id(dev)), lambda _d: dev.close(), needs_device=False)
        self.state = None
        self.toast(message)
        self._show_status("Basisstation getrennt", "Warte auf die ASTRO A50 Basisstation …", busy=True)

    # -- state propagation -------------------------------------------------

    def sync_all(self) -> None:
        self.eq_page.update(self.state)
        self.mic_page.update(self.state)
        self.stream_page.update(self.state)
        self.device_page.update(self.state, self.node)
        self._update_header()

    def _update_header(self) -> None:
        s = self.state
        if not s.headset_on:
            self.battery_icon.set_from_icon_name("battery-missing-symbolic")
            self.battery_label.set_label("Headset aus")
        else:
            level = min(100, int(round(s.battery / 10.0)) * 10)
            suffix = "-charging" if s.charging else ""
            self.battery_icon.set_from_icon_name(f"battery-level-{level}{suffix}-symbolic")
            self.battery_label.set_label(f"{s.battery} %")
        self.mark_changed()

    def mark_changed(self) -> None:
        dirty = self.state is not None and self.worker.device is not None and self.state.dirty
        self.lookup_action("save").set_enabled(dirty and not self._saving)
        self.lookup_action("revert").set_enabled(dirty)

    def _on_poll(self, status, gen: int) -> None:
        self._poll_failures = 0
        if self.state is None:
            return
        s = self.state
        s.battery, s.charging, s.mix = status.battery, status.charging, status.mix
        s.headset_on, s.docked, s.headset_version = status.headset_on, status.docked, status.headset_version
        # Only adopt the headset's preset if the user did not switch since this poll was queued.
        if gen == self._preset_gen and status.active_preset != s.active_preset:
            s.active_preset = status.active_preset
            self.eq_page.update(s)
        self.device_page.update_status(s)
        self._update_header()

    def _on_poll_error(self, exc: Exception, dev) -> None:
        if dev is not self.worker.device:
            return
        if isinstance(exc, DeviceGone):
            self._disconnect("Verbindung zur Basisstation verloren")
            return
        if isinstance(exc, ForeignTraffic):
            self._pause_for_foreign()
            return
        if not isinstance(exc, (Timeout, OSError)):
            return  # the station answered, just not usefully; no reason to reconnect
        self._poll_failures += 1
        if self._poll_failures >= POLL_FAILURES_BEFORE_RESET:
            self._disconnect("Basisstation antwortet nicht – verbinde neu")

    def _on_error(self, exc: Exception, dev=None) -> None:
        if dev is not None and dev is not self.worker.device:
            return  # error from a connection that has already been replaced
        if isinstance(exc, DeviceGone):
            self._disconnect("Verbindung zur Basisstation verloren")
            return
        if isinstance(exc, ForeignTraffic):
            self._pause_for_foreign()
            return
        if isinstance(exc, Timeout):
            self.toast("Keine Antwort von der Basisstation")
        elif isinstance(exc, p.DeviceError):
            self.toast(f"Gerät meldet: {exc.message}")
        else:
            self.toast(f"Fehler: {exc}")
        self._resync()

    def _resync(self, toast: str | None = None) -> None:
        """Re-read everything so the UI never shows values a failed write did not set."""
        writes = self._writes

        def done(state):
            if writes != self._writes:
                self._resync(toast)  # an edit was queued after this read; read again
                return
            self.state = state
            self.sync_all()
            self._show_main()
            if toast:
                self.toast(toast)

        self.worker.submit("refresh", lambda d: copy.deepcopy(d.read_all()), done, self._on_poll_error)

    # -- actions used by pages --------------------------------------------

    def run(self, key, fn) -> None:
        """Queue a write job; the latest job per key wins."""
        if self.worker.device is not None:
            self._writes += 1
            self.worker.submit(key, fn, None, self._on_error)

    def activate_preset(self, index: int) -> None:
        self.state.active_preset = index
        self._preset_gen += 1
        self.eq_page.update(self.state)
        self.run("preset", lambda d: d.set_active_preset(index))

    def apply_preset(self, index: int, preset: p.Preset) -> None:
        name = p.clip_name(preset.name or self.state.live.presets[index].name)
        preset = p.Preset(name, copy.deepcopy(preset.bands))
        self.state.live.presets[index] = preset
        self.eq_page.update(self.state)
        self.mark_changed()
        snapshot = copy.deepcopy(preset)
        self.run(("apply", index), lambda d: d.apply_preset(index, snapshot))
        self.toast(f"„{name}“ geladen – zum Behalten speichern")

    def set_slider(self, slider: int, value: int) -> None:
        self.state.live.sliders[slider] = value
        self.mark_changed()
        self.run(("slider", slider), lambda d: d.set_slider(slider, value))

    def set_noise_gate(self, mode: int) -> None:
        self.state.live.noise_gate = mode
        self.mark_changed()
        self.run("noise-gate", lambda d: d.set_noise_gate(mode))

    def set_mic_eq(self, mode: int) -> None:
        self.state.live.mic_eq = mode
        self.mark_changed()
        self.run("mic-eq", lambda d: d.set_mic_eq(mode))

    def set_balance(self, value: int) -> None:
        self.state.live.balance = value
        self.mark_changed()
        self.run("balance", lambda d: d.set_balance(value))

    def set_alert_volume(self, value: int) -> None:
        self.state.live.alert_volume = value
        self.mark_changed()
        self.run("alert", lambda d: d.set_alert_volume(value))

    def set_brightness(self, value: int) -> None:
        self.state.live.brightness = value
        self.mark_changed()
        self.run("brightness", lambda d: d.set_brightness(value))

    def set_auto_off(self, option: int) -> None:
        self.state.live.auto_off = option
        self.mark_changed()
        self.run("auto-off", lambda d: d.set_auto_off(option))

    def save(self) -> None:
        if self.worker.device is None or self.state is None:
            return

        def done(saved):
            self._saving = False
            if self.state is None:
                return
            self.state.saved = saved
            self.mark_changed()
            self.toast("Im Headset gespeichert")

        def failed(exc, dev):
            self._saving = False
            self._on_error(exc, dev)
            self.mark_changed()

        self._saving = True
        self.worker.submit("save", lambda d: (d.save(), copy.deepcopy(d.state.saved))[1], done, failed)
        self.mark_changed()

    def revert(self) -> None:
        if self.worker.device is None:
            return

        writes = self._writes

        def done(state):
            if writes != self._writes:
                self._resync("Änderungen verworfen")
                return
            self.state = state
            self.sync_all()
            self.toast("Änderungen verworfen")

        self.worker.submit("revert", lambda d: (d.revert(), copy.deepcopy(d.state))[1], done, self._on_error)

    def refresh(self) -> None:
        if self.worker.device is None:
            self._connect()
            return

        self._resync("Einstellungen neu eingelesen")
