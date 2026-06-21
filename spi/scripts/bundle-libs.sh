#!/usr/bin/env bash
# Bundle the build-host's versioned libraries that the display host lacks
# (different Debian release => different SONAMEs / runtime version checks).
# Run ON the build host. Usage: scripts/bundle-libs.sh <display-host>
#
# Observed needed for bookworm(build) -> trixie(display):
#   libicuuc.so.72 libicui18n.so.72 libicudata.so.72  (ICU 72 vs 76)
#   libpng16.so.16  (runtime version assert)
#   libuv.so.1
# Do NOT bundle the GPU/display stack (libEGL/libGL/Mesa/libdrm/libgbm) -
# those must come from the target so they match its kernel/driver.
set -eu
H="${1:?usage: bundle-libs.sh <display-host>}"
LIBDIR=/usr/lib/aarch64-linux-gnu
STAGE=$(mktemp -d)

for f in libicuuc.so.72 libicui18n.so.72 libicudata.so.72 libpng16.so.16; do
  cp -L "$LIBDIR/$f" "$STAGE/$f" && echo "staged $f"
done
UV=$(find /usr/lib /lib -name 'libuv.so.1' 2>/dev/null | head -1)
[ -n "$UV" ] && cp -L "$UV" "$STAGE/libuv.so.1" && echo "staged libuv.so.1"

tar c -C "$STAGE" . | ssh "$H" 'mkdir -p ~/mls-libs && tar x -C ~/mls-libs && echo "bundled into ~/mls-libs:" && ls ~/mls-libs'
rm -rf "$STAGE"
