# pi-maplibre-native-slint-touch

A Raspberry Pi touchscreen build of [maplibre/maplibre-native-slint](https://github.com/maplibre/maplibre-native-slint)
(the Rust demo), set up to run on a **console-only Linux box over DRM/KMS with
software GL**, plus an **idle screensaver** (bouncing DVD-style logo).

This repository is intentionally **not** part of upstream — it is the
hardware-specific, opinionated distribution. The generic, upstreamable pieces
were contributed back separately:

- maplibre/maplibre-native-slint#66 — `linuxkms-noseat` feature, `MAPLIBRE_*` env overrides, Raspberry Pi run guide
- maplibre/maplibre-native-slint#67 — surface `MAPLIBRE_STYLE_URL` in the style dropdown

Verified on: build host **Raspberry Pi 5 (Debian 12 / bookworm)**, display host
**Raspberry Pi 4 (Debian 13 / trixie)** with a 480×320 SPI/HDMI LCD + ADS7846
resistive touch. Both `aarch64`. Renders MapLibre demo tiles and PMTiles.

## What's here

- `main.slint`, `src/` — the Rust + Slint app (vendored MMapView components under
  `src/ui/`, with a local `render-active` addition to pause the render loop).
- `src/screensaver.rs` — idle screensaver: a watcher thread reads the touchscreen
  evdev node (shared, non-grabbing) so activity is detected even while the map
  consumes pointer events; a timer drives the state.
- `assets/dvd-logo.png` — the bouncing logo (colorized per bounce).
- `systemd/maplibre-slint.service`, `udev/99-ads7846-calib.rules`, `scripts/`.

## Why the special handling

- The Pi's VideoCore GPU has no usable hardware GLES3 for maplibre-native, so its
  EGL context must use **Mesa software GL (llvmpipe) on the surfaceless platform**,
  otherwise it aborts with `eglInitialize() failed`.
- No X11/Wayland on a console box → Slint uses its **linuxkms** backend
  (`-noseat`, no `libseat`/logind).

## Build

```bash
cargo build --release --features linuxkms-noseat
```

(First build compiles maplibre-native C++ from source — slow.)

## Run (from the active console / VT)

```bash
SLINT_BACKEND=linuxkms-noseat \
EGL_PLATFORM=surfaceless LIBGL_ALWAYS_SOFTWARE=1 GALLIUM_DRIVER=llvmpipe \
LD_LIBRARY_PATH="$HOME/mls-libs" \
./target/release/maplibre_native_slint
```

### Environment overrides

| Variable | Effect |
|---|---|
| `MAPLIBRE_STYLE_URL` | Initial style; also prepended to the dropdown (PMTiles `pmtiles://` ok) |
| `MAPLIBRE_LAT` / `MAPLIBRE_LON` / `MAPLIBRE_ZOOM` | Initial camera |
| `MAPLIBRE_SAVER_SECS` | Idle seconds → screensaver (default 300) |
| `MAPLIBRE_OFF_SECS` | Idle seconds → black/off (default 900) |
| `MAPLIBRE_TOUCH_DEV` | Touch evdev node (default `/dev/input/event4`) |

## Screensaver

- After `MAPLIBRE_SAVER_SECS` of no touch → bouncing DVD logo (colour changes on bounce).
- After `MAPLIBRE_OFF_SECS` → black ("off"; this LCD has no backlight control).
- Any touch wakes (a full-screen overlay catches it; the evdev watcher is the backstop).
- While the screensaver is up the map render loop is paused (`render-active`),
  dropping CPU from ~50% to ~1–8%.

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
