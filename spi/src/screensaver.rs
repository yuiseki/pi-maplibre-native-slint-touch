//! Staged idle screensaver for the KMS/touch build.
//!
//! A background thread watches the touchscreen evdev node (shared, non-grabbing)
//! and records the last activity time, so activity is detected even while the
//! map consumes pointer events. A timer turns idle time into a stage:
//!
//!   0 normal
//!   1 DVD logo bounce            [SAVER_SECS .. SAVER_SECS+DVD_SECS)
//!   2 bouncing square map tile   [SAVER_SECS+DVD_SECS .. OFF_SECS)
//!       a set of MAPLIBRE_TILE_CACHE tiles (random region+style at
//!       MAPLIBRE_TILE_ZOOM) is pre-rendered once into an image cache; the map
//!       only renders during that warm-up. After that each wall bounce just
//!       swaps to the next cached image, so bouncing never stalls the loop.
//!   3 off / black                [OFF_SECS ..)
//!
//! Any touch wakes (overlay pointer-down -> wake(), evdev watcher as backstop)
//! and the user's pre-screensaver style + camera are restored.

use slint::ComponentHandle;
use std::cell::RefCell;
use std::fs::File;
use std::io::Read;
use std::rc::Rc;
use std::sync::atomic::{AtomicU64, Ordering};
use std::sync::Arc;
use std::thread;
use std::time::{Duration, SystemTime, UNIX_EPOCH};

use crate::maplibre::{MapCamera, MapLibre};

const STYLES: [&str; 3] = [
    "https://tile.openstreetmap.jp/styles/osm-bright-ja/style.json",
    "https://tile.openstreetmap.jp/styles/maptiler-basic-ja/style.json",
    "https://yuiseki.dev/static/styles/osm-fiord.json",
];
// (lat, lon). Extend freely.
const REGIONS: [(f64, f64); 4] = [
    (48.8566, 2.3522),   // Paris
    (40.7128, -74.0060), // New York
    (35.6895, 139.6917), // Tokyo
    (34.3853, 132.4553), // Hiroshima
];
const TILE_ZOOM_DEFAULT: f64 = 4.0;

const W: f32 = 480.0;
const H: f32 = 320.0;
const LW: f32 = 140.0; // DVD logo (stage 1)
const LH: f32 = 84.0;
const TS: f32 = 140.0; // square map tile (stage 2)
const COLORS: [u32; 6] = [0xff5050, 0x50c8ff, 0x78ff78, 0xffdc50, 0xdc78ff, 0xff9650];

fn now_ms() -> u64 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|d| d.as_millis() as u64)
        .unwrap_or(0)
}

fn env_secs(key: &str, default: u64) -> u64 {
    std::env::var(key).ok().and_then(|s| s.parse().ok()).unwrap_or(default)
}

fn env_f64(key: &str, default: f64) -> f64 {
    std::env::var(key).ok().and_then(|s| s.parse().ok()).unwrap_or(default)
}

fn lcg(x: u64) -> u64 {
    x.wrapping_mul(6364136223846793005).wrapping_add(1442695040888963407)
}

/// Advance one tick and bounce off the walls of a `bw`x`bh` box.
/// Returns true on the tick a wall was hit.
fn advance_bounce(
    x: &mut f32,
    y: &mut f32,
    vx: &mut f32,
    vy: &mut f32,
    ci: &mut usize,
    bw: f32,
    bh: f32,
) -> bool {
    *x += *vx;
    *y += *vy;
    let mut bounced = false;
    if *x <= 0.0 {
        *x = 0.0;
        *vx = (*vx).abs();
        bounced = true;
    } else if *x + bw >= W {
        *x = W - bw;
        *vx = -(*vx).abs();
        bounced = true;
    }
    if *y <= 0.0 {
        *y = 0.0;
        *vy = (*vy).abs();
        bounced = true;
    } else if *y + bh >= H {
        *y = H - bh;
        *vy = -(*vy).abs();
        bounced = true;
    }
    if bounced {
        *ci = (*ci + 1) % COLORS.len();
    }
    bounced
}

