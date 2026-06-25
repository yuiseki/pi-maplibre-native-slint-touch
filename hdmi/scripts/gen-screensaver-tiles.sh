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
S_LIBERTY="https://yuiseki.dev/static/styles/osm-liberty.json"
S_BASIC="https://yuiseki.dev/static/styles/maptiler-basic-ja.json"

# Start from a clean set so removed entries don't linger on the host.
rm -f "$OUT"/*.png

# name lat lon zoom style
# Each place is rendered at ~3 zoom levels for variety in the bounce cycle.
gen() {
  xvfb-run -a -s "-screen 0 480x480x24" \
    "$BIN" -s "$5" -o "$OUT/$1.png" -z "$4" -x "$3" -y "$2" \
    -w "$TILE" -h "$TILE" \
    >/dev/null 2>&1
  echo "$1.png -> $(file -b "$OUT/$1.png" 2>&1 | cut -c1-32)"
}

# OSM Bright — Tokyo / Kyoto
gen bright-tokyo-z6   35.6895 139.6917  6 "$S_BRIGHT"
gen bright-tokyo-z11  35.6895 139.6917 11 "$S_BRIGHT"
gen bright-tokyo-z16  35.6812 139.7671 16 "$S_BRIGHT"
gen bright-kyoto-z9   35.0116 135.7681  9 "$S_BRIGHT"
gen bright-kyoto-z13  35.0116 135.7681 13 "$S_BRIGHT"
gen bright-kyoto-z16  35.0036 135.7780 16 "$S_BRIGHT"
# OSM Liberty — alpine cities so every zoom has content: Natural Earth
# shaded relief + labels at low zoom, city detail higher (avoids the
# "all mountain / all sea" featureless tiles a bare summit/ocean gives).
gen liberty-matsumoto-z5   36.2381 137.9720  5 "$S_LIBERTY"
gen liberty-matsumoto-z9   36.2381 137.9720  9 "$S_LIBERTY"
gen liberty-matsumoto-z13  36.2381 137.9720 13 "$S_LIBERTY"
gen liberty-innsbruck-z5   47.2692 11.4041   5 "$S_LIBERTY"
gen liberty-innsbruck-z9   47.2692 11.4041   9 "$S_LIBERTY"
gen liberty-innsbruck-z13  47.2692 11.4041  13 "$S_LIBERTY"
# MapTiler Basic — Osaka / Hiroshima
gen basic-osaka-z6    34.6937 135.5023  6 "$S_BASIC"
gen basic-osaka-z11   34.6937 135.5023 11 "$S_BASIC"
gen basic-osaka-z16   34.6863 135.5258 16 "$S_BASIC"
gen basic-hiro-z8     34.3853 132.4553  8 "$S_BASIC"
gen basic-hiro-z12    34.3853 132.4553 12 "$S_BASIC"
gen basic-hiro-z16    34.3955 132.4596 16 "$S_BASIC"

# FOSS4G past host cities (2020 cancelled by COVID): a Liberty wide view
# (shaded relief shows where in the world) + a city-detail zoom.
gen foss4g-2021-buenosaires-z5  -34.6037 -58.3816  5 "$S_LIBERTY"
gen foss4g-2021-buenosaires-z12 -34.6037 -58.3816 12 "$S_BRIGHT"
gen foss4g-2022-firenze-z5       43.7696  11.2558   5 "$S_LIBERTY"
gen foss4g-2022-firenze-z13      43.7696  11.2558  13 "$S_BASIC"
gen foss4g-2023-prizren-z6       42.2139  20.7397   6 "$S_LIBERTY"
gen foss4g-2023-prizren-z13      42.2139  20.7397  13 "$S_BRIGHT"
gen foss4g-2024-belem-z5         -1.4558 -48.4902   5 "$S_LIBERTY"
gen foss4g-2024-belem-z12        -1.4558 -48.4902  12 "$S_BASIC"
gen foss4g-2025-auckland-z6     -36.8485 174.7633   6 "$S_LIBERTY"
gen foss4g-2025-auckland-z12    -36.8485 174.7633  12 "$S_BRIGHT"

echo "tiles in $OUT:"
ls -1 "$OUT"
echo "copy to a display host with:  scp $OUT/*.png <host>:~/screensaver-tiles/"
