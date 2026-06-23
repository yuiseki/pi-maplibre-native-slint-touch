#include <GLES3/gl3.h>
#include <algorithm>
#include <atomic>
#include <chrono>
#include <cmath>
#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <dirent.h>
#include <fcntl.h>
#include <iostream>
#include <memory>
#include <poll.h>
#include <slint.h>
#include <string>
#include <thread>
#include <unistd.h>
#include <utility>
#include <vector>

#include <fstream>
#include <mbgl/util/image.hpp>

#include "gl_map_window.h"
#include "slint_map_gl.hpp"

// DVD logo dimensions (display + alpha-mask size).
static const int DVD_W = 140, DVD_H = 84;

// Decode the DVD-logo PNG into a downscaled alpha mask (the logo shape). In
// this zero-copy GL build Slint's own PNG path (@image-url / load_from_path)
// does not render and the raw PNG is black-on-transparent (invisible on the
// black field), so we keep just the shape and tint it ourselves into an opaque
// SharedPixelBuffer (which renders) at the current bounce colour.
static std::vector<uint8_t> load_dvd_mask(const std::string& path) {
    std::vector<uint8_t> mask((size_t)DVD_W * DVD_H, 0);
    std::ifstream f(path, std::ios::binary);
    if (!f)
        return mask;
    std::string data((std::istreambuf_iterator<char>(f)),
                     std::istreambuf_iterator<char>());
    if (data.empty())
        return mask;
    try {
        mbgl::PremultipliedImage img = mbgl::decodeImage(data);
        const uint32_t w = img.size.width, h = img.size.height;
        const uint8_t* src = img.data.get();
        for (int y = 0; y < DVD_H; ++y)
            for (int x = 0; x < DVD_W; ++x) {
                uint32_t sx = w ? (uint32_t)x * w / DVD_W : 0;
                uint32_t sy = h ? (uint32_t)y * h / DVD_H : 0;
                mask[(size_t)y * DVD_W + x] = src[((size_t)sy * w + sx) * 4 + 3];
            }
    } catch (...) {
    }
    return mask;
}

// Load a pre-rendered map-tile PNG (from mbgl-render) into an opaque RGBA
// SharedPixelBuffer, which renders in this build (unlike @image-url).
static slint::Image load_png_image(const std::string& path) {
    std::ifstream f(path, std::ios::binary);
    if (!f)
        return slint::Image();
    std::string data((std::istreambuf_iterator<char>(f)),
                     std::istreambuf_iterator<char>());
    if (data.empty())
        return slint::Image();
    try {
        mbgl::PremultipliedImage img = mbgl::decodeImage(data);
        const uint32_t w = img.size.width, h = img.size.height;
        const uint8_t* src = img.data.get();
        std::vector<uint8_t> rgba((size_t)w * h * 4);
        for (size_t i = 0; i < (size_t)w * h; ++i) {
            uint8_t a = src[i * 4 + 3];
            for (int c = 0; c < 3; ++c)
                rgba[i * 4 + c] =
                    a ? static_cast<uint8_t>(std::min(255, src[i * 4 + c] * 255 / a))
                      : 0;
            rgba[i * 4 + 3] = 255;  // opaque (tiles are full map crops)
        }
        slint::SharedPixelBuffer<slint::Rgba8Pixel> spb(
            w, h, reinterpret_cast<const slint::Rgba8Pixel*>(rgba.data()));
        return slint::Image(spb);
    } catch (...) {
        return slint::Image();
    }
}

// Build an opaque DVD-logo image: logo shape in `color`, the rest black (so it
// is seamless on the black screensaver field).
static slint::Image dvd_tinted(const std::vector<uint8_t>& mask,
                               uint32_t color) {
    uint8_t r = (color >> 16) & 0xff, g = (color >> 8) & 0xff, b = color & 0xff;
    std::vector<uint8_t> rgba((size_t)DVD_W * DVD_H * 4);
    for (size_t i = 0; i < (size_t)DVD_W * DVD_H; ++i) {
        bool on = i < mask.size() && mask[i] > 100;
        rgba[i * 4 + 0] = on ? r : 0;
        rgba[i * 4 + 1] = on ? g : 0;
        rgba[i * 4 + 2] = on ? b : 0;
        rgba[i * 4 + 3] = 255;
    }
    slint::SharedPixelBuffer<slint::Rgba8Pixel> spb(
        DVD_W, DVD_H, reinterpret_cast<const slint::Rgba8Pixel*>(rgba.data()));
    return slint::Image(spb);
}

