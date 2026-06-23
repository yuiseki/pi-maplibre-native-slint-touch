# HDMI / zero-copy-GL rendering notes

Notes on the **HDMI build** (`hdmi/`, `maplibre-slint-gl`): Slint with the
**FemtoVG OpenGL renderer** + the **linuxkms** backend, where maplibre-native
renders into an FBO whose colour texture is handed to Slint as a *borrowed GL
texture* (zero-copy). On the Raspberry Pi 4 this is the **V3D** GPU.

The single most important lesson: **verify what renders with pixel readback, not
by eyeballing the panel** (a glossy screen + webcam glare led to several wrong
conclusions during development — e.g. "PNGs don't render", which is false).
`maplibre-slint-gl` has a `MAPLIBRE_SELFTEST=1` mode that does exactly this.

## What renders (all confirmed by `glReadPixels`)

| Element | Renders? |
| --- | --- |
| Full-window `MMapView` (borrowed GL texture) | ✅ |
| `Rectangle`, `Text` (incl. `if`-created at runtime) | ✅ |
| `Image` from a **`SharedPixelBuffer`** (raw RGBA), set in C++ | ✅ (startup and runtime) |
| `Image` from **`@image-url("…")`** (embedded PNG) | ✅ |
| `Image` from **`Image::load_from_path("…")`** | ✅ |
| `glReadPixels` from **framebuffer 0** (the scanned-out surface) | ✅ |
| `glReadPixels` from the **custom texture-backed FBO**, right after rendering | ✅ |
| `glReadPixels` from that FBO at the **start of a frame** (before re-rendering) | ❌ all-black: V3D discards the colour attachment between frames |
| `glBlitFramebuffer` **scaling** on V3D | ❌ (1:1 copies are fine) |

So **Slint's PNG paths render fine here** — there is no Slint image bug. Two
real gotchas remain, plus the readback lesson:

### 1. `colorize` multiplies, so it can't tint black artwork

The DVD-logo PNG is **black art on transparent**. `Image { colorize: <colour> }`
multiplies the source by the colour, so black × colour = black → invisible on
the black screensaver field (0 lit pixels in readback). `colorize` works for
**white/light** monochrome art (white × colour = colour). For our black logo we
instead decode the PNG, keep only its **alpha as a shape mask**, and paint that
shape in the bounce colour into an opaque `SharedPixelBuffer` (`dvd_tinted()` in
`main_gl.cpp`). Recolouring = rebuild the buffer; runtime `set_*_image` updates
render fine.

### 2. The FBO colour attachment is discarded between frames (V3D)

V3D is a tiled GPU and treats the custom FBO's colour attachment as transient:
it is **not preserved across frames**. Reading it (or compositing the borrowed
texture) at the *start* of a frame, before re-rendering, gives all-black; you
must `frontend->render()` every frame. Confirmed by readback: the FBO centre is
`0,0,0,0` at frame start, then the real map colour right after `render()`.

(`glReadPixels` from this FBO itself works fine when you read **right after**
rendering — an earlier "readback returns black" note here was a misdiagnosis of
the cross-frame discard above.) The screensaver still uses **pre-rendered
`mbgl-render` tiles** rather than cropping the live map, but that is a design
choice (varied regions/styles, baked offline), not a readback limitation.

## Pre-rendering map tiles with `mbgl-render`

`mbgl-render` needs a GL context and is GLX-only in this build
(`Error: Failed to open X display`), so run it headless under **`xvfb-run`**
(software GL / llvmpipe is fine for an offline bake):

```sh
xvfb-run -a -s "-screen 0 480x480x24" \
  build/.../mbgl-render -s "<style-url>" -o tile.png \
    -z 5 -x <lon> -y <lat> -w 220 -h 220
```

See `hdmi/scripts/gen-screensaver-tiles.sh`. The app loads every PNG in
`~/screensaver-tiles/` at startup as `SharedPixelBuffer`s and bounces them,
cache-swapping on each wall hit. The screensaver never touches the live map, so
no style/camera save-restore is needed. (An EGL/surfaceless headless backend for
`mbgl-render`, removing the xvfb dependency, would be a nice maplibre-native
contribution.)

## Pixel-readback self-test

In `AfterRendering`, read the displayed framebuffer (framebuffer 0) and assert:

```cpp
glBindFramebuffer(GL_READ_FRAMEBUFFER, 0);
unsigned char px[4];
glReadPixels(x, height - 1 - y, 1, 1, GL_RGBA, GL_UNSIGNED_BYTE, px); // y top-left -> GL bottom-left
```

`MAPLIBRE_SELFTEST=1` freezes the bounce at (40,40) and logs how many pixels of
the 140×140 logo/tile region are non-black per stage: stage 0 ≈ all (map),
stage 1 = the logo shape in the tint colour, stage 2 ≈ all (opaque map tile),
stage 3 = 0 (off). Use this, not the webcam.