/// Crop the centre TSxTS (square) of the map's last frame into a still image.
/// Returns None if no frame is ready yet.
fn capture_tile_image(map: &Rc<RefCell<MapLibre>>) -> Option<slint::Image> {
    let mut m = map.borrow_mut();
    if !m.has_frame() {
        return None;
    }
    let image = m.read_still_image();
    let buf = image.as_image();
    let w = buf.width() as usize;
    let h = buf.height() as usize;
    let raw = buf.as_raw();
    let cw = TS as usize;
    let ch = TS as usize;
    if w < cw || h < ch || raw.len() < w * h * 4 {
        return None;
    }
    let x0 = (w - cw) / 2;
    let y0 = (h - ch) / 2;
    let mut spb = slint::SharedPixelBuffer::<slint::Rgba8Pixel>::new(cw as u32, ch as u32);
    {
        let dst = spb.make_mut_bytes();
        for row in 0..ch {
            let s = ((y0 + row) * w + x0) * 4;
            let d = row * cw * 4;
            dst[d..d + cw * 4].copy_from_slice(&raw[s..s + cw * 4]);
        }
    }
    Some(slint::Image::from_rgba8(spb))
}

/// All (style, region) pairs, shuffled, truncated to `n` (the pre-render set).
fn build_combos(rng: &mut u64, n: usize) -> Vec<(usize, usize)> {
    let mut all: Vec<(usize, usize)> = Vec::new();
    for si in 0..STYLES.len() {
        for ri in 0..REGIONS.len() {
            all.push((si, ri));
        }
    }
    for i in (1..all.len()).rev() {
        *rng = lcg(*rng);
        let j = (*rng >> 17) as usize % (i + 1);
        all.swap(i, j);
    }
    all.truncate(n.max(1).min(all.len()));
    all
}

