#!/usr/bin/env bash
# Manual launch on the display host (no systemd): a persistent tmux session.
# Use this for interactive debugging / capturing video; for unattended boot use
# the systemd unit instead (systemd/maplibre-slint-gl.service).
#
# Requires: seatd active, the user in the `video` group, an HDMI display
# connected to the vc4 connector, the binary at ~/maplibre-slint-gl, and
# ~/mls-libs populated.
set -eu

STYLE="${MAPLIBRE_STYLE_URL:-https://yuiseki.dev/static/styles/osm-bright.json}"

tmux kill-session -t mapgl 2>/dev/null || true
tmux new-session -d -s mapgl \
  "env SLINT_BACKEND=linuxkms LD_LIBRARY_PATH=$HOME/mls-libs \
   MAPLIBRE_STYLE_URL=$STYLE \
   $HOME/maplibre-slint-gl 2>&1 | tee $HOME/map-gl.log"

echo "launched in tmux session 'mapgl' (logs: ~/map-gl.log)"
echo "attach: tmux attach -t mapgl   |   stop: tmux kill-session -t mapgl"
