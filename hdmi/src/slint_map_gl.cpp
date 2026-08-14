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
#include <mbgl/style/expression/dsl.hpp>
#include <mbgl/style/layers/circle_layer.hpp>
#include <mbgl/style/layers/symbol_layer.hpp>
#include <mbgl/style/property_expression.hpp>
#include <mbgl/style/sources/geojson_source.hpp>
#include <mbgl/style/style.hpp>
#include <mbgl/util/color.hpp>
#include <mbgl/util/geojson.hpp>
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
    if (const char* e = std::getenv("MAPLIBRE_DANCE_SPEED")) {
        double v = std::atof(e);
        if (v > 0.0)
            dance_speed_ = v;
    }
    if (const char* e = std::getenv("MAPLIBRE_DANCE_MAX_PITCH")) {
        double v = std::atof(e);
        if (v >= 0.0 && v <= 60.0)  // mbgl caps pitch at 60; >45 explodes tiles
            dance_max_pitch_ = v;
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
    // Initial view: Tokyo at z10 by default, overridable with
    // MAPLIBRE_CENTER="lat,lon,zoom" (boot-to-location, also handy for perf
    // measurements at different zooms).
    double clat = 35.681, clon = 139.767, czoom = 10.0;
    if (const char* e = std::getenv("MAPLIBRE_CENTER")) {
        double a, b, c;
        if (std::sscanf(e, "%lf,%lf,%lf", &a, &b, &c) == 3) {
            clat = a;
            clon = b;
            czoom = c;
        }
    }
    map->jumpTo(mbgl::CameraOptions()
                    .withCenter(mbgl::LatLng{clat, clon})
                    .withZoom(czoom));
}

namespace {
// One source+layer pair per (who, freshness) bucket. Circle paint properties
// are per-layer, so buckets are how a faded marker is expressed without
// data-driven style expressions.
struct MarkerBucket {
    const char* source_id;
    const char* layer_id;
    const char* label_layer_id;
    mbgl::Color color;
    float opacity;
};
const MarkerBucket kBuckets[] = {
    {"pi-mesh-nodes-stale", "pi-mesh-nodes-stale-circles",
     "pi-mesh-nodes-stale-labels", mbgl::Color{1.0f, 0.35f, 0.35f, 1.0f}, 0.30f},
    {"pi-mesh-nodes", "pi-mesh-nodes-circles",
     "pi-mesh-nodes-labels", mbgl::Color{1.0f, 0.35f, 0.35f, 1.0f}, 1.0f},
    {"pi-self-stale", "pi-self-stale-circles",
     "pi-self-stale-labels", mbgl::Color{0.30f, 0.55f, 1.0f, 1.0f}, 0.30f},
    {"pi-self", "pi-self-circles",
     "pi-self-labels", mbgl::Color{0.30f, 0.55f, 1.0f, 1.0f}, 1.0f},
};
constexpr size_t kBucketCount = sizeof(kBuckets) / sizeof(kBuckets[0]);

// Bucket order: node-stale, node-fresh, self-stale, self-fresh. Later buckets
// are added later, so the live own position ends up on top.
size_t bucket_of(const SlintMapGL::MeshNode& n) {
    return (n.self ? 2u : 0u) + (n.stale ? 0u : 1u);
}
}  // namespace

void SlintMapGL::set_mesh_nodes(std::vector<MeshNode> nodes) {
    mesh_nodes_ = std::move(nodes);
    mesh_dirty_ = true;
}