// Monotonic milliseconds, shared by the idle watcher and the screensaver timer.
static int64_t now_ms() {
    return std::chrono::duration_cast<std::chrono::milliseconds>(
               std::chrono::steady_clock::now().time_since_epoch())
        .count();
}

static int64_t env_secs(const char* key, int64_t def) {
    if (const char* e = std::getenv(key)) {
        if (e[0] != '\0') {
            char* end = nullptr;
            long v = std::strtol(e, &end, 10);
            if (end != e && v >= 0)
                return v;
        }
    }
    return def;
}

// --- Screensaver shared state ------------------------------------------------

// One tick of a bouncing box; returns true on the tick a wall was hit.
static bool ss_bounce(float& x, float& y, float& vx, float& vy, float W, float H,
                      float bw, float bh) {
    x += vx;
    y += vy;
    bool b = false;
    if (x <= 0.f) {
        x = 0.f;
        vx = std::fabs(vx);
        b = true;
    } else if (x + bw >= W) {
        x = W - bw;
        vx = -std::fabs(vx);
        b = true;
    }
    if (y <= 0.f) {
        y = 0.f;
        vy = std::fabs(vy);
        b = true;
    } else if (y + bh >= H) {
        y = H - bh;
        vy = -std::fabs(vy);
        b = true;
    }
    return b;
}

