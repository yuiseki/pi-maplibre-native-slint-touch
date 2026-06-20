//! Idle screensaver for the KMS/touch build.
//!
//! A background thread watches the touchscreen evdev node (shared, non-grabbing)
//! and records the last activity time — this works even while the map itself is
//! consuming pointer events. A Slint timer turns that into a screensaver state:
//!   0 = normal, 1 = bouncing logo (after MAPLIBRE_SAVER_SECS),
//!   2 = black/"off" (after MAPLIBRE_OFF_SECS).
//! Any touch (or the overlay's pointer-down -> wake()) returns to normal.

use slint::ComponentHandle;
use std::cell::RefCell;
use std::fs::File;
use std::io::Read;
use std::rc::Rc;
use std::sync::atomic::{AtomicU64, Ordering};
use std::sync::Arc;
use std::thread;
use std::time::{Duration, SystemTime, UNIX_EPOCH};

fn now_ms() -> u64 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|d| d.as_millis() as u64)
        .unwrap_or(0)
}

fn env_secs(key: &str, default: u64) -> u64 {
    std::env::var(key)
        .ok()
        .and_then(|s| s.parse().ok())
        .unwrap_or(default)
}

pub fn install(ui: &crate::MapWindow) {
    let last = Arc::new(AtomicU64::new(now_ms()));

    // Touch-activity watcher thread (raw evdev; coexists with Slint's libinput).
    let dev = std::env::var("MAPLIBRE_TOUCH_DEV")
        .unwrap_or_else(|_| "/dev/input/event4".to_string());
    {
        let last = last.clone();
        thread::spawn(move || loop {
            if let Ok(mut f) = File::open(&dev) {
                let mut buf = [0u8; 24 * 32];
                loop {
                    match f.read(&mut buf) {
                        Ok(n) if n > 0 => last.store(now_ms(), Ordering::Relaxed),
                        _ => break, // EOF / error -> reopen
                    }
                }
            }
            thread::sleep(Duration::from_millis(500));
        });
    }

    let saver_secs = env_secs("MAPLIBRE_SAVER_SECS", 300);
    let off_secs = env_secs("MAPLIBRE_OFF_SECS", 900);

    // wake(): user tapped the overlay -> reset activity immediately.
    {
        let last = last.clone();
        ui.on_wake(move || last.store(now_ms(), Ordering::Relaxed));
    }

    // Idle/animation timer on the UI thread.
    let colors: [u32; 6] = [0xff5050, 0x50c8ff, 0x78ff78, 0xffdc50, 0xdc78ff, 0xff9650];
    // (x, y, vx, vy, color_index) — velocity is 1.25x the original 3.0/2.4.
    let logo = Rc::new(RefCell::new((40.0f32, 40.0f32, 3.75f32, 3.0f32, 0usize)));
    let weak = ui.as_weak();
    let mut prev_state: i32 = -1;
    let timer = slint::Timer::default();
    timer.start(slint::TimerMode::Repeated, Duration::from_millis(60), move || {
        let Some(ui) = weak.upgrade() else { return };
        let idle = now_ms().saturating_sub(last.load(Ordering::Relaxed)) / 1000;
        let state: i32 = if idle >= off_secs {
            2
        } else if idle >= saver_secs {
            1
        } else {
            0
        };
        if state != prev_state {
            eprintln!("[saver] state -> {} (idle {}s)", state, idle);
            prev_state = state;
            ui.window().request_redraw(); // repaint on enter/leave screensaver
        }
        ui.set_saver_state(state);

        if state == 1 {
            const W: f32 = 480.0;
            const H: f32 = 320.0;
            const LW: f32 = 140.0;
            const LH: f32 = 84.0;
            let mut l = logo.borrow_mut();
            l.0 += l.2;
            l.1 += l.3;
            let mut bounced = false;
            if l.0 <= 0.0 {
                l.0 = 0.0;
                l.2 = l.2.abs();
                bounced = true;
            } else if l.0 + LW >= W {
                l.0 = W - LW;
                l.2 = -l.2.abs();
                bounced = true;
            }
            if l.1 <= 0.0 {
                l.1 = 0.0;
                l.3 = l.3.abs();
                bounced = true;
            } else if l.1 + LH >= H {
                l.1 = H - LH;
                l.3 = -l.3.abs();
                bounced = true;
            }
            if bounced {
                l.4 = (l.4 + 1) % colors.len();
            }
            ui.set_logo_x(l.0);
            ui.set_logo_y(l.1);
            let c = colors[l.4];
            ui.set_logo_color(slint::Brush::SolidColor(slint::Color::from_rgb_u8(
                (c >> 16) as u8,
                (c >> 8) as u8,
                c as u8,
            )));
            // The map drives frames; when idle nothing requests a repaint,
            // so force one each tick to actually animate the bouncing logo.
            ui.window().request_redraw();
        }
    });
    // Keep the timer running for the lifetime of the app.
    Box::leak(Box::new(timer));
}
