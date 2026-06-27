#!/usr/bin/env bash
# Copy the built binary to the display host and (re)start it.
# Run ON the build host after scripts/build.sh. Usage: deploy.sh <display-host>
#
# The display host needs ~/mls-libs populated (build-host libs the target lacks:
# libslint_cpp, libcpr.so.1, libicu*.72, libpng16.so.16). See README.
set -eu

H="${1:?usage: deploy.sh <display-host>}"
WORK="${WORK:-$HOME/poc/mln-slint-cpp}"
BIN="$WORK/build/cpp/maplibre-slint-gl"

[ -x "$BIN" ] || { echo "missing $BIN (run scripts/build.sh first)"; exit 1; }

HERE="$(cd "$(dirname "$0")/.." && pwd)"  # the hdmi/ dir

# Stop whichever launcher is in use (systemd unit or manual tmux).
ssh "$H" 'sudo systemctl stop maplibre-slint-gl.service 2>/dev/null || true; \
          tmux kill-session -t mapgl 2>/dev/null || true; sleep 1'

scp "$BIN" "$H":~/maplibre-slint-gl

# Screensaver assets (see docs/hdmi-gl-rendering-notes.md):
#   ~/dvd-logo.png         DVD logo PNG (decoded to a SharedPixelBuffer in C++)
#   ~/screensaver-tiles/   pre-rendered map-tile PNGs (scripts/gen-screensaver-tiles.sh)
scp "$HERE/assets/dvd-logo.png" "$H":~/dvd-logo.png 2>/dev/null || \
  echo "WARN: hdmi/assets/dvd-logo.png missing (DVD stage will be blank)"

# GPS satellite status-bar icons (decoded to SharedPixelBuffers in C++):
#   ~/sat-grey.png  no GPS device   ~/sat-yellow.png  no fix   ~/sat-green.png  fix
# (@image-url images do NOT render in this femtovg-GL build, so these are
#  loaded by path in C++ and pushed via `in property <image>`.)
for s in grey yellow green; do
  scp "$HERE/assets/sat-$s.png" "$H":~/sat-$s.png 2>/dev/null || \
    echo "WARN: hdmi/assets/sat-$s.png missing (GPS icon stage $s will be blank)"
done

# Wi-Fi / network status-bar icons (same mechanism as the satellite icons):
#   ~/wifi-grey.png  down/disconnected (grey + red X)   ~/wifi-yellow.png  up, no route
#   ~/wifi-green.png  connected with a default route
for s in grey yellow green; do
  scp "$HERE/assets/wifi-$s.png" "$H":~/wifi-$s.png 2>/dev/null || \
    echo "WARN: hdmi/assets/wifi-$s.png missing (Wi-Fi icon stage $s will be blank)"
done

# Keyboard-connected indicator (green keyboard glyph; shown only while a
# USB or Bluetooth keyboard is connected).
scp "$HERE/assets/kbd-green.png" "$H":~/kbd-green.png 2>/dev/null || \
  echo "WARN: hdmi/assets/kbd-green.png missing (keyboard indicator will be blank)"

# Battery status icons (Icons8): charge (plugged) / full / high / middle / low.
for s in charge full high middle low; do
  scp "$HERE/assets/battery-$s.png" "$H":~/battery-$s.png 2>/dev/null || \
    echo "WARN: hdmi/assets/battery-$s.png missing (battery icon $s will be blank)"
done
if ls "$HOME/screensaver-tiles/"*.png >/dev/null 2>&1; then
  ssh "$H" 'mkdir -p ~/screensaver-tiles'
  scp "$HOME/screensaver-tiles/"*.png "$H":~/screensaver-tiles/
else
  echo "NOTE: no ~/screensaver-tiles/*.png on build host;"
  echo "      run scripts/gen-screensaver-tiles.sh first for the map-tile stage."
fi

# Prefer the systemd unit if installed; otherwise fall back to a tmux launch.
ssh "$H" 'if systemctl list-unit-files | grep -q "^maplibre-slint-gl.service"; then
            sudo systemctl start maplibre-slint-gl.service; sleep 3;
            systemctl is-active maplibre-slint-gl.service;
          else
            echo "no systemd unit installed; launching in tmux";
            tmux new-session -d -s mapgl \
              "env SLINT_BACKEND=linuxkms LD_LIBRARY_PATH=$HOME/mls-libs \
               MAPLIBRE_STYLE_URL=https://yuiseki.dev/static/styles/osm-bright.json \
               $HOME/maplibre-slint-gl 2>&1 | tee $HOME/map-gl.log";
            tmux ls;
          fi'
