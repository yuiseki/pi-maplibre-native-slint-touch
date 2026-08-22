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
#   REF     upstream ref to build against (default: the PR #68 merge commit)
#   REPO    upstream URL
#   SYNC    1 to move an existing checkout onto REF (default: leave it alone)
set -eu

HERE="$(cd "$(dirname "$0")/.." && pwd)"            # the hdmi/ dir
WORK="${WORK:-$HOME/poc/mln-slint-cpp}"
# The app was upstreamed as PR #68 and its branch was then deleted, so there is
# no branch to track. Upstream has since refactored the backend into a reusable
# mbgl-slint library (#73), which these sources do not yet follow -- so the ref
# is pinned to the merge commit rather than to main. Moving to a newer upstream
# is a porting job, not a build fix.
REF="${REF:-1f32a5a}"
REPO="${REPO:-https://github.com/maplibre/maplibre-native-slint}"
SYNC="${SYNC:-0}"

if [ ! -d "$WORK/.git" ]; then
  echo "== cloning $REPO -> $WORK =="
  git clone --recurse-submodules "$REPO" "$WORK"
  git -C "$WORK" checkout "$REF"
  git -C "$WORK" submodule update --init --recursive
elif [ "$SYNC" = "1" ]; then
  echo "== moving $WORK onto $REF =="
  git -C "$WORK" fetch origin
  git -C "$WORK" checkout "$REF"
  git -C "$WORK" submodule update --init --recursive
else
  # An existing checkout is left exactly as it is: it carries the overlaid
  # sources as modifications by design, and a stray checkout/fetch here has
  # already cost one debugging session. Pass SYNC=1 to move it deliberately.
  echo "== using existing checkout: $(git -C "$WORK" rev-parse --short HEAD) =="
fi

echo "== overlay app sources from $HERE onto $WORK/cpp =="
cp "$HERE/main_gl.cpp" "$HERE/gl_map_window.slint" "$WORK/cpp/"
cp "$HERE/src/"*.hpp "$HERE/src/"*.cpp "$WORK/cpp/src/"
cp "$HERE/platform/"*.hpp "$HERE/platform/"*.cpp "$WORK/cpp/platform/"
# Screensaver assets (@image-url paths in gl_map_window.slint resolve relative
# to the .slint file, i.e. cpp/assets/).
mkdir -p "$WORK/cpp/assets"
cp "$HERE/assets/"* "$WORK/cpp/assets/" 2>/dev/null || true

# Configure only when there is nothing to reuse. Re-configuring an already
# working tree is not free here: at this pinned ref Slint is fetched from the
# moving release/1 branch, so CMake's update step tries to rebase the local
# checkout onto whatever upstream Slint is today and dies in merge conflicts --
# leaving the tree needing a regeneration that then fails on every build too.
# FETCHCONTENT_UPDATES_DISCONNECTED stops that update step; RECONFIGURE=1
# forces the configure when a flag really has to change.
if [ ! -f "$WORK/build/CMakeCache.txt" ] || [ "${RECONFIGURE:-0}" = "1" ]; then
  echo "== configure (OpenGL backend + FemtoVG GL + libseat linuxkms) =="
  # OpenGL_GL_PREFERENCE=GLVND: maplibre-native links mbgl-core against
  # OpenGL::GLX even though we render through EGL/KMS, and CMake's FindOpenGL
  # only defines that target in GLVND mode. Left at the default (LEGACY) it
  # picks libGL.so, reports "found components: GLX", defines no OpenGL::GLX,
  # and the generate step fails. Needs libopengl-dev (libOpenGL.so) plus the
  # X11/GLX headers -- see README.
  cmake -S "$WORK" -B "$WORK/build" -DCMAKE_BUILD_TYPE=Release \
    -DOpenGL_GL_PREFERENCE=GLVND \
    -DMLN_WITH_OPENGL=ON -DMLN_WITH_WEBGPU=OFF -DMLN_WITH_GLFW=OFF \
    -DSLINT_FEATURE_RENDERER_FEMTOVG=ON -DSLINT_FEATURE_BACKEND_LINUXKMS=ON \
    -DFETCHCONTENT_UPDATES_DISCONNECTED=ON
else
  echo "== reusing existing configuration in $WORK/build =="
fi

echo "== build maplibre-slint-gl =="
cmake --build "$WORK/build" --target maplibre-slint-gl -j"$(nproc)"

echo "== done: $WORK/build/cpp/maplibre-slint-gl =="
