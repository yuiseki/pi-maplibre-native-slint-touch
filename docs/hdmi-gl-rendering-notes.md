# HDMI / zero-copy-GL rendering notes (hard-won)

These are the rendering constraints of the **HDMI build** (`hdmi/`,
`maplibre-slint-gl`): Slint with the **FemtoVG OpenGL renderer** + the
**linuxkms** backend, where maplibre-native renders into an FBO whose colour
texture is handed to Slint as a *borrowed GL texture* (zero-copy). On the
Raspberry Pi 4 this runs on the **V3D** GPU.

They were established the hard way (a long detour) while building the
screensaver. They differ sharply from the **SPI build** (`spi/`), which uses
Slint's *software* renderer and has none of these limits. Verify everything by
**pixel readback**, never by eyeballing a glossy panel through a webcam (glare
made us misjudge "rendered vs not" repeatedly).

## What renders, what does not

| Element | Renders here? |
| --- | --- |
| The full-window `MMapView` (borrowed GL texture, `create_from_borrowed_gl_2d_rgba_texture`) | ✅ always |
| `Rectangle`, `Text` (incl. created at runtime via `if`) | ✅ |
| `Image` whose source is a **`SharedPixelBuffer`** (raw RGBA), set in C++ | ✅ (startup **and** runtime updates) |
| `Image` with `source: @image-url("…")` (embedded PNG) | ❌ never draws |
| `Image` with `Image::load_from_path("…")` (PNG/JPEG file) | ❌ never draws |
| `glReadPixels` from the custom FBO (texture-backed) | ❌ returns all-black on V3D |
| `glReadPixels` from framebuffer 0 (the scanned-out surface) | ✅ works |
| `glBlitFramebuffer` *scaling* on V3D | ❌ (1:1 copies are fine) |

Net: **Slint's own image-decode path (`@image-url`, `load_from_path`) does not
render in this build.** Only a hand-built `SharedPixelBuffer` does. And you
cannot read the rendered map back out of the FBO with `glReadPixels` (V3D), so
you cannot turn the live map into a CPU image on-device.

## Consequences / recipes

- **Show a PNG (e.g. the DVD logo):** decode it yourself to raw RGBA and feed a
  `SharedPixelBuffer` — do **not** use `@image-url`. mbgl already links an image
  decoder: `mbgl::decodeImage(bytes)` → `PremultipliedImage` → un-premultiply →
  `slint::SharedPixelBuffer<Rgba8Pixel>` → `slint::Image`.
- **A transparent/black logo is invisible on a black field.** Tint it yourself:
  keep only the PNG's alpha as a shape mask and paint the shape in the wanted
  colour into an **opaque** buffer (background black = seamless on the black
  screensaver field). Recolour by regenerating the buffer — runtime
  `set_*_image` updates render fine.
- **Map tiles for the bouncing-tile stage:** you can't crop the live map
  (`glReadPixels` is black). Instead **pre-render tile PNGs with `mbgl-render`**
  and load them as `SharedPixelBuffer`s. `mbgl-render` needs a GL context; it is
  GLX-only in this build (`Error: Failed to open X display`), so run it headless
  under **`xvfb-run`** (software GL / llvmpipe is fine for an offline bake):

  ```sh
  xvfb-run -a -s "-screen 0 320x320x24" \
    build/.../mbgl-render -s "<style-url>" -o tile.png \
      -z 5 -x <lon> -y <lat> -w 220 -h 220
  ```

  See `hdmi/scripts/gen-screensaver-tiles.sh`. The app loads every PNG in
  `~/screensaver-tiles/` at startup and bounces them, cache-swap on each wall
  hit (same idea as the SPI build, but the tiles come from `mbgl-render` instead
  of a `glReadPixels` crop). Because the screensaver never touches the live map,
  no style/camera save-restore is needed.

## TDD via pixel readback (do this instead of using the webcam)

In the `AfterRendering` rendering-notifier state, read the **displayed**
framebuffer (framebuffer 0) and assert on specific pixels — this is exactly what
the panel shows, but precise and instant:

```cpp
glBindFramebuffer(GL_READ_FRAMEBUFFER, 0);
unsigned char px[4];
glReadPixels(x, height - 1 - y, 1, 1, GL_RGBA, GL_UNSIGNED_BYTE, px); // y: top-left -> GL bottom-left
```

`maplibre-slint-gl` has a `MAPLIBRE_SELFTEST=1` mode that freezes the bounce at
(40,40) and logs how many pixels of the 140×140 logo/tile region are non-black
per stage. Expected: stage 0 ≈ all non-black (map), stage 1 = some non-black in
the tint colour (DVD logo shape), stage 2 ≈ all non-black (opaque map tile),
stage 3 = 0 (off / black).

## Why the SPI build is simpler

`spi/` uses Slint's **software** renderer, which decodes/render images (incl.
`@image-url`) normally and can read pixels back, so it ports the family
screensaver directly. The HDMI build pays for zero-copy GL with the limits
above. They are not a sign the feature is "harder" by design — they are
specific quirks of FemtoVG-GL + borrowed textures + V3D.
