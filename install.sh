#!/bin/sh
# Install A50 Control for the current user.
#   ./install.sh              install to ~/.local (+ udev rule via sudo)
#   ./install.sh --no-udev    skip the udev rule
#   ./install.sh --uninstall  remove the user installation
set -eu

SRC=$(cd "$(dirname "$0")" && pwd)
PREFIX=${PREFIX:-$HOME/.local}
APP_ID=de.donno.A50Control
LIB=$PREFIX/share/a50control
RULE=70-astro-a50.rules
UDEV=yes

case $PREFIX in
    *[\"\$\`\\\|\&\;\%]* | "")
        echo "PREFIX must not be empty or contain quotes, \$, \`, \\, |, &, ; or %: $PREFIX" >&2
        exit 2 ;;
esac

refresh_caches() {
    command -v update-desktop-database >/dev/null 2>&1 && update-desktop-database -q "$PREFIX/share/applications" || true
    if [ -f "$PREFIX/share/icons/hicolor/index.theme" ]; then
        command -v gtk-update-icon-cache >/dev/null 2>&1 && gtk-update-icon-cache -q "$PREFIX/share/icons/hicolor" || true
    else
        rm -f "$PREFIX/share/icons/hicolor/icon-theme.cache"  # stale cache from older installers
    fi
}

for arg in "$@"; do
    case $arg in
        --no-udev) UDEV=no ;;
        --uninstall)
            rm -rf "$LIB" "$PREFIX/bin/a50control" \
                "$PREFIX/share/applications/$APP_ID.desktop" \
                "$PREFIX/share/icons/hicolor/scalable/apps/$APP_ID.svg" \
                "$PREFIX/share/metainfo/$APP_ID.metainfo.xml"
            refresh_caches
            echo "Removed. The udev rule stays in /etc/udev/rules.d/$RULE (delete it with sudo if wanted)."
            exit 0 ;;
        *) echo "unknown option: $arg" >&2; exit 2 ;;
    esac
done

# Pick a Python that has PyGObject with GTK 4 and libadwaita >= 1.4.
CHECK='import gi; gi.require_version("Gtk", "4.0"); gi.require_version("Adw", "1")
from gi.repository import Adw, Gtk
assert (Gtk.get_major_version(), Gtk.get_minor_version()) >= (4, 10), "GTK >= 4.10 needed"
assert (Adw.get_major_version(), Adw.get_minor_version()) >= (1, 4), "libadwaita >= 1.4 needed"'
PYTHON=
for candidate in /usr/bin/python3 /usr/local/bin/python3 python3; do
    if command -v "$candidate" >/dev/null 2>&1 && "$candidate" -c "$CHECK" 2>/dev/null; then
        PYTHON=$(command -v "$candidate")
        break
    fi
done
if [ -z "$PYTHON" ]; then
    cat >&2 <<'EOF'
Python with PyGObject, GTK >= 4.10 and libadwaita >= 1.4 not found. Install it first:
  Arch/Manjaro : sudo pacman -S python-gobject gtk4 libadwaita
  Debian/Ubuntu: sudo apt install python3-gi gir1.2-gtk-4.0 gir1.2-adw-1
  Fedora       : sudo dnf install python3-gobject gtk4 libadwaita
  openSUSE     : sudo zypper install python3-gobject typelib-1_0-Gtk-4_0 typelib-1_0-Adw-1
EOF
    exit 1
fi

rm -rf "$LIB"
mkdir -p "$LIB" "$PREFIX/bin"
cp -r "$SRC/a50control" "$LIB/"
find "$LIB" -name __pycache__ -type d -prune -exec rm -rf {} +

cat > "$PREFIX/bin/a50control" <<EOF
#!/bin/sh
PYTHONPATH="$LIB\${PYTHONPATH:+:\$PYTHONPATH}" exec "$PYTHON" -m a50control "\$@"
EOF
chmod 755 "$PREFIX/bin/a50control"

mkdir -p "$PREFIX/share/applications" "$PREFIX/share/icons/hicolor/scalable/apps" "$PREFIX/share/metainfo"
sed "s|^Exec=.*|Exec=\"$PREFIX/bin/a50control\"|" "$SRC/data/$APP_ID.desktop" \
    > "$PREFIX/share/applications/$APP_ID.desktop"
cp "$SRC/a50control/data/icons/hicolor/scalable/apps/$APP_ID.svg" "$PREFIX/share/icons/hicolor/scalable/apps/"
cp "$SRC/data/$APP_ID.metainfo.xml" "$PREFIX/share/metainfo/"
refresh_caches

echo "Installed A50 Control to $PREFIX (Python: $PYTHON)."

if [ "$UDEV" = yes ]; then
    if cmp -s "$SRC/data/$RULE" "/etc/udev/rules.d/$RULE"; then
        echo "udev rule already installed."
    else
        echo "Installing udev rule /etc/udev/rules.d/$RULE (needs sudo) ..."
        sudo install -m644 "$SRC/data/$RULE" "/etc/udev/rules.d/$RULE"
        sudo udevadm control --reload
        sudo udevadm trigger --subsystem-match=hidraw
    fi
fi
echo "Start it from your app menu or run: a50control"
