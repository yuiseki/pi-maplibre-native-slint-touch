#include "slint_map_gl.hpp"

#include <algorithm>
#include <chrono>
#include <cmath>
#include <cstdio>
#include <cstdlib>
#include <iostream>
#include <mbgl/map/camera.hpp>
#include <mbgl/map/map_options.hpp>
#include <mbgl/renderer/renderer.hpp>
#include <mbgl/storage/resource_options.hpp>
#include <mbgl/style/style.hpp>
#include <mbgl/util/chrono.hpp>
#include <mbgl/util/geo.hpp>

SlintMapGL::~SlintMapGL() {
    // Orderly shutdown: detach observer, then drop map before frontend/backend.
    if (frontend) {
        frontend->setObserver(noop_observer);
    }
    map.reset();
    frontend.reset();
    backend.reset();
    run_loop.reset();
}

void SlintMapGL::setup(uint32_t fbo, int w, int h,
                       const std::string& styleUrl) {
    if (!run_loop) {
        run_loop = std::make_unique<mbgl::util::RunLoop>();
    }

    backend = std::make_unique<SlintGLBackend>(
        mbgl::Size{static_cast<uint32_t>(w), static_cast<uint32_t>(h)});
    backend->setFbo(fbo);

    auto renderer = std::make_unique<mbgl::Renderer>(*backend, 1.0f);
    frontend = std::make_unique<SlintGLFrontend>(std::move(renderer), *backend);

    observer =
        std::make_unique<SlintGLRendererObserver>([this]() { repaint = true; });
    frontend->setObserver(*observer);

    mbgl::ResourceOptions ro;
    ro.withCachePath("cache.sqlite").withAssetPath(".");

    map = std::make_unique<mbgl::Map>(
        *frontend, *this,
        mbgl::MapOptions()
            .withMapMode(mbgl::MapMode::Continuous)
            .withSize({static_cast<uint32_t>(w), static_cast<uint32_t>(h)})
            .withPixelRatio(1.0f),
        ro);

    std::cout << "[SlintMapGL] setup fbo=" << fbo << " size=" << w << "x" << h
              << " style=" << styleUrl << std::endl;

    if (const char* e = std::getenv("MAPLIBRE_FLY_MS")) {
        int v = std::atoi(e);
        if (v > 0)
            fly_ms_ = v;
    }

    if (const char* e = std::getenv("MAPLIBRE_ORIENTATION_DEMO")) {
        demo_orientation_ = (std::atoi(e) != 0);
    }
    if (const char* e = std::getenv("MAPLIBRE_PERF")) {
        perf_log_ = (std::atoi(e) != 0);
    }
    demo_start_ = std::chrono::steady_clock::now();
    fps_last_ = demo_start_;

    // Tile prefetching: request parent (zoom - delta) tiles first so a coarse
    // full map shows immediately during flyTo / continuous pitch+bearing moves,
    // instead of blank pop-in. maplibre's default delta is 4; override with
    // MAPLIBRE_PREFETCH_DELTA (0 disables).
    if (const char* e = std::getenv("MAPLIBRE_PREFETCH_DELTA")) {
        int d = std::atoi(e);
        if (d >= 0 && d <= 255)
            map->setPrefetchZoomDelta(static_cast<uint8_t>(d));
    }
    std::cout << "[SlintMapGL] prefetchZoomDelta="
              << static_cast<int>(map->getPrefetchZoomDelta()) << std::endl;

    style_url_ = styleUrl;
    map->getStyle().loadURL(styleUrl);
    map->jumpTo(mbgl::CameraOptions()
                    .withCenter(mbgl::LatLng{35.681, 139.767})
                    .withZoom(10.0));
}

