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

S_BRIGHT="https://yuiseki.dev/static/styles/osm-bright.json"
S_FIORD="https://yuiseki.dev/static/styles/osm-fiord.json"

# name lat lon zoom style
gen() {
  xvfb-run -a -s "-screen 0 480x480x24" \
    "$BIN" -s "$5" -o "$OUT/$1.png" -z "$4" -x "$3" -y "$2" -w "$TILE" -h "$TILE" \
    >/dev/null 2>&1
  echo "$1.png -> $(file -b "$OUT/$1.png" 2>&1 | cut -c1-32)"
}

gen tokyo   35.6895 139.6917 5 "$S_BRIGHT"
gen paris   48.8566 2.3522   5 "$S_BRIGHT"
gen ny      40.7128 -74.0060 5 "$S_BRIGHT"
gen hiro    34.3853 132.4553 6 "$S_BRIGHT"
gen london  51.5074 -0.1278  5 "$S_FIORD"
gen sf      37.7749 -122.4194 6 "$S_FIORD"
gen tokyo9  35.6812 139.7671 9 "$S_BRIGHT"
gen osaka   34.6937 135.5023 7 "$S_FIORD"

echo "tiles in $OUT:"
ls -1 "$OUT"
echo "copy to a display host with:  scp $OUT/*.png <host>:~/screensaver-tiles/"
