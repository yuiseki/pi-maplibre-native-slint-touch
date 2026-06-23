#!/usr/bin/env bash
# Build the HDMI zero-copy GL app (maplibre-slint-gl).
#
# The app is a target inside maplibre/maplibre-native-slint (upstreamed in
# PR #68). It cannot build standalone (it needs Slint, mbgl-core and cpr from
# that project), so we build it inside a checkout of that repo and overlay the
# canonical app sources from this directory on top.
#
# Run ON the build host (aarch64, e.g. a Raspberry Pi 5). First build compiles
# maplibre-native from source and is slow (tens of minutes).
#
# Env overrides:
#   WORK    maplibre-native-slint checkout dir (default: ~/poc/mln-slint-cpp)
#   BRANCH  branch to build (default: cpp-zero-copy-gl-example; use main once
#           PR #68 is merged)
#   REPO    upstream URL
set -eu

HERE="$(cd "$(dirname "$0")/.." && pwd)"            # the hdmi/ dir
WORK="${WORK:-$HOME/poc/mln-slint-cpp}"
BRANCH="${BRANCH:-cpp-zero-copy-gl-example}"
REPO="${REPO:-https://github.com/maplibre/maplibre-native-slint}"

if [ ! -d "$WORK/.git" ]; then
  echo "== cloning $REPO -> $WORK =="
  git clone --recurse-submodules "$REPO" "$WORK"
fi
echo "== checkout $BRANCH =="
git -C "$WORK" fetch origin "$BRANCH"
git -C "$WORK" checkout "$BRANCH"
git -C "$WORK" submodule update --init --recursive

echo "== overlay app sources from $HERE onto $WORK/cpp =="
cp "$HERE/main_gl.cpp" "$HERE/gl_map_window.slint" "$WORK/cpp/"
cp "$HERE/src/"*.hpp "$HERE/src/"*.cpp "$WORK/cpp/src/"
cp "$HERE/platform/"*.hpp "$HERE/platform/"*.cpp "$WORK/cpp/platform/"
# Screensaver assets (@image-url paths in gl_map_window.slint resolve relative
# to the .slint file, i.e. cpp/assets/).
mkdir -p "$WORK/cpp/assets"
cp "$HERE/assets/"* "$WORK/cpp/assets/" 2>/dev/null || true

echo "== configure (OpenGL backend + FemtoVG GL + libseat linuxkms) =="
cmake -S "$WORK" -B "$WORK/build" -DCMAKE_BUILD_TYPE=Release \
  -DMLN_WITH_OPENGL=ON -DMLN_WITH_WEBGPU=OFF -DMLN_WITH_GLFW=OFF \
  -DSLINT_FEATURE_RENDERER_FEMTOVG=ON -DSLINT_FEATURE_BACKEND_LINUXKMS=ON

echo "== build maplibre-slint-gl =="
cmake --build "$WORK/build" --target maplibre-slint-gl -j"$(nproc)"

echo "== done: $WORK/build/cpp/maplibre-slint-gl =="
