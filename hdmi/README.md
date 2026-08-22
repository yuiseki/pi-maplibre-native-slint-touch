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
  vertical zoom slider + zoom buttons, no pitch/bearing sliders), plus a
  full-width status bar along the top: GPS satellite icon and fix count on the
  left, the wall clock (`HH:MM`) centred, and Wi-Fi/SSID + battery on the
  right. Everything in the bar is fed from `main_gl.cpp` and hides itself when
  its source is absent, so a machine without GPS or a battery just shows less.

## Build

Run on the build host (aarch64, e.g. a Pi 5). The first build compiles
maplibre-native from source and takes tens of minutes.

On a minimal install, the build host needs these first:

```bash
sudo apt install build-essential cmake ninja-build pkg-config git curl \
  libegl-dev libgles-dev libgl-dev libopengl-dev libgbm-dev libdrm-dev \
  libinput-dev libxkbcommon-dev libudev-dev libseat-dev seatd \
  libssl-dev libcurl4-openssl-dev zlib1g-dev libpng-dev libicu-dev \
  libx11-dev libxext-dev mesa-common-dev
curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh   # Slint needs Rust
```

The X11 packages and `libopengl-dev` are needed even though nothing here uses
X11: maplibre-native links `mbgl-core` against `OpenGL::GLX`, and CMake's
`FindOpenGL` only defines that target in GLVND mode (which is why `build.sh`
passes `-DOpenGL_GL_PREFERENCE=GLVND`). Without them the generate step fails
with `Target "mbgl-core" links to: OpenGL::GLX but the target was not found`.
The confusing part is that CMake still prints `found components: GLX` while
defining no such target; a good configure says `Found OpenGL: .../libOpenGL.so`.

```bash
hdmi/scripts/build.sh          # clones maplibre-native-slint, overlays these
                               # sources onto cpp/, builds maplibre-slint-gl
```

The app was upstreamed as PR #68 and the branch it lived on was deleted, so
there is no branch to track: `build.sh` pins `REF` to the merge commit
(`1f32a5a`). An existing checkout is used as-is -- it carries the overlaid
sources as local modifications by design -- and only moved onto `REF` when
`SYNC=1` is passed.

Upstream has moved on since: #73 extracted the backend into a reusable
`mbgl-slint` library, and #70/#72/#75/#76 reworked the build. Every file this
directory overlays was touched. Following upstream is therefore a porting job,
not a version bump; `REF=main SYNC=1 hdmi/scripts/build.sh` is where that work
would start, and it will not build unchanged.

An existing `$WORK/build` is reused rather than re-configured. At this ref
Slint is fetched from the moving `release/1` branch, so a re-configure makes
CMake rebase the local Slint checkout onto today's upstream and fail in merge
conflicts -- which then breaks plain `cmake --build` too, since ninja
regenerates through the same failing configure. The configure that does run
passes `FETCHCONTENT_UPDATES_DISCONNECTED=ON` for the same reason. Pass
`RECONFIGURE=1` when a CMake flag genuinely has to change.

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
| `MAPLIBRE_FLY_MS` | `flyTo` duration in ms for the city buttons (default 6000). Long on purpose: a fast flyTo outruns tile loading on V3D so almost nothing renders mid-flight; a slow one keeps tiles in view. |
| `MAPLIBRE_PREFETCH_DELTA` | `Map::setPrefetchZoomDelta`: request `zoom - delta` parent tiles first so a coarse map shows during loads instead of blank pop-in (maplibre default 4; 0 disables). Affects what shows during a load, not the frame rate. |
| `MAPLIBRE_ORIENTATION_DEMO` | When `1`, sweep pitch (0..45) and bearing continuously every frame and log `[perf] N fps`. A stand-in for a future tilt/compass sensor feed; use it to gauge how the panel follows continuous camera changes. |
| `MAPLIBRE_DANCE_SPEED` | Dance/orientation sweep rate factor (default `0.5`). The full map re-renders every frame (~11ms on V3D, near the 16.6ms vsync budget), so a slower sweep keeps frames under budget and makes any drops far less noticeable. Lower = smoother/slower. |
| `MAPLIBRE_SAVER_SECS` | Idle seconds before the DVD-logo stage (default 300) |
| `MAPLIBRE_DVD_SECS` | Extra idle seconds before the map-tile stage (default 1800) |
| `MAPLIBRE_OFF_AC_SECS` / `MAPLIBRE_OFF_BATT_SECS` | Idle seconds before the screen goes black, on AC vs battery (default 43200 / 1800; PiSugar-aware) |
| `MAPLIBRE_TILE_DIR` | Directory of pre-rendered map-tile PNGs (default `~/screensaver-tiles`) |
| `MAPLIBRE_DVD_LOGO` | DVD logo PNG path (default `~/dvd-logo.png`) |
| `MAPLIBRE_SELFTEST` | When `1`, freeze the bounce and log per-stage pixel-readback assertions (TDD; see docs) |
| `MAPLIBRE_INPUT_DEVS` | Comma-separated `/dev/input/event*` paths the idle watcher treats as wake input (default: all). **Set this to the touchscreen only** so the USB-mic HID keyboard / HDMI kbd nodes can't spuriously wake the screensaver (touch-to-wake). e.g. `/dev/input/by-path/platform-fe204000.spi-cs-1-event` |

## Screensaver

A staged idle screensaver (kept out of upstream): live map -> bouncing
recoloured **DVD logo** (5 min idle) -> bouncing **map tile** (+30 min) -> off /
black (PiSugar-aware: 12 h on AC, 30 min on battery). Any input wakes it.

There are a couple of FemtoVG-GL/V3D gotchas (full story in
**`docs/hdmi-gl-rendering-notes.md`** — PNGs *do* render here; earlier claims to
the contrary were webcam-glare misreads). Practical upshot:

