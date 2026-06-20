#!/usr/bin/env bash
# Build on the build host and deploy to the display host's systemd service.
# Run ON the build host. Usage: scripts/deploy.sh <display-host>
set -eu
H="${1:?usage: deploy.sh <display-host>}"
cargo build --release --features linuxkms-noseat
BIN=target/release/maplibre_native_slint

ssh "$H" 'sudo systemctl stop maplibre-slint.service 2>/dev/null || true; sleep 1'
scp "$BIN" "$H":~/maplibre_native_slint
ssh "$H" 'sudo systemctl start maplibre-slint.service; sleep 3; systemctl is-active maplibre-slint.service'
