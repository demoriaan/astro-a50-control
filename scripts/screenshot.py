#!/usr/bin/env python3
"""Render every page of the running app to PNG files (dev tool).

Usage: gtk4-broadwayd :5 & GDK_BACKEND=broadway BROADWAY_DISPLAY=:5 \
       python3 scripts/screenshot.py OUTDIR [WIDTHxHEIGHT]
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import gi  # noqa: E402

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
gi.require_version("Graphene", "1.0")
from gi.repository import GLib, Graphene, Gtk  # noqa: E402

from a50control.app import Application  # noqa: E402

OUT = sys.argv[1]
SIZE = tuple(int(v) for v in (sys.argv[2] if len(sys.argv) > 2 else "1000x860").split("x"))
PAGES = ["eq", "mic", "stream", "device"]


def render(win, path):
    content = win.get_content()
    w, h = content.get_width(), content.get_height()
    snap = Gtk.Snapshot()
    Gtk.WidgetPaintable.new(content).snapshot(snap, w, h)
    node = snap.to_node()
    if node is None:
        print("nothing rendered yet")
        return False
    rect = Graphene.Rect().init(0, 0, w, h)
    win.get_native().get_renderer().render_texture(node, rect).save_to_png(path)
    print("wrote", path)
    return True


def main():
    os.makedirs(OUT, exist_ok=True)
    app = Application()
    queue = list(PAGES)

    def step():
        win = app.window
        if win is None:
            return True
        win.set_default_size(*SIZE)
        if win.root.get_visible_child_name() != "main":
            if getattr(step, "waited", 0) > 40:
                render(win, os.path.join(OUT, "status.png"))
                app.quit()
                return False
            step.waited = getattr(step, "waited", 0) + 1
            return True
        if not queue:
            app.quit()
            return False
        page = queue[0]
        if win.view_stack.get_visible_child_name() != page:
            win.view_stack.set_visible_child_name(page)
            return True
        if render(win, os.path.join(OUT, f"{page}.png")):
            queue.pop(0)
        return True

    GLib.timeout_add(500, step)
    app.run([sys.argv[0]])


if __name__ == "__main__":
    main()