- The **DVD logo** art is black-on-transparent, and Slint `colorize` multiplies
  it back to black, so C++ decodes the PNG and paints its alpha shape in the
  bounce colour into a `SharedPixelBuffer` (`~/dvd-logo.png`).
- The **map tiles** are pre-baked PNGs (baked offline with `mbgl-render` for
  varied regions/styles). Generate them on the build host; `deploy.sh` ships them:

  ```bash
  hdmi/scripts/gen-screensaver-tiles.sh   # mbgl-render under xvfb -> ~/screensaver-tiles/*.png
  hdmi/scripts/deploy.sh <display-host>   # copies binary + dvd-logo.png + tiles
  ```

  Verify rendering precisely (no webcam guesswork) with
  `MAPLIBRE_SELFTEST=1 MAPLIBRE_SAVER_SECS=4 MAPLIBRE_DVD_SECS=8 …` and read the
  `[selftest]` lines.

## Input: touch, mouse, keyboard + the map/terminal switch

The map is operable three ways: the resistive **touch** panel, a **mouse** (a USB
or 2.4 GHz dongle pointer drives the Slint UI cursor via libinput), and a
**keyboard** for the terminal switch below.

`supervisor.py` (run by the systemd unit instead of the binary directly) toggles
between the map and a native console, the way the Display HAT Mini build
([pi-z2-display-hat-mini](https://github.com/yuiseki/pi-z2-display-hat-mini))
does, but here the "terminal" is simply the Linux console on `tty1`:

- **MAP** (default): the supervisor runs `maplibre-slint-gl` (it holds the HDMI
  via seatd/libseat).
- **Ctrl+C twice within 1.5 s** on the keyboard is **context-sensitive**:
  - while the **screensaver is up** -> **WAKE** the live map (the supervisor sees
    `/dev/shm/pi-saver-stage >= 1` and does *not* drop to the console; the map
    binary's own Ctrl+C x2 watcher resets its idle clock to wake);
  - while the **live map is shown** -> the supervisor stops the map and releasing
    DRM lets the `tty1` console (fbcon) reappear on the HDMI = **TERMINAL**.
- In that shell, run **`pi-maps`** (or `pi-map`) -> the supervisor restarts the map.

The supervisor reads keyboards directly (raw `input_event`, no `python3-evdev`):
USB via `/dev/input/by-id/*-event-kbd`, plus any device whose
`/proc/bus/input/devices` Handlers include `kbd` (Bluetooth/BLE keyboards, which
have no by-id symlink). Slint/libinput does not grab them exclusively, so both
the supervisor and the map binary see the keys. **Wake** lives in the map binary
(it auto-discovers keyboards by `KEY_C` capability, so the USB-mic HID and HDMI
nodes are skipped) and **terminal** lives in the supervisor — gated by the
screensaver stage so the two never collide.

Touch is the only thing that wakes the screensaver by default (set
`MAPLIBRE_INPUT_DEVS` to the touchscreen node); Ctrl+C x2 is the deliberate
keyboard alternative.

Install (in addition to the binary + `~/mls-libs`):

```bash
cp hdmi/supervisor.py ~/pi-display-supervisor.py
sudo cp hdmi/bin/pi-map hdmi/bin/pi-maps /usr/local/bin/ && sudo chmod +x /usr/local/bin/pi-map*
# autologin so Ctrl+C x2 drops straight into a shell (no login prompt):
sudo mkdir -p /etc/systemd/system/getty@tty1.service.d
sudo cp hdmi/systemd/getty@tty1-autologin.conf /etc/systemd/system/getty@tty1.service.d/autologin.conf
sudo cp hdmi/systemd/maplibre-slint-gl.service /etc/systemd/system/
sudo systemctl daemon-reload && sudo systemctl restart getty@tty1 maplibre-slint-gl.service
```

A small console font helps on the downscaled panel (720x576 -> 480x320):
`FONTFACE="TerminusBold"` / `FONTSIZE="32x16"` in `/etc/default/console-setup`.

## Raspberry Pi runtime notes

- **DRM master**: install/enable `seatd`, add the user to the `video` group, and
  build Slint with `SLINT_FEATURE_BACKEND_LINUXKMS=ON` (libseat). seatd grants
  DRM master without an X11/Wayland session or an active VT, so this runs over
  ssh on the console. (This is the libseat `linuxkms` backend, not the
  `-noseat` software variant the SPI path uses.)
- **Display mode**: these 3.5" panels only advertise standard HDMI modes (they
  scale internally to 480x320); a raw 480x320 CVT signal is rejected. Force a
  mode in `cmdline.txt` for less downscaling. On the Osoyoo 3.5" HDMI V2.0,
  `video=HDMI-A-1:720x480@60` is the one to use: 720x480 is 3:2, the same
  aspect as the panel, so nothing is squashed. 720x576 is 5:4 and stretches
  the image vertically.
- **Touch**: the XPT2046 / ADS7846 resistive controller is single-touch (no
  pinch). Enable `dtparam=spi=on` and `dtoverlay=ads7846,...`, then set a
  libinput calibration matrix via udev. The `by-path` name carries the SoC's
  SPI address, so it differs per board: `platform-fe204000.spi-cs-1-event` on a
  Pi 4, `platform-1f00050000.spi-cs-1-event` on a Pi 5. Check yours before
  setting `MAPLIBRE_INPUT_DEVS`. Double-tap zooms in; zoom out with the
  on-screen zoom buttons / slider. Resistive panels have an edge dead zone, so
  the layout keeps controls inset.
- maplibre-native renders on the V3D GPU and Slint composites it zero-copy; the
  `flyTo` camera animation stays smooth.