int main(int /*argc*/, char** /*argv*/) {
    std::cout << "[main_gl] Starting zero-copy GL application" << std::endl;

    auto win = MapWindow::create();
    auto smap = std::make_shared<SlintMapGL>();

    // Idle clock, updated by the input watcher and the wake() callback.
    auto last_activity = std::make_shared<std::atomic<int64_t>>(now_ms());

    // Self-test: fill an 80x80 solid-red CPU image so the harness can read it
    // back and decide whether femtovg renders CPU images in this GL build.
    const bool selftest = std::getenv("MAPLIBRE_SELFTEST") != nullptr;

    // DVD logo: decode the PNG shape once into an alpha mask; we tint it to the
    // bounce colour each wall hit (Slint's PNG path does not render here, but a
    // hand-tinted opaque SharedPixelBuffer does).
    auto dvd_mask = std::make_shared<std::vector<uint8_t>>();
    {
        std::string home =
            std::getenv("HOME") ? std::getenv("HOME") : "/home/yuiseki";
        std::string p = std::getenv("MAPLIBRE_DVD_LOGO")
                            ? std::getenv("MAPLIBRE_DVD_LOGO")
                            : (home + "/dvd-logo.png");
        *dvd_mask = load_dvd_mask(p);
        win->set_dvd_image(dvd_tinted(*dvd_mask, 0x50c8ff));
    }

    // Pre-rendered map tiles (PNGs from mbgl-render) for the bouncing-tile
    // stage. Loaded once into RGBA SharedPixelBuffers (which render here).
    auto tiles = std::make_shared<std::vector<slint::Image>>();
    {
        std::string home =
            std::getenv("HOME") ? std::getenv("HOME") : "/home/yuiseki";
        std::string dir = std::getenv("MAPLIBRE_TILE_DIR")
                              ? std::getenv("MAPLIBRE_TILE_DIR")
                              : (home + "/screensaver-tiles");
        std::vector<std::string> names;
        if (DIR* d = opendir(dir.c_str())) {
            while (struct dirent* e = readdir(d)) {
                std::string n = e->d_name;
                if (n.size() > 4 && n.substr(n.size() - 4) == ".png")
                    names.push_back(n);
            }
            closedir(d);
        }
        std::sort(names.begin(), names.end());
        for (auto& n : names) {
            slint::Image img = load_png_image(dir + "/" + n);
            if (img.size().width > 0)
                tiles->push_back(img);
        }
        std::cout << "[main_gl] loaded " << tiles->size()
                  << " screensaver tiles from " << dir << std::endl;
    }

    std::string styleUrl = "https://demotiles.maplibre.org/style.json";
    if (const char* env = std::getenv("MAPLIBRE_STYLE_URL")) {
        if (env[0] != '\0')
            styleUrl = env;
    }

    // Optional explicit render size; otherwise the display's native size is
    // used (so it fills the screen at any resolution, including small panels).
    int envW = 0, envH = 0;
    if (const char* e = std::getenv("MAPLIBRE_WIDTH"))
        envW = std::atoi(e);
    if (const char* e = std::getenv("MAPLIBRE_HEIGHT"))
        envH = std::atoi(e);

    auto gl_ready = std::make_shared<bool>(false);
    auto tex = std::make_shared<GLuint>(0);
    auto fbo = std::make_shared<GLuint>(0);
    auto rbo = std::make_shared<GLuint>(0);
    auto Wp = std::make_shared<int>(0);
    auto Hp = std::make_shared<int>(0);

    win->window().set_rendering_notifier([=](slint::RenderingState state,
                                             slint::GraphicsAPI api) {
        switch (state) {
        case slint::RenderingState::RenderingSetup: {
            if (api != slint::GraphicsAPI::NativeOpenGL) {
                std::cout << "[main_gl] WARNING: GraphicsAPI is not "
                             "NativeOpenGL; zero-copy GL path unavailable"
                          << std::endl;
                return;
            }

            auto ps = win->window().size();
            int w = envW > 0
                        ? envW
                        : (ps.width > 0 ? static_cast<int>(ps.width) : 1280);
            int h = envH > 0
                        ? envH
                        : (ps.height > 0 ? static_cast<int>(ps.height) : 720);
            *Wp = w;
            *Hp = h;
            std::cout << "[main_gl] RenderingSetup: NativeOpenGL acquired, "
                         "render size "
                      << w << "x" << h << std::endl;

            glGenTextures(1, tex.get());
            glBindTexture(GL_TEXTURE_2D, *tex);
            glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MIN_FILTER, GL_LINEAR);
            glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MAG_FILTER, GL_LINEAR);
            glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_WRAP_S, GL_CLAMP_TO_EDGE);
            glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_WRAP_T, GL_CLAMP_TO_EDGE);
            glTexImage2D(GL_TEXTURE_2D, 0, GL_RGBA, w, h, 0, GL_RGBA,
                         GL_UNSIGNED_BYTE, nullptr);

            glGenRenderbuffers(1, rbo.get());
            glBindRenderbuffer(GL_RENDERBUFFER, *rbo);
            glRenderbufferStorage(GL_RENDERBUFFER, GL_DEPTH24_STENCIL8, w, h);

            glGenFramebuffers(1, fbo.get());
            glBindFramebuffer(GL_FRAMEBUFFER, *fbo);
            glFramebufferTexture2D(GL_FRAMEBUFFER, GL_COLOR_ATTACHMENT0,
                                   GL_TEXTURE_2D, *tex, 0);
            glFramebufferRenderbuffer(GL_FRAMEBUFFER,
                                      GL_DEPTH_STENCIL_ATTACHMENT,
                                      GL_RENDERBUFFER, *rbo);

            GLenum status = glCheckFramebufferStatus(GL_FRAMEBUFFER);
            std::cout << "[main_gl] FBO status="
                      << (status == GL_FRAMEBUFFER_COMPLETE
                              ? "GL_FRAMEBUFFER_COMPLETE"
                              : std::to_string(status))
                      << " fbo=" << *fbo << " tex=" << *tex << std::endl;

            glBindFramebuffer(GL_FRAMEBUFFER, 0);

            smap->setup(*fbo, w, h, styleUrl);
            *gl_ready = true;
            break;
        }
        case slint::RenderingState::BeforeRendering: {
            if (!*gl_ready)
                return;

            GLint pf = 0;
            glGetIntegerv(GL_FRAMEBUFFER_BINDING, &pf);
            GLint vp[4] = {0, 0, 0, 0};
            glGetIntegerv(GL_VIEWPORT, vp);
            GLint prog = 0;
            glGetIntegerv(GL_CURRENT_PROGRAM, &prog);
            GLint arrayBuf = 0;
            glGetIntegerv(GL_ARRAY_BUFFER_BINDING, &arrayBuf);
            GLint elemBuf = 0;
            glGetIntegerv(GL_ELEMENT_ARRAY_BUFFER_BINDING, &elemBuf);
            GLint activeTex = 0;
            glGetIntegerv(GL_ACTIVE_TEXTURE, &activeTex);
            GLboolean blend = glIsEnabled(GL_BLEND);
            GLboolean depth = glIsEnabled(GL_DEPTH_TEST);
            GLboolean scissor = glIsEnabled(GL_SCISSOR_TEST);
            GLboolean cull = glIsEnabled(GL_CULL_FACE);

            // Render the live map into the FBO; the MMapView shows it in stage
            // 0. While the screensaver runs the opaque Slint overlay covers it.
            smap->render();

            glBindFramebuffer(GL_FRAMEBUFFER, pf);
            glViewport(vp[0], vp[1], vp[2], vp[3]);
            glUseProgram(prog);
            glBindBuffer(GL_ARRAY_BUFFER, arrayBuf);
            glBindBuffer(GL_ELEMENT_ARRAY_BUFFER, elemBuf);
            glActiveTexture(activeTex);
            if (blend)
                glEnable(GL_BLEND);
            else
                glDisable(GL_BLEND);
            if (depth)
                glEnable(GL_DEPTH_TEST);
            else
                glDisable(GL_DEPTH_TEST);
            if (scissor)
                glEnable(GL_SCISSOR_TEST);
            else
                glDisable(GL_SCISSOR_TEST);
            if (cull)
                glEnable(GL_CULL_FACE);
            else
                glDisable(GL_CULL_FACE);

            // The persistent full-screen MMapView shows whatever we composited
            // into the FBO (live map, or black + bouncing logo/tile).
            win->global<MMapAdapter>().set_frame(
                slint::Image::create_from_borrowed_gl_2d_rgba_texture(
                    *tex,
                    {static_cast<uint32_t>(*Wp), static_cast<uint32_t>(*Hp)},
                    slint::Image::BorrowedOpenGLTextureOrigin::BottomLeft));
            win->window().request_redraw();
            break;
        }
        case slint::RenderingState::AfterRendering: {
            // Pixel-readback self-test: with the bounce frozen at (40,40), scan
            // the 140x140 logo/tile region of the actual displayed framebuffer
            // and report. Stage 1 (DVD) -> some non-black; stage 2 (tile) ->
            // mostly non-black map colour; stage 3 (off) -> all black.
            if (selftest && *gl_ready) {
                static int f = 0;
                ++f;
                if (f % 120 == 0 && f <= 1800) {  // every ~2s for ~30s
                    glBindFramebuffer(GL_READ_FRAMEBUFFER, 0);
                    int nonblack = 0, total = 0, sr = 0, sg = 0, sb = 0;
                    for (int y = 44; y < 176; y += 8)
                        for (int x = 44; x < 176; x += 8) {
                            unsigned char px[4] = {0, 0, 0, 0};
                            glReadPixels(x, *Hp - 1 - y, 1, 1, GL_RGBA,
                                         GL_UNSIGNED_BYTE, px);
                            ++total;
                            if (px[0] + px[1] + px[2] > 24) {
                                ++nonblack;
                                sr += px[0];
                                sg += px[1];
                                sb += px[2];
                            }
                        }
                    std::cout << "[selftest] f=" << f
                              << " saver-state=" << win->get_saver_state()
                              << " region(40,40,140,140): " << nonblack << "/"
                              << total << " non-black";
                    if (nonblack)
                        std::cout << " avg=" << sr / nonblack << ","
                                  << sg / nonblack << "," << sb / nonblack;
                    std::cout << " glErr=" << glGetError() << std::endl;
                }
            }
            break;
        }
        case slint::RenderingState::RenderingTeardown: {
            std::cout << "[main_gl] RenderingTeardown" << std::endl;
            if (*fbo)
                glDeleteFramebuffers(1, fbo.get());
            if (*rbo)
                glDeleteRenderbuffers(1, rbo.get());
            if (*tex)
                glDeleteTextures(1, tex.get());
            *fbo = *rbo = *tex = 0;
            *gl_ready = false;
            break;
        }
        }
    });

    // Touch / pointer interaction (Slint delivers touch via libinput as pointer
    // events; the MMapView forwards them through these MMapAdapter callbacks).
    win->global<MMapAdapter>().on_mouse_pressed(
        [=](float x, float y) { smap->handle_mouse_press(x, y); });
    win->global<MMapAdapter>().on_mouse_released(
        [=](float, float) { smap->handle_mouse_release(); });
    win->global<MMapAdapter>().on_mouse_moved(
        [=](float x, float y) { smap->handle_mouse_move(x, y, true); });
    // Double-tap is detected inside handle_mouse_press (touchscreens do not
    // reliably emit Slint's double-clicked), so on_double_clicked is left
    // unwired to avoid double-triggering with a real mouse.
    win->global<MMapAdapter>().on_wheel_zoomed(
        [=](float x, float y, float dy) { smap->handle_wheel_zoom(x, y, dy); });

    // Toolbar commands (dropdown / buttons / sliders).
    win->global<MMapAdapter>().on_request_style_change(
        [=](const slint::SharedString& u) {
            smap->setStyleUrl(std::string(u.data(), u.size()));
        });
    win->global<MMapAdapter>().on_request_fly_to(
        [=](float lat, float lon, float z) { smap->fly_to(lat, lon, z); });
    win->global<MMapAdapter>().on_request_zoom_change(
        [=](float z) { smap->set_zoom(z); });
    win->global<MMapAdapter>().on_request_pitch_change(
        [=](float p) { smap->set_pitch(p); });
    win->global<MMapAdapter>().on_request_bearing_change(
        [=](float b) { smap->set_bearing(b); });

    win->on_map_size_changed([=]() {});

    // "Dance" button: start/stop the pitch+bearing animation at the current view.
    win->on_set_dance([=](bool on) { smap->set_dance(on); });

    // Keyboard arrow-key pan (Shift+Up/Down zoom is handled in the .slint).
    win->on_pan([=](float dx, float dy) { smap->handle_pan(dx, dy); });

    // ---------------------------------------------------------------------
    // Staged idle screensaver (pi4-s-d appliance feature; NEVER upstreamed).
    //
    //   0 normal
    //   1 DVD logo bounce            [SAVER_SECS .. OFF)
    //   3 off / black                [OFF ..)
    //
    // (Stage 2, the bouncing pre-rendered map tile, is added in a later pass.)
    //
    // OFF is power-aware via PiSugar/pi-power: on AC the panel stays alive far
    // longer than on battery. Any input (raw evdev watcher, or the overlay's
    // wake() tap) resets the idle clock and restores the live map.
    // ---------------------------------------------------------------------

    // (a) Raw evdev activity watcher. Reading the input nodes directly means a
    //     keyboard press counts as activity too (the map only ever sees pointer
    //     events), and a tap registers even while the black overlay is up.
    {
        auto la = last_activity;
        std::thread([la]() {
            std::vector<std::string> paths;
            if (const char* env = std::getenv("MAPLIBRE_INPUT_DEVS")) {
                std::string s(env), cur;
                for (char c : s) {
                    if (c == ',') {
                        if (!cur.empty())
                            paths.push_back(cur);
                        cur.clear();
                    } else {
                        cur += c;
                    }
                }
                if (!cur.empty())
                    paths.push_back(cur);
            } else if (DIR* d = opendir("/dev/input")) {
                while (struct dirent* e = readdir(d)) {
                    if (std::strncmp(e->d_name, "event", 5) == 0)
                        paths.push_back(std::string("/dev/input/") + e->d_name);
                }
                closedir(d);
            }
            std::vector<pollfd> pfds;
            for (auto& p : paths) {
                int fd = open(p.c_str(), O_RDONLY | O_NONBLOCK);
                if (fd >= 0)
                    pfds.push_back({fd, POLLIN, 0});
            }
            if (pfds.empty())
                return;
            char buf[256];
            while (true) {
                int r = poll(pfds.data(), pfds.size(), 1000);
                if (r <= 0)
                    continue;
                bool any = false;
                for (auto& pf : pfds) {
                    if (pf.revents & POLLIN) {
                        while (read(pf.fd, buf, sizeof(buf)) > 0) {
                        }
                        any = true;
                    }
                }
                if (any)
                    la->store(now_ms());
            }
        }).detach();
    }

    // (b) Overlay tap -> wake.
    win->on_wake([=]() { last_activity->store(now_ms()); });

    // (c) Power-state poller (PiSugar via pi-power). AC -> long OFF timeout,
    //     battery -> short. Polled off the UI thread so nc never stalls it.
    auto plugged = std::make_shared<std::atomic<bool>>(true);
    {
        auto pl = plugged;
        std::string home = std::getenv("HOME") ? std::getenv("HOME") : "/home/pi";
        std::string cmd = home + "/.local/bin/pi-power 2>/dev/null";
        std::thread([pl, cmd]() {
            while (true) {
                bool p = pl->load();
                if (FILE* fp = popen(cmd.c_str(), "r")) {
                    char line[160];
                    while (std::fgets(line, sizeof(line), fp)) {
                        if (std::strstr(line, "battery_power_plugged"))
                            p = std::strstr(line, "true") != nullptr;
                    }
                    pclose(fp);
                }
                pl->store(p);
                std::this_thread::sleep_for(std::chrono::seconds(30));
            }
        }).detach();
    }

    // (d) Stage driver: a 60ms repeating timer on the UI thread. The
    //     screensaver never touches the live map (tiles are pre-rendered PNGs),
    //     so this just drives the stage, the bounce, the DVD recolour, and the
    //     cached-tile swap.
    const int64_t saver_secs = env_secs("MAPLIBRE_SAVER_SECS", 300);    // 5 min
    const int64_t dvd_secs = env_secs("MAPLIBRE_DVD_SECS", 1800);       // +30 min
    const int64_t off_ac = env_secs("MAPLIBRE_OFF_AC_SECS", 43200);     // 12 h
    const int64_t off_batt = env_secs("MAPLIBRE_OFF_BATT_SECS", 1800);  // 30 min
    static const uint32_t COLORS[6] = {0xff5050, 0x50c8ff, 0x78ff78,
                                       0xffdc50, 0xdc78ff, 0xff9650};

    struct SaverState {
        float bx = 40.f, by = 40.f, bvx = 3.75f, bvy = 3.0f;
        int ci = 0;
        int prev = -1;
        size_t show = 0;
    };
    auto ss = std::make_shared<SaverState>();

    auto saver_timer = std::make_shared<slint::Timer>();
    saver_timer->start(
        slint::TimerMode::Repeated, std::chrono::milliseconds(60), [=]() {
            int64_t idle = (now_ms() - last_activity->load()) / 1000;
            int64_t off = plugged->load() ? off_ac : off_batt;
            int stage = idle >= off                     ? 3
                        : idle >= saver_secs + dvd_secs  ? 2
                        : idle >= saver_secs             ? 1
                                                         : 0;

            if (stage != ss->prev) {
                if (stage == 1)
                    win->set_dvd_image(dvd_tinted(*dvd_mask, COLORS[ss->ci]));
                if (stage == 2 && !tiles->empty()) {
                    ss->show %= tiles->size();
                    win->set_tile_image((*tiles)[ss->show]);
                }
                std::cout << "[saver] stage -> " << stage << " (idle " << idle
                          << "s, " << (plugged->load() ? "AC" : "battery") << ")"
                          << std::endl;
                ss->prev = stage;
                win->window().request_redraw();
            }
            win->set_saver_state(stage);

            if (stage != 1 && stage != 2)
                return;

            const float W = (*Wp > 0) ? static_cast<float>(*Wp) : 720.f;
            const float H = (*Hp > 0) ? static_cast<float>(*Hp) : 480.f;
            const float bw = (stage == 1) ? 140.f : 220.f;
            const float bh = (stage == 1) ? 84.f : 220.f;
            bool bounced =
                selftest ? false
                         : ss_bounce(ss->bx, ss->by, ss->bvx, ss->bvy, W, H, bw,
                                     bh);
            win->set_logo_x(ss->bx);
            win->set_logo_y(ss->by);
            if (stage == 1) {
                if (bounced) {
                    ss->ci = (ss->ci + 1) % 6;
                    win->set_dvd_image(dvd_tinted(*dvd_mask, COLORS[ss->ci]));
                }
            } else if (bounced && !tiles->empty()) {
                ss->show = (ss->show + 1) % tiles->size();
                win->set_tile_image((*tiles)[ss->show]);
            }
            win->window().request_redraw();
        });

    std::cout << "[main_gl] Entering UI event loop" << std::endl;
    win->run();
    return 0;
}
