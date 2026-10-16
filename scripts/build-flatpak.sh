#!/bin/sh
# Build a single-file Flatpak bundle (dist/a50control.flatpak) without flatpak-builder.
# The app is pure Python, so the GNOME Platform runtime doubles as the SDK.
# Usage: scripts/build-flatpak.sh [runtime-version]   (default: 48)
set -eu

SRC=$(cd "$(dirname "$0")/.." && pwd)
APP_ID=de.donno.A50Control
VERSION=${1:-48}
WORK=$SRC/.work/flatpak
BUILD=$WORK/build
REPO=$WORK/repo
mkdir -p "$SRC/dist"
rm -rf "$BUILD"

flatpak build-init "$BUILD" "$APP_ID" org.gnome.Platform org.gnome.Platform "$VERSION"
FILES=$BUILD/files
mkdir -p "$FILES/lib/a50control" "$FILES/bin" "$FILES/share/applications" \
    "$FILES/share/icons/hicolor/scalable/apps" "$FILES/share/metainfo"
cp -r "$SRC/a50control" "$FILES/lib/a50control/"
find "$FILES/lib" -name __pycache__ -type d -prune -exec rm -rf {} +
cat > "$FILES/bin/a50control" <<'EOF'
#!/bin/sh
PYTHONPATH=/app/lib/a50control exec python3 -m a50control "$@"
EOF
chmod 755 "$FILES/bin/a50control"
cp "$SRC/data/$APP_ID.desktop" "$FILES/share/applications/"
cp "$SRC/a50control/data/icons/hicolor/scalable/apps/$APP_ID.svg" "$FILES/share/icons/hicolor/scalable/apps/"
cp "$SRC/data/$APP_ID.metainfo.xml" "$FILES/share/metainfo/"

# hidraw nodes need --device=all; the host still needs data/70-astro-a50.rules.
flatpak build-finish "$BUILD" --command=a50control \
    --socket=wayland --socket=fallback-x11 --share=ipc --device=all
flatpak build-export "$REPO" "$BUILD"
flatpak build-bundle "$REPO" "$SRC/dist/a50control.flatpak" "$APP_ID"
echo "Bundle: $SRC/dist/a50control.flatpak"
echo "Install: flatpak install --user $SRC/dist/a50control.flatpak"
