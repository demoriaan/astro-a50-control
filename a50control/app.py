"""GTK application entry point."""
from __future__ import annotations

import sys
from pathlib import Path

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
gi.require_version("PangoCairo", "1.0")

from gi.repository import Adw, Gdk, Gio, Gtk  # noqa: E402

from . import APP_ID, __version__  # noqa: E402
from .i18n import t  # noqa: E402
from .ui.eq_page import band_css  # noqa: E402
from .ui.window import MainWindow  # noqa: E402

DATA = Path(__file__).resolve().parent / "data"


class Application(Adw.Application):
    def __init__(self) -> None:
        super().__init__(application_id=APP_ID, flags=Gio.ApplicationFlags.DEFAULT_FLAGS)
        self.window = None

    def do_startup(self) -> None:
        Adw.Application.do_startup(self)
        Adw.StyleManager.get_default().set_color_scheme(Adw.ColorScheme.PREFER_DARK)
        display = Gdk.Display.get_default()
        Gtk.IconTheme.get_for_display(display).add_search_path(str(DATA / "icons"))
        css = Gtk.CssProvider()
        text = (DATA / "style.css").read_text() + band_css()
        if hasattr(css, "load_from_string"):  # GTK >= 4.12
            css.load_from_string(text)
        else:
            css.load_from_data(text, -1)
        Gtk.StyleContext.add_provider_for_display(display, css, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
        for name, cb, accels in (("about", self._about, []), ("quit", self._quit, ["<Control>q"])):
            action = Gio.SimpleAction.new(name, None)
            action.connect("activate", cb)
            self.add_action(action)
            self.set_accels_for_action(f"app.{name}", accels)

    def do_activate(self) -> None:
        if self.window is None:
            self.window = MainWindow(self)
        self.window.present()

    def _quit(self, *_args) -> None:
        # Close through the window so pending writes are flushed (close-request).
        if self.window is not None:
            self.window.close()
        else:
            self.quit()

    def _about(self, *_args) -> None:
        info = dict(application_name="A50 Control", application_icon=APP_ID, version=__version__,
                    developer_name="A50 Control contributors",
                    comments=t("about_comments"),
                    license_type=Gtk.License.MIT_X11)
        if hasattr(Adw, "AboutDialog"):
            Adw.AboutDialog(**info).present(self.window)
        else:
            Adw.AboutWindow(transient_for=self.window, **info).present()


def main() -> int:
    return Application().run(sys.argv)