void SlintMapGL::render() {
    using msd = std::chrono::duration<double, std::milli>;
    const auto f0 = std::chrono::steady_clock::now();

    if (run_loop) {
        run_loop->runOnce();
    }
    const auto f1 = std::chrono::steady_clock::now();

    // Orientation perf demo: drive pitch + bearing every frame, the way a
    // tilt/compass sensor feed eventually will, to measure how fast the panel
    // can follow continuous camera changes.
    if (demo_orientation_ && map) {
        const double t =
            std::chrono::duration<double>(
                std::chrono::steady_clock::now() - demo_start_)
                .count();
        // Cap pitch at 45 (not 60): beyond ~45 the frustum reaches far toward
        // the horizon and the visible tile count explodes, which is what spikes
        // V3D render time. 45 keeps the dance lively but much smoother.
        const double pitch = 22.5 * (1.0 - std::cos(t * 0.8));  // 0..45, eases up
        const double bearing = std::fmod(t * 30.0, 360.0);      // 12s / turn
        map->jumpTo(mbgl::CameraOptions().withPitch(pitch).withBearing(bearing));
        map->triggerRepaint();
        repaint = true;
    }
    const auto f2 = std::chrono::steady_clock::now();

    // NOTE: render-on-demand (skipping this when idle) is NOT viable here. V3D is
    // a tiled GPU and does not preserve the FBO colour texture across frames when
    // it is not re-rendered (the attachment is treated as transient and
    // discarded), so skipping the render makes the borrowed texture go white when
    // the camera is static. The texture must be re-rendered every frame.
    if (frontend) {
        frontend->render();
    }
    const auto f3 = std::chrono::steady_clock::now();

    ++fps_frames_;

    if (perf_log_) {
        const double t_rl = msd(f1 - f0).count();   // run_loop (tile processing)
        const double t_rn = msd(f3 - f2).count();   // frontend->render (V3D GPU)
        const double t_frame =
            (last_frame_.time_since_epoch().count() == 0)
                ? 0.0
                : msd(f0 - last_frame_).count();     // wall interval between frames
        last_frame_ = f0;

        acc_rl_ms_ += t_rl;
        acc_rn_ms_ += t_rn;
        if (t_rl > max_rl_ms_) max_rl_ms_ = t_rl;
        if (t_rn > max_rn_ms_) max_rn_ms_ = t_rn;
        if (t_frame > 0.0) {
            acc_frame_ms_ += t_frame;
            if (t_frame > max_frame_ms_) max_frame_ms_ = t_frame;
            if (t_frame > 33.0) {  // slower than ~30fps: a visible stutter frame
                ++slow_frames_;
                slow_frame_ms_ += t_frame;
                slow_rl_ms_ += t_rl;
                slow_rn_ms_ += t_rn;
            }
        }
    }

    const auto now = std::chrono::steady_clock::now();
    const double dt = std::chrono::duration<double>(now - fps_last_).count();
    if (dt >= 2.0) {
        if (perf_log_ && fps_frames_ > 0) {
            const int n = fps_frames_;
            std::printf(
                "[perf] %.1f fps | frame avg %.1f max %.1f | runloop avg %.2f "
                "max %.2f | render avg %.2f max %.2f | slow>33ms %d",
                n / dt, acc_frame_ms_ / n, max_frame_ms_, acc_rl_ms_ / n,
                max_rl_ms_, acc_rn_ms_ / n, max_rn_ms_, slow_frames_);
            if (slow_frames_ > 0) {
                // Per slow frame: which segment dominated the overrun?
                // "other" = Slint UI compositing + present + vsync wait.
                std::printf(
                    " [slow avg ms: frame %.1f = runloop %.2f + render %.2f + "
                    "other %.2f]",
                    slow_frame_ms_ / slow_frames_, slow_rl_ms_ / slow_frames_,
                    slow_rn_ms_ / slow_frames_,
                    (slow_frame_ms_ - slow_rl_ms_ - slow_rn_ms_) / slow_frames_);
            }
            std::printf("%s\n", demo_orientation_ ? " (sweep)" : "");
            std::fflush(stdout);
            acc_frame_ms_ = acc_rl_ms_ = acc_rn_ms_ = 0.0;
            max_frame_ms_ = max_rl_ms_ = max_rn_ms_ = 0.0;
            slow_frame_ms_ = slow_rl_ms_ = slow_rn_ms_ = 0.0;
            slow_frames_ = 0;
        } else if (!perf_log_) {
            std::cout << "[perf] " << (fps_frames_ / dt) << " fps"
                      << (demo_orientation_ ? " (pitch+bearing sweep)" : "")
                      << std::endl;
        }
        fps_frames_ = 0;
        fps_last_ = now;
    }

    if ((frame_count_++ % 300) == 0) {
        std::cout << "[SlintMapGL] render frame=" << frame_count_
                  << " style_loaded=" << style_loaded.load() << std::endl;
    }
}

// --- Pointer / touch interaction ---
void SlintMapGL::handle_mouse_press(float x, float y) {
    // Detect a double-tap (two quick taps close together) ourselves, since
    // touchscreens do not reliably produce Slint's double-clicked event.
    // Kernel event timestamps are accurate, so this is robust to input lag.
    auto now = std::chrono::steady_clock::now();
    double dt =
        std::chrono::duration<double, std::milli>(now - last_tap_).count();
    double dist = std::hypot(x - last_tap_x_, y - last_tap_y_);
    if (dt < 350.0 && dist < 30.0) {
        last_tap_ = {};  // reset so a third tap does not re-trigger
        handle_double_click(x, y, false);
        return;
    }
    last_tap_ = now;
    last_tap_x_ = x;
    last_tap_y_ = y;
    last_pos = {x, y};
}

void SlintMapGL::handle_mouse_release() {
}

void SlintMapGL::handle_mouse_move(float x, float y, bool pressed) {
    if (!pressed || !map)
        return;
    mbgl::Point<double> cur{x, y};
    map->moveBy(cur - last_pos);
    last_pos = cur;
    map->triggerRepaint();
    repaint = true;
}

void SlintMapGL::handle_pan(float dx, float dy) {
    if (!map) return;
    map->moveBy(mbgl::ScreenCoordinate{static_cast<double>(dx),
                                       static_cast<double>(dy)});
    map->triggerRepaint();
    repaint = true;
}

