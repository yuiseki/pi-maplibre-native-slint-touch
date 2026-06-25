#!/usr/bin/env bash
# Pre-render the bouncing-tile screensaver images with mbgl-render.
#
# Why: in the HDMI/zero-copy-GL build, Slint cannot decode PNGs (@image-url /
# load_from_path do not render) and glReadPixels from the custom FBO returns
# black on V3D, so the live map cannot be cropped on-device. Instead we bake a
# set of map-tile PNGs offline with mbgl-render and the app loads them as
# SharedPixelBuffers. See docs/hdmi-gl-rendering-notes.md.
#
# mbgl-render is GLX-only in this build, so run it headless under xvfb
# (software GL / llvmpipe is fine for an offline bake).
#
# Run ON the build host (the maplibre-native-slint checkout). Output goes to
# $OUT; copy it to each display host's ~/screensaver-tiles/.
#
# Env: WORK (checkout dir), OUT (output dir), TILE (tile px).
set -u

WORK="${WORK:-$HOME/poc/mln-slint-cpp}"
OUT="${OUT:-$HOME/screensaver-tiles}"
TILE="${TILE:-220}"
BIN="$WORK/build/vendor/maplibre-native/bin/mbgl-render"

if [ ! -x "$BIN" ]; then
  echo "building mbgl-render…"
  cmake --build "$WORK/build" --target mbgl-render -j"$(nproc)"
fi
command -v xvfb-run >/dev/null || { echo "need xvfb (apt install xvfb)"; exit 1; }
mkdir -p "$OUT"

# The same styles offered in the on-device dropdown (all self-hosted on
# yuiseki.dev). The bouncing-tile stage cycles through every PNG in $OUT, so
# adding more styles/regions here just adds variety — no app change needed.
S_BRIGHT="https://yuiseki.dev/static/styles/osm-bright.json"
S_FIORD="https://yuiseki.dev/static/styles/osm-fiord.json"
S_LIBERTY="https://yuiseki.dev/static/styles/osm-liberty.json"
S_BASIC="https://yuiseki.dev/static/styles/maptiler-basic-ja.json"
S_3D="https://yuiseki.dev/static/styles/maptiler-3d.json"

# Start from a clean set so removed entries don't linger on the host.
rm -f "$OUT"/*.png

# name lat lon zoom style [pitch] [bearing]   (pitch/bearing default 0)
gen() {
  xvfb-run -a -s "-screen 0 480x480x24" \
    "$BIN" -s "$5" -o "$OUT/$1.png" -z "$4" -x "$3" -y "$2" \
    -p "${6:-0}" -b "${7:-0}" -w "$TILE" -h "$TILE" \
    >/dev/null 2>&1
  echo "$1.png -> $(file -b "$OUT/$1.png" 2>&1 | cut -c1-32)"
}

# OSM Bright
gen bright-tokyo   35.6895 139.6917  5 "$S_BRIGHT"
gen bright-osaka   34.6937 135.5023  7 "$S_BRIGHT"
# OSM Fiord
gen fiord-paris    48.8566 2.3522    5 "$S_FIORD"
gen fiord-sf       37.7749 -122.4194 6 "$S_FIORD"
# OSM Liberty (low zoom shows the Natural Earth shaded relief)
gen liberty-ny     40.7128 -74.0060  4 "$S_LIBERTY"
gen liberty-alps   46.5000 8.0000    5 "$S_LIBERTY"
# MapTiler Basic
gen basic-hiro     34.3853 132.4553  6 "$S_BASIC"
gen basic-tokyo9   35.6812 139.7671  9 "$S_BASIC"
# MapTiler 3D (pitched + slight bearing to show extruded buildings)
gen 3d-shibuya     35.6595 139.7004 16 "$S_3D" 60 20
gen 3d-marunouchi  35.6812 139.7649 16 "$S_3D" 55 0

echo "tiles in $OUT:"
ls -1 "$OUT"
echo "copy to a display host with:  scp $OUT/*.png <host>:~/screensaver-tiles/"
