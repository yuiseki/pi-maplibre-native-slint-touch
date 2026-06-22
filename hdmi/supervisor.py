#!/usr/bin/env python3
"""HDMI 版スーパバイザ: zero-copy GL マップ <-> ネイティブコンソール の切替。

pi-z2-display-hat-mini の supervisor を HDMI 機 (pi4-s-d) 向けに簡略化したもの。
あちらは自前 pyte ターミナルを描くが、HDMI 機では「ターミナル = tty1 の
getty(autologin シェル)」をそのまま使う。マップ(seatd/libseat で DRM master を
占有)を止めれば fbcon コンソールが HDMI に復帰し、USB キーボードで操作できる。

モード:
  MAP      : maplibre-slint-gl を子プロセスとして起動(既定)。
  TERMINAL : 子を止め、tty1 の console を前面に出す(本プロセスは何も描かない)。

切替:
  - MAP 中に USB キーボードの Ctrl+C を 1.5 秒以内に2回 → TERMINAL
  - TERMINAL の shell で `pi-maps` (= /tmp/pi-display/request に "map") → MAP

キーボードは /dev/input/by-id/*-event-kbd を生 evdev で読む(python3-evdev 不要)。
Slint(libinput)はキーボードを排他 grab しないので並行読取できる。
"""
import glob
import os
import select
import signal
import struct
import subprocess
import time

BIN = os.path.expanduser("~/maplibre-slint-gl")
REQUEST_DIR = "/tmp/pi-display"
REQUEST = os.path.join(REQUEST_DIR, "request")

# linux/input-event-codes.h
EV_KEY = 0x01
KEY_LEFTCTRL, KEY_RIGHTCTRL, KEY_C = 29, 97, 46
# struct input_event: timeval(long sec, long usec) + u16 type + u16 code + s32 value
EVENT_FMT = "llHHi"
EVENT_SIZE = struct.calcsize(EVENT_FMT)
DOUBLE_WINDOW = 1.5  # Ctrl+C 連続2回とみなす最大間隔 (秒)


def log(*a):
    print("[supervisor]", *a, flush=True)


def start_map():
    log("start MAP")
    # env (SLINT_BACKEND / LD_LIBRARY_PATH / MAPLIBRE_*) は systemd unit から継承する。
    return subprocess.Popen([BIN], start_new_session=True)


def stop_map(proc):
    """子プロセスグループを停止し、libseat/DRM が解放され console が戻るまで待つ。"""
    if proc is None or proc.poll() is not None:
        return
    try:
        os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
    except Exception:
        pass
    for _ in range(30):
        if proc.poll() is not None:
            break
        time.sleep(0.1)
    if proc.poll() is None:
        try:
            os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
        except Exception:
            pass
        try:
            proc.wait(timeout=2)
        except Exception:
            pass
    time.sleep(1.0)  # fbcon が HDMI を取り戻すのを待つ


def read_request():
    try:
        if os.path.exists(REQUEST):
            with open(REQUEST) as f:
                m = f.read().strip()
            os.remove(REQUEST)
            return m or None
    except Exception:
        pass
    return None


def main():
    os.makedirs(REQUEST_DIR, exist_ok=True)
    read_request()  # 起動時に古い要求を捨てる

    kbds = {}  # path -> fd

    def rescan_keyboards():
        for p in glob.glob("/dev/input/by-id/*-event-kbd"):
            if p not in kbds:
                try:
                    kbds[p] = os.open(p, os.O_RDONLY | os.O_NONBLOCK)
                    log("keyboard:", p)
                except OSError:
                    pass

    def drop_fd(fd):
        for p, f in list(kbds.items()):
            if f == fd:
                del kbds[p]
        try:
            os.close(fd)
        except Exception:
            pass

    rescan_keyboards()
    mode = "map"
    child = start_map()
    ctrl_held = False
    last_ctrl_c = 0.0
    last_scan = time.time()
    log("started. mode=MAP keyboards=%d" % len(kbds))

    try:
        while True:
            # --- 切替要求 (pi-maps) ---
            req = read_request()
            if req == "map":
                if mode != "map" or child is None or child.poll() is not None:
                    stop_map(child)
                    child = start_map()
                    mode = "map"
                    log("-> MAP (request)")
            elif req == "terminal":
                if mode != "terminal":
                    stop_map(child)
                    child = None
                    mode = "terminal"
                    log("-> TERMINAL (request)")

            # --- MAP のクラッシュ復帰 ---
            if mode == "map" and (child is None or child.poll() is not None):
                log("map child exited unexpectedly -> restart")
                child = start_map()

            # --- ホットプラグ再スキャン ---
            now = time.time()
            if now - last_scan >= 2.0:
                last_scan = now
                rescan_keyboards()

            # --- キーボード読取 (Ctrl+C x2 は MAP 中のみ作用) ---
            if not kbds:
                time.sleep(0.3)
                continue
            try:
                r, _, _ = select.select(list(kbds.values()), [], [], 0.3)
            except OSError:
                continue
            for fd in r:
                try:
                    data = os.read(fd, EVENT_SIZE * 64)
                except OSError:
                    drop_fd(fd)  # 切断
                    continue
                for off in range(0, len(data) - EVENT_SIZE + 1, EVENT_SIZE):
                    _, _, etype, code, value = struct.unpack(
                        EVENT_FMT, data[off:off + EVENT_SIZE])
                    if etype != EV_KEY:
                        continue
                    if code in (KEY_LEFTCTRL, KEY_RIGHTCTRL):
                        ctrl_held = value != 0
                    elif code == KEY_C and value == 1 and ctrl_held and mode == "map":
                        t = time.time()
                        if t - last_ctrl_c <= DOUBLE_WINDOW:
                            last_ctrl_c = 0.0
                            log("Ctrl+C x2 -> TERMINAL")
                            stop_map(child)
                            child = None
                            mode = "terminal"
                        else:
                            last_ctrl_c = t
    except KeyboardInterrupt:
        pass
    finally:
        stop_map(child)
        log("bye")


if __name__ == "__main__":
    main()
