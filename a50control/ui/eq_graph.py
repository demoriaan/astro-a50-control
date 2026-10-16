"""Interactive EQ curve: drag handles for gain/frequency, scroll for bandwidth."""
from __future__ import annotations

import copy
import math

from gi.repository import Adw, GObject, Gtk, Pango, PangoCairo

from .. import eq_math
from .. import protocol as p

F_LO, F_HI = 20.0, 20000.0
DB_RANGE = 15.0
PAD_L, PAD_R, PAD_T, PAD_B = 38, 14, 14, 26
HANDLE_R = 9
BAND_COLORS = ("#ff7a6b", "#ffb35c", "#7bd88f", "#5ec8f2", "#b48cff")
GRID_FREQS = (20, 50, 100, 200, 500, 1000, 2000, 5000, 10000, 20000)
FREQS = eq_math.log_freqs(F_LO, F_HI, 260)


def hex_rgb(value: str) -> tuple[float, float, float]:
    return tuple(int(value[i:i + 2], 16) / 255 for i in (1, 3, 5))


def fmt_freq(f: float) -> str:
    return f"{f / 1000:g}k" if f >= 1000 else f"{f:g}"


class EqGraph(Gtk.DrawingArea):
    __gsignals__ = {
        # band index; values are read from .bands
        "band-changed": (GObject.SignalFlags.RUN_FIRST, None, (int,)),
        "band-selected": (GObject.SignalFlags.RUN_FIRST, None, (int,)),
    }

    def __init__(self) -> None:
        super().__init__(hexpand=True, content_height=280)
        self.add_css_class("eq-graph")
        self.bands = [p.Band() for _ in range(p.BAND_COUNT)]
        self.selected = 0
        self._hover: int | None = None
        self._drag: int | None = None
        self._drag_origin = (0.0, 0.0)
        self.set_draw_func(self._draw)

        self._drag_gesture = drag = Gtk.GestureDrag()
        drag.connect("drag-begin", self._on_drag_begin)
        drag.connect("drag-update", self._on_drag_update)
        drag.connect("drag-end", lambda *_: self._end_drag())
        self.add_controller(drag)
        motion = Gtk.EventControllerMotion()
        motion.connect("motion", self._on_motion)
        motion.connect("leave", lambda *_: self._set_hover(None))
        self.add_controller(motion)
        scroll = Gtk.EventControllerScroll(
            flags=Gtk.EventControllerScrollFlags.VERTICAL | Gtk.EventControllerScrollFlags.DISCRETE)
        scroll.connect("scroll", self._on_scroll)
        self.add_controller(scroll)

    # -- public ------------------------------------------------------------

    def set_bands(self, bands: list[p.Band], cancel_drag: bool = False) -> None:
        """Show new band values; ``cancel_drag`` aborts a drag of stale (e.g. other preset) data."""
        if cancel_drag and self._drag is not None:
            self._drag_gesture.reset()
            self._drag = None
        if self._drag is None:
            self.bands = copy.deepcopy(bands)
            self.queue_draw()

    def select(self, index: int) -> None:
        self.selected = index
        self.queue_draw()

    # -- geometry ----------------------------------------------------------

    def _plot(self) -> tuple[float, float, float, float]:
        w, h = self.get_width(), self.get_height()
        return PAD_L, PAD_T, max(w - PAD_L - PAD_R, 1), max(h - PAD_T - PAD_B, 1)

    def _x(self, f: float) -> float:
        x0, _, w, _ = self._plot()
        return x0 + math.log(f / F_LO) / math.log(F_HI / F_LO) * w

    def _y(self, db: float) -> float:
        _, y0, _, h = self._plot()
        return y0 + (DB_RANGE - max(-DB_RANGE, min(DB_RANGE, db))) / (2 * DB_RANGE) * h

    def _freq_at(self, x: float) -> float:
        x0, _, w, _ = self._plot()
        return F_LO * (F_HI / F_LO) ** ((x - x0) / w)

    def _db_at(self, y: float) -> float:
        _, y0, _, h = self._plot()
        return DB_RANGE - (y - y0) / h * 2 * DB_RANGE

    def _hit(self, x: float, y: float) -> int | None:
        best, dist = None, (HANDLE_R + 8) ** 2
        for i, band in enumerate(self.bands):
            d = (self._x(band.freq) - x) ** 2 + (self._y(band.gain) - y) ** 2
            if d <= dist:
                best, dist = i, d
        return best

    # -- input -------------------------------------------------------------

    def _set_hover(self, index: int | None) -> None:
        if index != self._hover:
            self._hover = index
            self.set_cursor_from_name("grab" if index is not None else None)
            self.queue_draw()

    def _on_motion(self, _ctl, x: float, y: float) -> None:
        if self._drag is None:
            self._set_hover(self._hit(x, y))

    def _on_drag_begin(self, gesture: Gtk.GestureDrag, x: float, y: float) -> None:
        hit = self._hit(x, y)
        if hit is None:
            gesture.set_state(Gtk.EventSequenceState.DENIED)
            return
        self._drag = hit
        self._drag_origin = (x, y)
        self.set_cursor_from_name("grabbing")
        if hit != self.selected:
            self.selected = hit
            self.emit("band-selected", hit)
        self.queue_draw()

    def _on_drag_update(self, _gesture, dx: float, dy: float) -> None:
        if self._drag is None:
            return
        x, y = self._drag_origin[0] + dx, self._drag_origin[1] + dy
        band = self.bands[self._drag]
        gain = int(round(max(p.GAIN_MIN, min(p.GAIN_MAX, self._db_at(y)))))
        freq = int(round(max(p.FREQ_MIN, min(p.FREQ_MAX, self._freq_at(x)))))
        if (gain, freq) != (band.gain, band.freq):
            band.gain, band.freq = gain, freq
            self.queue_draw()
            self.emit("band-changed", self._drag)

    def _end_drag(self) -> None:
        self._drag = None
        self.set_cursor_from_name("grab" if self._hover is not None else None)
        self.queue_draw()

    def _on_scroll(self, _ctl, _dx: float, dy: float) -> bool:
        # Only over a handle; elsewhere the wheel must keep scrolling the page.
        index = self._hover
        if index is None or index not in p.PEAKING_BANDS or dy == 0:
            return False
        band = self.bands[index]
        width = round(max(p.BW_MIN, min(p.BW_MAX, band.bandwidth - 0.1 * (1 if dy > 0 else -1))), 1)
        if width != band.bandwidth:
            band.bandwidth = width
            self.queue_draw()
            self.emit("band-changed", index)
        return True

    # -- drawing -----------------------------------------------------------

    def _draw(self, _area, cr, width: int, height: int) -> None:
        fg = self.get_color()
        accent = _accent_rgb()
        x0, y0, w, h = self._plot()

        cr.set_line_width(1)
        for f in GRID_FREQS:
            x = round(self._x(f)) + 0.5
            cr.set_source_rgba(fg.red, fg.green, fg.blue, 0.07)
            cr.move_to(x, y0)
            cr.line_to(x, y0 + h)
            cr.stroke()
            self._text(cr, fmt_freq(f), x, y0 + h + 6, fg, 0.45, center=True)
        for db in (-12, -6, 0, 6, 12):
            y = round(self._y(db)) + 0.5
            cr.set_source_rgba(fg.red, fg.green, fg.blue, 0.16 if db == 0 else 0.07)
            cr.move_to(x0, y)
            cr.line_to(x0 + w, y)
            cr.stroke()
            self._text(cr, f"{db:+d}" if db else "0", x0 - 8, y - 7, fg, 0.45, right=True)

        response = eq_math.response_db(self.bands, FREQS)
        points = [(self._x(f), self._y(v)) for f, v in zip(FREQS, response)]
        zero = self._y(0)
        cr.move_to(points[0][0], zero)
        for x, y in points:
            cr.line_to(x, y)
        cr.line_to(points[-1][0], zero)
        cr.close_path()
        cr.set_source_rgba(*accent, 0.16)
        cr.fill()
        cr.move_to(*points[0])
        for x, y in points[1:]:
            cr.line_to(x, y)
        cr.set_source_rgba(*accent, 1)
        cr.set_line_width(2.5)
        cr.stroke()

        for i, band in enumerate(self.bands):
            x, y = self._x(band.freq), self._y(band.gain)
            r, g, b = hex_rgb(BAND_COLORS[i])
            active = i in (self.selected, self._hover, self._drag)
            if i in p.PEAKING_BANDS and active:
                half = band.bandwidth / 2
                xa, xb = self._x(band.freq / 2 ** half), self._x(band.freq * 2 ** half)
                cr.set_source_rgba(r, g, b, 0.10)
                cr.rectangle(xa, y0, xb - xa, h)
                cr.fill()
            radius = HANDLE_R + (2 if i == self.selected else 0)
            cr.arc(x, y, radius + 3, 0, 2 * math.pi)
            cr.set_source_rgba(r, g, b, 0.28 if active else 0.0)
            cr.fill()
            cr.arc(x, y, radius, 0, 2 * math.pi)
            cr.set_source_rgba(r, g, b, 1)
            cr.fill()
            self._text(cr, str(i + 1), x, y - 7, None, 1, center=True, bold=True, dark=True)

    def _text(self, cr, text, x, y, fg, alpha, center=False, right=False, bold=False, dark=False) -> None:
        layout = PangoCairo.create_layout(cr)
        font = Pango.FontDescription.from_string(("Sans Bold 8" if bold else "Sans 8"))
        layout.set_font_description(font)
        layout.set_text(text, -1)
        tw = layout.get_pixel_size()[0]
        if center:
            x -= tw / 2
        elif right:
            x -= tw
        if dark:
            cr.set_source_rgba(0.08, 0.08, 0.1, 0.9)
        else:
            cr.set_source_rgba(fg.red, fg.green, fg.blue, alpha)
        cr.move_to(x, y)
        PangoCairo.show_layout(cr, layout)


def _accent_rgb() -> tuple[float, float, float]:
    manager = Adw.StyleManager.get_default()
    if hasattr(manager, "get_accent_color_rgba"):
        c = manager.get_accent_color_rgba()
        return c.red, c.green, c.blue
    return 0.21, 0.52, 0.89
