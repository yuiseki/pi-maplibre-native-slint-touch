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
  - MAP 中に Ctrl+C または Esc を 1.5 秒以内に2回 → TERMINAL
    (Esc があるのは CardKB2 に Ctrl キーが物理的に無いため。42キーのうち
     Ctrl は一つも無く、あの端末では Ctrl+C×2 は不可能。Esc は Fn+1。
     Enter は割り当てない: 地図操作中に押しがちで、誤ってコンソールに
     落ちるほうがショートカットが無いことより悪い)
  - TERMINAL の shell で `pi-maps` (= /tmp/pi-display/request に "map") → MAP

キーボードは生 evdev で読む(python3-evdev 不要)。USB は by-id の
*-event-kbd で拾えるが、Bluetooth(BLE)HID はその symlink を作らないので、
/proc/bus/input/devices の Handlers に "kbd" を持つ全デバイスの eventN も開く
(USB/BT どちらの Ctrl+C×2 も効くように)。Slint(libinput)はキーボードを
排他 grab しないので並行読取できる。
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
# The map publishes its screensaver stage here (0=active, >=1 idle). Ctrl+C x2
# is context-sensitive: while the screensaver is up the map wakes itself (its
# own Ctrl+C x2 watcher resets the idle clock), so we must NOT drop to the
# console; only when the live map is shown does Ctrl+C x2 mean "give me a shell".
SAVER_STAGE = "/dev/shm/pi-saver-stage"

# linux/input-event-codes.h
EV_KEY = 0x01
KEY_ESC = 1
KEY_ENTER = 28
KEY_LEFTCTRL, KEY_RIGHTCTRL, KEY_C = 29, 97, 46
# struct input_event: timeval(long sec, long usec) + u16 type + u16 code + s32 value
EVENT_FMT = "llHHi"
EVENT_SIZE = struct.calcsize(EVENT_FMT)
DOUBLE_WINDOW = 1.5  # Ctrl+C 連続2回とみなす最大間隔 (秒)


class ExitGesture:
    """Ctrl+C x2 / Esc x2 の検出。

    キーごとに別の時計を持つ。混ぜて交互に押しても「2回」にはならない。
    成立したら対を消費するので、3回目は次の対の1回目として数える。
    """

    def __init__(self, window=1.5):
        self.window = window
        self.ctrl_held = False
        self.last = {}          # code -> 直前の押下時刻

    def feed(self, code, value, now):
        """1 イベントを与える。成立したらジェスチャ名(真値)、でなければ False。"""
        if code in (KEY_LEFTCTRL, KEY_RIGHTCTRL):
            self.ctrl_held = value != 0
            return False
        # value 1 = 押下のみ。2 は自動リピートで、押しっぱなしが勝手に
        # 成立してしまうため数えない。0 は離した瞬間。
        if value != 1:
            return False
        if code == KEY_C and self.ctrl_held:
            name = "Ctrl+C x2"
        elif code == KEY_ESC:
            name = "Esc x2"
        else:
            return False
        prev = self.last.get(code, 0.0)
        if prev and now - prev <= self.window:
            self.last[code] = 0.0
            return name
        self.last[code] = now
        return False


def log(*a):
    print("[supervisor]", *a, flush=True)


def saver_active():
    """True while the map's screensaver is up (stage >= 1)."""
    try:
        with open(SAVER_STAGE) as f:
            return int(f.read().strip() or "0") >= 1
    except (OSError, ValueError):
        return False


def keyboard_event_nodes():
    """All keyboard event devices, incl. Bluetooth (which lack a by-id
    *-event-kbd symlink). Combines the stable by-id symlinks with a parse of
    /proc/bus/input/devices for any device whose Handlers include 'kbd'.
    Returns canonical /dev/input/eventN paths (deduped, so a device reachable
    via both by-id and proc is opened once)."""
    nodes = set()
    for p in glob.glob("/dev/input/by-id/*-event-kbd"):
        nodes.add(os.path.realpath(p))
    try:
        with open("/proc/bus/input/devices") as f:
            blocks, block = [], []
            for line in f:
                if line.strip():
                    block.append(line)
                else:
                    blocks.append(block)
                    block = []
            blocks.append(block)
        for b in blocks:
            handlers = []
            for line in b:
                if line.startswith("H: Handlers="):
                    handlers = line.split("=", 1)[1].split()
                    break
            if "kbd" in handlers:
                for tok in handlers:
                    if tok.startswith("event"):
                        nodes.add("/dev/input/" + tok)
                        break
    except OSError:
        pass
    return nodes


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
        for dev in keyboard_event_nodes():
            if dev not in kbds:
                try:
                    kbds[dev] = os.open(dev, os.O_RDONLY | os.O_NONBLOCK)
                    log("keyboard:", dev)
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
    gesture = ExitGesture(DOUBLE_WINDOW)
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

            # --- キーボード読取 (脱出ジェスチャは MAP 中のみ作用) ---
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
                    fired = gesture.feed(code, value, time.time())
                    if not fired or mode != "map":
                        continue
                    if saver_active():
                        # Screensaver up: the map's own watcher resets its idle
                        # clock and wakes the live map. Don't drop to the
                        # console here -- the first gesture means "come back".
                        log("%s -> WAKE (screensaver; map handles)" % fired)
                    else:
                        log("%s -> TERMINAL" % fired)
                        stop_map(child)
                        child = None
                        mode = "terminal"
    except KeyboardInterrupt:
        pass
    finally:
        stop_map(child)
        log("bye")


if __name__ == "__main__":
    main()
