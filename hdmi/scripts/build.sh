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
#   OVERLAY_ONLY  1 to copy the sources and stop (used by the tests)
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

# Copy only what actually differs. An unconditional cp changes every mtime on
# every run, so ninja rebuilds the world -- and, far worse, sees CMakeLists.txt
# as modified and re-runs CMake's configure. See the note on configuring below
# for why that is dangerous here; the cheapest way to never trigger it is to
# leave the file alone when it has not changed.
copied=0
unchanged=0
put() {
  local src="$1" dst="$2"
  if [ -f "$dst" ] && cmp -s "$src" "$dst"; then
    unchanged=$((unchanged + 1))
    return
  fi
  cp "$src" "$dst"
  copied=$((copied + 1))
}

echo "== overlay app sources from $HERE onto $WORK/cpp =="
put "$HERE/main_gl.cpp" "$WORK/cpp/main_gl.cpp"
put "$HERE/gl_map_window.slint" "$WORK/cpp/gl_map_window.slint"
for f in "$HERE/src/"*.hpp "$HERE/src/"*.cpp; do
  [ -e "$f" ] && put "$f" "$WORK/cpp/src/$(basename "$f")"
done
for f in "$HERE/platform/"*.hpp "$HERE/platform/"*.cpp; do
  [ -e "$f" ] && put "$f" "$WORK/cpp/platform/$(basename "$f")"
done

# ...and the target definition that lists them. Overlaying sources without it
# means a new file is copied but never compiled, and the failure arrives as an
# undefined reference at link time rather than anywhere near the cause.
# The upstream file ends with this target, so replace from its banner to EOF.
CM="$WORK/cpp/CMakeLists.txt"
MARK="# --- Zero-copy OpenGL example (maplibre-slint-gl) ---"
if grep -qF "$MARK" "$CM"; then
  awk -v mark="$MARK" 'index($0, mark) {exit} {print}' "$CM" > "$CM.tmp"
  # Keep only the target definition from our copy (its header is commentary
  # about not being a standalone project, which does not apply once inlined).
  awk '/^if\(MLN_WITH_OPENGL/{s=1} s' "$HERE/CMakeLists.txt" \
      | sed "1i $MARK" >> "$CM.tmp"
  if cmp -s "$CM.tmp" "$CM"; then
    rm -f "$CM.tmp"
    unchanged=$((unchanged + 1))
  else
    mv "$CM.tmp" "$CM"
    copied=$((copied + 1))
    echo "== overlaid the maplibre-slint-gl target definition =="
  fi
else
  echo "WARN: could not find the target banner in $CM; not overlaying it" >&2
fi

# Screensaver assets (@image-url paths in gl_map_window.slint resolve relative
# to the .slint file, i.e. cpp/assets/).
mkdir -p "$WORK/cpp/assets"
for f in "$HERE/assets/"*; do
  [ -e "$f" ] && put "$f" "$WORK/cpp/assets/$(basename "$f")"
done

if [ "$copied" = "0" ]; then
  echo "== overlay: all $unchanged files unchanged (nothing to regenerate) =="
else
  echo "== overlay: $copied changed, $unchanged unchanged =="
fi

# Used by the tests: everything above is pure file shuffling and can be checked
# anywhere; everything below needs the build host.
[ "${OVERLAY_ONLY:-0}" = "1" ] && exit 0

# Configure only when there is nothing to reuse. Re-configuring an already
# working tree is not free here: at this pinned ref Slint is fetched from the
# moving release/1 branch, so CMake's update step tries to rebase the local
# checkout onto whatever upstream Slint is today and dies in merge conflicts --
# leaving the tree needing a regeneration that then fails on every build too.
# FETCHCONTENT_UPDATES_DISCONNECTED stops that update step -- but FetchContent
# also honours a per-dependency FETCHCONTENT_UPDATES_DISCONNECTED_<NAME>, which
# takes precedence over the global one. An existing cache here was found with
# the global ON and _SLINT still OFF, i.e. protected in name only, so both are
# set. RECONFIGURE=1 forces the configure when a flag really has to change.
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
    -DFETCHCONTENT_UPDATES_DISCONNECTED=ON \
    -DFETCHCONTENT_UPDATES_DISCONNECTED_SLINT=ON
else
  echo "== reusing existing configuration in $WORK/build =="
fi

echo "== build maplibre-slint-gl =="
cmake --build "$WORK/build" --target maplibre-slint-gl -j"$(nproc)"

echo "== done: $WORK/build/cpp/maplibre-slint-gl =="