// Push the current node list into the style as a GeoJSON source + circle layer,
// creating them if the style does not have them yet. Switching styles throws
// them away, which is why onDidFinishLoadingStyle() re-arms mesh_dirty_.
void SlintMapGL::apply_mesh_nodes() {
    if (!map || !style_loaded)
        return;
    auto& style = map->getStyle();

    mbgl::FeatureCollection buckets[kBucketCount];
    for (const auto& n : mesh_nodes_) {
        mapbox::geojson::feature f{mapbox::geometry::point<double>{n.lon, n.lat}};
        f.properties["id"] = n.id;
        f.properties["name"] = n.name;
        buckets[bucket_of(n)].push_back(std::move(f));
    }

    for (size_t i = 0; i < kBucketCount; ++i) {
        const auto& b = kBuckets[i];
        auto* existing = style.getSource(b.source_id);
        if (!existing) {
            auto source = std::make_unique<mbgl::style::GeoJSONSource>(b.source_id);
            source->setGeoJSON(mbgl::GeoJSON{buckets[i]});
            style.addSource(std::move(source));

            auto layer = std::make_unique<mbgl::style::CircleLayer>(b.layer_id,
                                                                   b.source_id);
            layer->setCircleRadius(7.0f);
            layer->setCircleColor(b.color);
            layer->setCircleOpacity(b.opacity);
            layer->setCircleStrokeWidth(2.0f);
            layer->setCircleStrokeColor(mbgl::Color::white());
            layer->setCircleStrokeOpacity(b.opacity);
            style.addLayer(std::move(layer));          // on top of everything

            // Label above the dot. text-field reads the feature's "name", so
            // one layer covers every marker in the bucket. The halo keeps it
            // readable over both land and water; allow-overlap because a
            // handful of markers must never hide each other.
            namespace dsl = mbgl::style::expression::dsl;
            auto labels = std::make_unique<mbgl::style::SymbolLayer>(
                b.label_layer_id, b.source_id);
            labels->setTextField(
                mbgl::style::PropertyValue<mbgl::style::expression::Formatted>(
                    mbgl::style::PropertyExpression<
                        mbgl::style::expression::Formatted>(
                        dsl::format(dsl::get("name")))));
            labels->setTextFont({std::vector<std::string>{"Noto Sans Bold"}});
            labels->setTextSize(13.0f);
            labels->setTextAnchor(mbgl::style::SymbolAnchorType::Bottom);
            labels->setTextOffset(std::array<float, 2>{{0.0f, -0.9f}});
            labels->setTextAllowOverlap(true);
            labels->setTextIgnorePlacement(true);
            labels->setTextColor(b.color);
            labels->setTextOpacity(b.opacity);
            labels->setTextHaloColor(mbgl::Color::white());
            labels->setTextHaloWidth(1.5f);
            style.addLayer(std::move(labels));
            std::cout << "[SlintMapGL] marker layer created: " << b.layer_id
                      << " (+labels)" << std::endl;
        } else {
            static_cast<mbgl::style::GeoJSONSource*>(existing)
                ->setGeoJSON(mbgl::GeoJSON{buckets[i]});
        }
    }
    std::cout << "[SlintMapGL] markers: " << mesh_nodes_.size() << " ("
              << buckets[1].size() << " node, " << buckets[0].size()
              << " node-stale, " << buckets[3].size() << " self, "
              << buckets[2].size() << " self-stale)" << std::endl;
}

void SlintMapGL::render() {
    if (mesh_dirty_ && style_loaded) {
        apply_mesh_nodes();
        mesh_dirty_ = false;
    }
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
        //
        // dance_speed_ (MAPLIBRE_DANCE_SPEED, default 0.5) slows the sweep: a
        // gentler view change per frame loads fewer new tiles per frame and
        // makes the inevitable dropped frames far less noticeable. At V3D's
        // ~11ms/frame baseline the full map render grazes the 16.6ms vsync
        // budget, so a fast sweep tips frames over and stutters; a slow one
        // does not.
        const double pitch =
            (dance_max_pitch_ / 2.0) *
            (1.0 - std::cos(t * 0.8 * dance_speed_));  // 0..dance_max_pitch_, eases up
        const double bearing = std::fmod(t * 30.0 * dance_speed_, 360.0);
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

    // Liveness heartbeat -- not the frame-rate instrumentation, which is the
    // MAPLIBRE_PERF block above. Every 300 frames is ~5s at 60fps, which was
    // 25k journal lines a day (a third of this host's whole journal) to say
    // "still rendering". Once every 18000 frames (~5 min) says the same thing;
    // the fast cadence stays available while measuring.
    const uint64_t heartbeat_every = perf_log_ ? 300 : 18000;
    if ((frame_count_++ % heartbeat_every) == 0) {
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

void SlintMapGL::set_orientation(double pitch, double bearing) {
    if (!map)
        return;
    map->jumpTo(mbgl::CameraOptions().withPitch(pitch).withBearing(bearing));
    map->triggerRepaint();
    repaint = true;
}

void SlintMapGL::set_sync(bool use_center, double lat, double lon,
                          bool use_orient, double pitch, double bearing) {
    if (!map)
        return;
    auto opts = mbgl::CameraOptions();
    if (use_center)
        opts = opts.withCenter(mbgl::LatLng{lat, lon});
    if (use_orient)
        opts = opts.withPitch(pitch).withBearing(bearing);
    map->jumpTo(opts);
    map->triggerRepaint();
    repaint = true;
}

void SlintMapGL::set_dance(bool on) {
    demo_orientation_ = on;
    std::cout << "[SlintMapGL] dance=" << (on ? "on" : "off") << std::endl;
    if (on) {
        // Restart the sweep phase so pitch eases up from the current (flat) view.
        // The dance keeps whatever style is currently selected -- pick the
        // "OSM NoLabel" style for a smooth 60fps dance, or a label-heavy one to
        // see the FPS cost of per-frame label re-projection.
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
    // A new style has no idea about our source/layer; put them back.
    mesh_dirty_ = true;
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
