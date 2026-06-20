# pi-maplibre-native-slint-touch

A Raspberry Pi touchscreen build of [maplibre/maplibre-native-slint](https://github.com/maplibre/maplibre-native-slint)
(the Rust demo), set up to run on a **console-only Linux box that renders to a
legacy framebuffer (`/dev/fb0`) with Slint's software renderer**, plus an
**idle screensaver** (bouncing DVD-style logo, with a staged "hidden mode").

This repository is intentionally **not** part of upstream. It is the
hardware-specific, opinionated distribution. The generic, upstreamable pieces
were contributed back separately:

- maplibre/maplibre-native-slint#66 — `linuxkms-noseat` feature, `MAPLIBRE_*` env overrides, Raspberry Pi run guide
- maplibre/maplibre-native-slint#67 — surface `MAPLIBRE_STYLE_URL` in the style dropdown

Verified on: build host **Raspberry Pi 5 (Debian 12 / bookworm)**, display host
**Raspberry Pi 4 (Debian 13 / trixie)** driving a **480x320 ILI9486 SPI TFT**
(the `fb_ili9486` fbtft staging driver, exposed only as `/dev/fb0`) with an
ADS7846 resistive touch panel. Both `aarch64`. Renders MapLibre demo tiles and PMTiles.

## What's here

- `main.slint`, `src/` — the Rust + Slint app (vendored MMapView components under
  `src/ui/`, with a local `render-active` addition to pause the render loop).
- `src/screensaver.rs` — idle screensaver: a watcher thread reads the touchscreen
  evdev node (shared, non-grabbing) so activity is detected even while the map
  consumes pointer events; a timer drives the state.
- `assets/dvd-logo.png` — the bouncing logo (colorized per bounce).
- `systemd/maplibre-slint.service`, `udev/99-ads7846-calib.rules`, `scripts/`.

## Display stack: why this is software-only (and why that is correct here)

This was investigated end to end. The short version: **on this panel there is no
GPU path to the screen at all, by hardware design.** Two separate "GL" concerns
are involved and it helps to keep them apart.

### 1. MapLibre's offscreen GL context (the map pixels)

maplibre-native renders the map with its own OpenGL context into an offscreen
buffer (`HeadlessFrontend`), and the app reads those pixels back into a Slint
`SharedPixelBuffer`. The Pi's VideoCore has no usable hardware GLES3 for
maplibre-native, so this context must use **Mesa software GL (llvmpipe) on the
surfaceless EGL platform**:

```
EGL_PLATFORM=surfaceless LIBGL_ALWAYS_SOFTWARE=1 GALLIUM_DRIVER=llvmpipe
```

Without this, `eglInitialize()` fails and the app aborts.

### 2. Slint's on-screen renderer (getting pixels onto the LCD)

Slint runs on the **linuxkms** backend (`-noseat`, no `libseat`/logind, because a
console box has no seat manager). Its renderer selection tries
`Skia -> FemtoVG -> FemtoVG-wgpu -> Software` and lands on **Software**, which on
this box draws to **`/dev/fb0`** via Slint's `LinuxFBDisplay` path. That is what
you see in the logs:

```
Using Software renderer
Rendering at 480x320
```

### Why none of the GPU renderers can be used here

The 480x320 LCD is an **ILI9486 SPI panel driven by the `fbtft` staging driver**.
It is a **legacy framebuffer only** (`/dev/fb0`, name `fb_ili9486`). It is **not a
DRM/KMS device**: it has no DRM connector and no CRTC.

The actual DRM/KMS cards on the Pi 4 are:

| Node | Driver | Role |
|---|---|---|
| `card0` | `v3d` | render-only (no display output) |
| `card1` | `vc4` | HDMI (KMS), but **both HDMI connectors are disconnected** here |

Slint's GPU renderers (FemtoVG-GL, FemtoVG-wgpu, Skia/Vulkan) all go through
**GBM -> DRM-KMS**, which requires a KMS card with a **connected connector**.
This box has none (the only display is the SPI fbtft panel, which is not a KMS
connector). So every GPU renderer fails at device/connector setup and the backend
falls back to the software renderer. Only the software renderer can target a
legacy fbdev panel.

What was ruled out along the way (so nobody re-investigates these):

- It is **not wgpu-specific**: plain FemtoVG-GL fails at the exact same point as FemtoVG-wgpu.
- It is **not the v3d/Vulkan driver**: `drm_info` and `modetest -M vc4` work fine; the Broadcom Vulkan ICD is healthy.
- It is **not DRM-master/permission related**: `modetest` acquires DRM master fine; the misleading
  `Error reading DRM resource handles: Permission denied` is just the device scan hitting the v3d
  render-only node. The real reason a GPU renderer cannot start is "no connected KMS connector".

### Performance note

Do not expect a speedup from "going GPU" or zero-copy here. **There is no GPU
scanout path to an SPI panel.** Whatever renders the frame, the final pixels must
be pushed to the panel by the CPU over SPI (the fbtft transfer). The current
design (software rendering + a pixel readback from maplibre) is the appropriate
and effectively optimal approach for this hardware. GPU acceleration would only
help if you (a) attach an HDMI display to the vc4 connector, or (b) replace fbtft
with a DRM/KMS panel driver (tinydrm / `panel-mipi-dbi`); even in case (b) the SPI
transfer stays CPU-bound, so the win is marginal.

## Build

```bash
cargo build --release --features linuxkms-noseat
```

(First build compiles maplibre-native C++ from source. Slow.)

## Run (from the console / VT)

```bash
SLINT_BACKEND=linuxkms-noseat \
EGL_PLATFORM=surfaceless LIBGL_ALWAYS_SOFTWARE=1 GALLIUM_DRIVER=llvmpipe \
LD_LIBRARY_PATH="$HOME/mls-libs" \
./target/release/maplibre_native_slint
```

Note: `SLINT_BACKEND=linuxkms-noseat` currently logs
`unrecognized renderer noseat, falling back default`. This is harmless here (the
only compiled backend is the noseat linuxkms backend, so the fallback picks it
anyway); Slint parses the value as `backend-renderer` and treats `noseat` as a
renderer name.

### Environment overrides

| Variable | Effect |
|---|---|
| `MAPLIBRE_STYLE_URL` | Initial style; also prepended to the dropdown (PMTiles `pmtiles://` ok) |
| `MAPLIBRE_LAT` / `MAPLIBRE_LON` / `MAPLIBRE_ZOOM` | Initial camera |
| `MAPLIBRE_SAVER_SECS` | Idle seconds → DVD screensaver (default 300 = 5 min) |
| `MAPLIBRE_DVD_SECS` | Seconds of DVD before switching to map tiles (default 1800 = 30 min) |
| `MAPLIBRE_TILE_CHANGE_SECS` | New random region/style every N seconds (default 900 = 15 min) |
| `MAPLIBRE_TILE_LOAD_SECS` | Render window to load each tile before capture (default 15) |
| `MAPLIBRE_OFF_SECS` | Idle seconds → black/off (default 43200 = 12 h) |
| `MAPLIBRE_TOUCH_DEV` | Touch evdev node (default `/dev/input/event4`) |

## Screensaver (staged "hidden mode")

Idle time drives the stages:

1. **0 to 5 min**: normal interactive map.
2. **5 to 35 min**: bouncing DVD logo (colour changes on each bounce).
3. **35 min to 12 h**: bouncing **map tile** — a random region at a random style,
   re-picked every 15 min. Each pick renders the map for ~15 s, crops its centre
   to the logo size and bounces that still image (cheap after capture).
   - Styles: `osm-bright-ja`, `maptiler-basic-ja`, `osm-fiord`.
   - Regions: Paris, New York, Tokyo, Hiroshima (extend `REGIONS` in `src/screensaver.rs`).
4. **12 h+**: black ("off"; this LCD has no backlight control).

Any touch wakes and restores the user's pre-screensaver style + camera (a
full-screen overlay catches the touch; the evdev watcher is the backstop).
The map render loop is paused (`render-active`) except briefly while a tile is
captured, so idle CPU stays low (~1 to 8 % vs ~50 % live).