void SlintMapGL::handle_wheel_zoom(float x, float y, float dy) {
    if (!map)
        return;
    constexpr double step = 1.2;
    double scale = (dy < 0.0) ? step : (1.0 / step);
    map->scaleBy(scale, mbgl::ScreenCoordinate{x, y});
    map->triggerRepaint();
    repaint = true;
}

void SlintMapGL::handle_double_click(float x, float y, bool shift) {
    if (!map)
        return;
    const mbgl::LatLng ll = map->latLngForPixel(mbgl::ScreenCoordinate{x, y});
    const auto cam = map->getCameraOptions();
    double z = cam.zoom.value_or(0.0) + (shift ? -1.0 : 1.0);
    z = std::min(max_zoom_, std::max(min_zoom_, z));
    map->jumpTo(mbgl::CameraOptions().withCenter(ll).withZoom(z));
    map->triggerRepaint();
    repaint = true;
}

// --- Toolbar commands ---
void SlintMapGL::setStyleUrl(const std::string& url) {
    if (map) {
        std::cout << "[SlintMapGL] style change: " << url << std::endl;
        style_url_ = url;
        map->getStyle().loadURL(url);
        repaint = true;
    }
}

void SlintMapGL::jump_to(double lat, double lon, double zoom) {
    if (!map)
        return;
    map->jumpTo(mbgl::CameraOptions()
                    .withCenter(mbgl::LatLng{lat, lon})
                    .withZoom(zoom));
    map->triggerRepaint();
    repaint = true;
}

void SlintMapGL::get_center_zoom(double& lat, double& lon, double& zoom) const {
    lat = lon = 0.0;
    zoom = 10.0;
    if (!map)
        return;
    const auto cam = map->getCameraOptions();
    if (cam.center) {
        lat = cam.center->latitude();
        lon = cam.center->longitude();
    }
    zoom = cam.zoom.value_or(10.0);
}

void SlintMapGL::fly_to(double lat, double lon, double zoom) {
    if (!map)
        return;
    mbgl::AnimationOptions anim;
    anim.duration = mbgl::Duration(std::chrono::milliseconds(fly_ms_));
    map->flyTo(
        mbgl::CameraOptions().withCenter(mbgl::LatLng{lat, lon}).withZoom(zoom),
        anim);
    map->triggerRepaint();
    repaint = true;
}

void SlintMapGL::set_zoom(double zoom) {
    if (!map)
        return;
    map->jumpTo(mbgl::CameraOptions().withZoom(zoom));
    map->triggerRepaint();
    repaint = true;
}

void SlintMapGL::set_pitch(double pitch) {
    if (!map)
        return;
    map->jumpTo(mbgl::CameraOptions().withPitch(pitch));
    map->triggerRepaint();
    repaint = true;
}

void SlintMapGL::set_bearing(double bearing) {
    if (!map)
        return;
    map->jumpTo(mbgl::CameraOptions().withBearing(bearing));
    map->triggerRepaint();
    repaint = true;
}

void SlintMapGL::set_dance(bool on) {
    demo_orientation_ = on;
    std::cout << "[SlintMapGL] dance=" << (on ? "on" : "off") << std::endl;
    if (on) {
        // Restart the sweep phase so pitch eases up from the current (flat) view.
        demo_start_ = std::chrono::steady_clock::now();
    } else if (map) {
        // Calm: reset tilt + rotation, keep the current center and zoom.
        map->jumpTo(mbgl::CameraOptions().withPitch(0.0).withBearing(0.0));
        map->triggerRepaint();
        repaint = true;
    }
}

void SlintMapGL::onWillStartLoadingMap() {
    std::cout << "[MapObserver] Will start loading map" << std::endl;
    style_loaded = false;
    map_idle = false;
}

void SlintMapGL::onDidFinishLoadingStyle() {
    std::cout << "[MapObserver] Did finish loading style" << std::endl;
    style_loaded = true;
}

void SlintMapGL::onDidBecomeIdle() {
    std::cout << "[MapObserver] Did become idle" << std::endl;
    map_idle = true;
}

void SlintMapGL::onDidFailLoadingMap(mbgl::MapLoadError error,
                                     const std::string& what) {
    std::cout << "[MapObserver] FAILED loading map. type="
              << static_cast<int>(error) << " what=" << what << std::endl;
    if (!fallback_style_applied && map) {
        fallback_style_applied = true;
        std::cout << "[MapObserver] Applying fallback local JSON style"
                  << std::endl;
        const std::string fallback_json = R"JSON({
            "version": 8,
            "name": "solid-background",
            "sources": {},
            "layers": [
                {"id": "background", "type": "background",
                 "paint": {"background-color": "rgb(255, 0, 0)",
                            "background-opacity": 1.0}}]
        })JSON";
        map->getStyle().loadJSON(fallback_json);
    }
}

void SlintMapGL::onCameraDidChange(CameraChangeMode) {
    repaint = true;
}

void SlintMapGL::onSourceChanged(mbgl::style::Source&) {
    repaint = true;
}

void SlintMapGL::onDidFinishRenderingFrame(const RenderFrameStatus& status) {
    if (status.needsRepaint)
        repaint = true;
}
