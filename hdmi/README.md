# HDMI path: zero-copy GL (`maplibre-slint-gl`)

The HDMI path renders MapLibre Native **directly into an OpenGL texture inside
Slint's own GL context** and hands that texture to Slint as a borrowed texture
(`slint::Image::create_from_borrowed_gl_2d_rgba_texture`). There is no GPU->CPU
readback and no second GL context, so the Raspberry Pi's V3D GPU does the work
and Slint composites the result for free. This is the opposite trade-off from the
[SPI path](../spi/README.md), which renders in software to a legacy framebuffer.

This app is contributed upstream as
[maplibre/maplibre-native-slint#68](https://github.com/maplibre/maplibre-native-slint/pull/68).
The sources here are the canonical Pi copy; `scripts/build.sh` builds them inside
a checkout of that repo (see Build).

## When to use this path

Use it when the panel is driven by a **real DRM/KMS connector with a working
GPU**, i.e. an **HDMI display on the Pi's vc4 connector** (the V3D GPU is then
usable). The SPI fbtft panels have no KMS connector and no GPU scanout path, so
they must use the software [SPI path](../spi/README.md) instead.

Verified on a Raspberry Pi 4 (Debian 13 / trixie, aarch64, V3D 4.2.14.0) with a
480x320 HDMI touch panel (Quimat MPI3508) on the console over DRM/KMS.

## How it works

- `src/slint_gl_backend.*`: a custom `mbgl::gl::RendererBackend` +
  `mbgl::gfx::Renderable` (modelled on the upstream GLFW backend) that renders
  into an FBO whose colour texture lives in Slint's GL context. GL entry points
  come from `eglGetProcAddress`; `activate()`/`deactivate()` are no-ops because
  Slint's context is already current inside the rendering-notifier callback;
  `ContextMode::Shared`.
- `src/slint_map_gl.*`: owns the `mbgl::Map` (Continuous mode) + renderer +
  frontend, pumps the run loop, and handles pointer input (drag = pan, a custom
  double-tap detector zooms in, `flyTo` for the city buttons).
- `main_gl.cpp`: on `RenderingState::RenderingSetup` it creates the
  texture/RBO/FBO; on `BeforeRendering` it renders the map into the FBO and
  publishes the texture with `create_from_borrowed_gl_2d_rgba_texture(...,
  BottomLeft)`, saving/restoring GL state around the maplibre render.
- `gl_map_window.slint`: the Pi touch layout (large buttons, a right-edge
  vertical zoom slider + zoom buttons, no pitch/bearing sliders).

## Build

Run on the build host (aarch64, e.g. a Pi 5). The first build compiles
maplibre-native from source and takes tens of minutes.

```bash
hdmi/scripts/build.sh          # clones maplibre-native-slint, overlays these
                               # sources onto cpp/, builds maplibre-slint-gl
```

It configures with the OpenGL backend (not WebGPU), Slint's FemtoVG GL renderer,
and the libseat linuxkms backend:

```
-DMLN_WITH_OPENGL=ON -DMLN_WITH_WEBGPU=OFF -DMLN_WITH_GLFW=OFF
-DSLINT_FEATURE_RENDERER_FEMTOVG=ON -DSLINT_FEATURE_BACKEND_LINUXKMS=ON
```

The target is compiled with `-fno-rtti` to match `mbgl-core` (`MLN_WITH_RTTI=OFF`).
Output: `<checkout>/build/cpp/maplibre-slint-gl`.

## Deploy

```bash
hdmi/scripts/deploy.sh <display-host>   # scp the binary + (re)start it
```

The display host needs `~/mls-libs` populated with the build-host libraries it
lacks (different Debian release -> different SONAMEs / runtime asserts):
`libslint_cpp.so`, `libcpr.so.1`, `libicu{uc,i18n,data}.so.72`,
`libpng16.so.16`. Do **not** bundle the GPU/display stack (Mesa/libEGL/libGL/
libdrm/libgbm); those must come from the target.

## Run

Unattended (survives reboot):

```bash
sudo cp hdmi/systemd/maplibre-slint-gl.service /etc/systemd/system/
sudo systemctl enable --now maplibre-slint-gl.service
```

Interactive (manual tmux session, handy for debugging / capturing video):

```bash
hdmi/scripts/run.sh             # tmux session 'mapgl'; logs to ~/map-gl.log
```

| Variable | Effect |
|---|---|
| `MAPLIBRE_STYLE_URL` | Initial style (also added to the dropdown) |
| `MAPLIBRE_WIDTH` / `MAPLIBRE_HEIGHT` | Render size (default: the display resolution) |
| `MAPLIBRE_FLY_MS` | `flyTo` duration in ms for the city buttons (default 2500) |
| `MAPLIBRE_PREFETCH_DELTA` | `Map::setPrefetchZoomDelta`: request `zoom - delta` parent tiles first so a coarse map shows during loads instead of blank pop-in (maplibre default 4; 0 disables). Affects what shows during a load, not the frame rate. |
| `MAPLIBRE_ORIENTATION_DEMO` | When `1`, sweep pitch (0..60) and bearing continuously every frame and log `[perf] N fps`. A stand-in for a future tilt/compass sensor feed; use it to gauge how the panel follows continuous camera changes. |

## Raspberry Pi runtime notes

- **DRM master**: install/enable `seatd`, add the user to the `video` group, and
  build Slint with `SLINT_FEATURE_BACKEND_LINUXKMS=ON` (libseat). seatd grants
  DRM master without an X11/Wayland session or an active VT, so this runs over
  ssh on the console. (This is the libseat `linuxkms` backend, not the
  `-noseat` software variant the SPI path uses.)
- **Display mode**: the MPI3508 only advertises standard HDMI modes (it scales
  internally to 480x320); a raw 480x320 CVT signal is rejected. Force e.g.
  `video=HDMI-A-1:720x576@50` in `cmdline.txt` for less downscaling.
- **Touch**: the XPT2046 / ADS7846 resistive controller is single-touch (no
  pinch). Enable `dtparam=spi=on` and `dtoverlay=ads7846,...`, then set a
  libinput calibration matrix via udev. Double-tap zooms in; zoom out with the
  on-screen zoom buttons / slider. Resistive panels have an edge dead zone, so
  the layout keeps controls inset.
- maplibre-native renders on the V3D GPU and Slint composites it zero-copy; the
  `flyTo` camera animation stays smooth.