## Touch calibration

The ADS7846 reports raw ADC coordinates; install `udev/99-ads7846-calib.rules`
to `/etc/udev/rules.d/`, then `sudo udevadm control --reload && sudo udevadm trigger -s input`
and restart the app. Re-derive the matrix per panel by tapping known points.

## Cross-distro libraries

When the build host OS differs from the display host OS, bundle the build host's
versioned libs (ICU/libpng/libuv) — see `scripts/bundle-libs.sh <display-host>`.
Do **not** bundle the GPU/display stack.

## systemd

```bash
scripts/bundle-libs.sh <display-host>      # one-time, from the build host
scripts/deploy.sh <display-host>           # build + copy binary + restart
sudo loginctl enable-linger yuiseki        # so /run/user/1000 exists at boot
sudo cp systemd/maplibre-slint.service /etc/systemd/system/
sudo systemctl enable --now maplibre-slint.service
```

## Credits

Built on [maplibre/maplibre-native-slint](https://github.com/maplibre/maplibre-native-slint)
by Yuri Astrakhan & MapLibre contributors, itself based on Murmele's
[slintmaplibretest](https://gitlab.com/Murmele/slintmaplibretest).
The bouncing-logo idea is borrowed from
[pi-z2-display-hat-mini](https://github.com/yuiseki/pi-z2-display-hat-mini).