pub fn install(ui: &crate::MapWindow, map: &Rc<RefCell<MapLibre>>) {
    let last = Arc::new(AtomicU64::new(now_ms()));

    // Touch-activity watcher (raw evdev; coexists with Slint's libinput).
    let dev = std::env::var("MAPLIBRE_TOUCH_DEV").unwrap_or_else(|_| "/dev/input/event4".to_string());
    {
        let last = last.clone();
        thread::spawn(move || loop {
            if let Ok(mut f) = File::open(&dev) {
                let mut b = [0u8; 24 * 32];
                loop {
                    match f.read(&mut b) {
                        Ok(n) if n > 0 => last.store(now_ms(), Ordering::Relaxed),
                        _ => break,
                    }
                }
            }
            thread::sleep(Duration::from_millis(500));
        });
    }

    {
        let last = last.clone();
        ui.on_wake(move || last.store(now_ms(), Ordering::Relaxed));
    }

    let saver = env_secs("MAPLIBRE_SAVER_SECS", 300);
    let dvd = env_secs("MAPLIBRE_DVD_SECS", 1800);
    let off = env_secs("MAPLIBRE_OFF_SECS", 43200);
    let tile_zoom = env_f64("MAPLIBRE_TILE_ZOOM", TILE_ZOOM_DEFAULT);
    let load_secs = env_secs("MAPLIBRE_TILE_LOAD_SECS", 15);
    let cache_target = env_secs("MAPLIBRE_TILE_CACHE", 8).max(1) as usize;

    let map_t = map.clone();
    let weak = ui.as_weak();
    let mut prev_stage: i32 = -1;
    let mut bx = 40.0f32;
    let mut by = 40.0f32;
    let mut bvx = 3.75f32;
    let mut bvy = 3.0f32;
    let mut ci = 0usize;
    let mut loading = false;
    let mut load_start = 0u64;
    let mut saved: Option<(String, MapCamera)> = None;
    let mut rng = now_ms() | 1;

    // Pre-rendered tile cache: bounce only swaps a cached image (no map work).
    let mut tile_cache: Vec<slint::Image> = Vec::new();
    let mut cache_show: usize = 0;
    let mut combos: Vec<(usize, usize)> = Vec::new();
    let mut combo_pos: usize = 0;

    let timer = slint::Timer::default();
    timer.start(slint::TimerMode::Repeated, Duration::from_millis(60), move || {
        let Some(ui) = weak.upgrade() else { return };
        let idle = now_ms().saturating_sub(last.load(Ordering::Relaxed)) / 1000;
        let stage: i32 = if idle >= off {
            3
        } else if idle >= saver + dvd {
            2
        } else if idle >= saver {
            1
        } else {
            0
        };

        if stage != prev_stage {
            if prev_stage <= 0 && stage >= 1 {
                let m = map_t.borrow();
                saved = Some((m.style_url().to_string(), m.camera()));
            }
            if stage == 0 && prev_stage >= 1 {
                if let Some((s, c)) = saved.take() {
                    let mut m = map_t.borrow_mut();
                    m.load_style(&s);
                    m.fly_to(c.lat, c.lon, c.zoom);
                }
                loading = false;
                tile_cache.clear();
                combos.clear();
            }
            if stage == 2 {
                // Plan the pre-render set; tiles fill the cache one at a time.
                combos = build_combos(&mut rng, cache_target);
                combo_pos = 0;
                cache_show = 0;
                tile_cache.clear();
                loading = false;
            }
            eprintln!("[saver] stage -> {} (idle {}s)", stage, idle);
            prev_stage = stage;
            ui.window().request_redraw();
        }
        ui.set_saver_state(stage);

        match stage {
            0 => ui.set_map_render_active(true),
            3 => ui.set_map_render_active(false),
            1 => {
                ui.set_map_render_active(false);
                advance_bounce(&mut bx, &mut by, &mut bvx, &mut bvy, &mut ci, LW, LH);
                ui.set_logo_x(bx);
                ui.set_logo_y(by);
                let c = COLORS[ci];
                ui.set_logo_color(slint::Brush::SolidColor(slint::Color::from_rgb_u8(
                    (c >> 16) as u8,
                    (c >> 8) as u8,
                    c as u8,
                )));
                ui.window().request_redraw();
            }
            2 => {
                // Warm-up: pre-render the planned tiles into the cache, one at a
                // time. The map only renders during this phase; once the cache is
                // full we stop rendering entirely, so bouncing stays smooth.
                if tile_cache.len() < combos.len() {
                    if !loading {
                        let (si, ri) = combos[combo_pos % combos.len()];
                        let (lat, lon) = REGIONS[ri];
                        {
                            let mut m = map_t.borrow_mut();
                            m.load_style(STYLES[si]);
                            m.fly_to(lat, lon, tile_zoom);
                        }
                        loading = true;
                        load_start = now_ms();
                        ui.set_map_render_active(true);
                        eprintln!(
                            "[saver] prerender {}/{} style#{} region={:.3},{:.3} zoom={}",
                            tile_cache.len() + 1,
                            combos.len(),
                            si,
                            lat,
                            lon,
                            tile_zoom
                        );
                    } else if now_ms().saturating_sub(load_start) >= load_secs * 1000 {
                        if let Some(img) = capture_tile_image(&map_t) {
                            if tile_cache.is_empty() {
                                ui.set_tile_image(img.clone()); // show the first as soon as ready
                            }
                            tile_cache.push(img);
                        }
                        loading = false;
                        combo_pos += 1;
                        if tile_cache.len() >= combos.len() {
                            ui.set_map_render_active(false);
                            eprintln!("[saver] prerender complete ({} tiles)", tile_cache.len());
                        }
                    }
                }

                // Bounce only swaps to the next cached tile: no map work, no freeze.
                let bounced = advance_bounce(&mut bx, &mut by, &mut bvx, &mut bvy, &mut ci, TS, TS);
                if bounced && !tile_cache.is_empty() {
                    cache_show = (cache_show + 1) % tile_cache.len();
                    ui.set_tile_image(tile_cache[cache_show].clone());
                }
                ui.set_logo_x(bx);
                ui.set_logo_y(by);
                ui.window().request_redraw();
            }
            _ => {}
        }
    });
    Box::leak(Box::new(timer));
}
