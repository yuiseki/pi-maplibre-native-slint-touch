# pi-maplibre-native-slint-touch

Raspberry Pi touchscreen builds of
[maplibre/maplibre-native-slint](https://github.com/maplibre/maplibre-native-slint),
in **two paths** depending on how the panel is wired to the Pi. Both run on a
console-only box (no X11/Wayland) and support resistive touch; pick the one that
matches your display.

| Path | Panel / connector | Renderer | Code | Extras |
|---|---|---|---|---|
| **[`spi/`](spi/README.md)** | SPI fbtft panel, legacy `/dev/fb0`, **no GPU/KMS** (e.g. Osoyoo 3.5B, ILI9488) | Slint **software** + maplibre readback | Rust | idle DVD/tile screensaver |
| **[`hdmi/`](hdmi/README.md)** | HDMI on the vc4 **KMS connector**, **V3D GPU** usable (e.g. Quimat MPI3508) | **zero-copy GL** (maplibre renders into Slint's GL texture, no readback) | C++ | animated `flyTo` |

Which one do I use?

- The panel shows up only as `/dev/fb0` (an `fb_*` fbtft device) with no DRM
  connector -> **SPI path**. There is no GPU scanout to an SPI panel, so software
  rendering is correct and optimal there.
- The panel is a real HDMI display on the vc4 connector (the V3D GPU works) ->
  **HDMI path**, which uses the GPU and composites zero-copy.

See each subdirectory's README for hardware setup (device-tree overlay, touch
calibration), build, deploy, and the systemd unit.

## Relationship to upstream

The generic, upstreamable pieces were contributed back to
maplibre/maplibre-native-slint:

- [#66](https://github.com/maplibre/maplibre-native-slint/pull/66): `linuxkms-noseat` feature, `MAPLIBRE_*` env overrides, Raspberry Pi run guide (used by the SPI path)
- [#67](https://github.com/maplibre/maplibre-native-slint/pull/67): surface `MAPLIBRE_STYLE_URL` in the style dropdown
- [#68](https://github.com/maplibre/maplibre-native-slint/pull/68): the zero-copy OpenGL example (the HDMI path's C++ app)

This repository is the hardware-specific, opinionated distribution that ties
those together with device-tree overlays, touch calibration, screensaver, and
systemd units.

## Credits

Built on [maplibre/maplibre-native-slint](https://github.com/maplibre/maplibre-native-slint)
by Yuri Astrakhan & MapLibre contributors, itself based on Murmele's
[slintmaplibretest](https://gitlab.com/Murmele/slintmaplibretest).
The bouncing-logo screensaver idea is borrowed from
[pi-z2-display-hat-mini](https://github.com/yuiseki/pi-z2-display-hat-mini).
