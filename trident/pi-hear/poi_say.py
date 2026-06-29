#!/usr/bin/env python3
"""pi-say-poi: speak a sentence "The Machine" style (Person of Interest) by
stitching per-word audio snippets, each word in a deterministic English voice.

Snippets are cached per (voice, rate, word), so piper only runs the first time
a word/rate is seen; later it is just file reads + concatenation. A leading beep
is The Machine's signature blip AND a Bluetooth SCO wake-up (so the first word
isn't clipped). Plays to --play (default btspk = the Aeropex BT headset).

  pi-say-poi --rate 1.5 --gap 0.22 --beep_s 0.5 --beep_hz 1000 "can you hear me?"
"""
import argparse
import hashlib
import os
import re
import subprocess
import sys
import wave

import numpy as np

PIPER = os.path.expanduser("~/piper-tts/piper/bin/piper")
MDIR = os.path.expanduser("~/piper-tts/en-models")
CACHE = os.path.expanduser("~/piper-tts/poi-cache")
SR = 16000
VOICES = ["en_US-ryan-low", "en_US-amy-low",
          "en_GB-alan-low", "en_GB-southern_english_female-low"]
os.makedirs(CACHE, exist_ok=True)


def beep(beep_s, beep_hz):
    t = np.arange(int(beep_s * SR)) / SR
    s = 0.35 * np.sin(2 * np.pi * beep_hz * t)
    f = min(int(0.012 * SR), len(s) // 2)
    if f:
        s[:f] *= np.linspace(0, 1, f)
        s[-f:] *= np.linspace(1, 0, f)
    return s


def voice_for(word):
    h = int(hashlib.md5(word.lower().encode()).hexdigest(), 16)
    return VOICES[h % len(VOICES)]


def cache_path(word, rate):
    safe = re.sub(r"[^a-z0-9]+", "_", word.lower()).strip("_") or "x"
    return os.path.join(CACHE, f"{voice_for(word)}__r{rate}__{safe}.wav")


def get_word(word, rate):
    """int16 samples (16 kHz) for a word; synthesise+cache if missing."""
    path = cache_path(word, rate)
    if not os.path.exists(path):
        voice = voice_for(word)
        tmp = "/dev/shm/poi_tmp.wav"
        env = dict(os.environ, LC_ALL="C.UTF-8", LANG="C.UTF-8")
        subprocess.run([PIPER, "--model", f"{MDIR}/{voice}.onnx",
                        "--length_scale", str(rate), "--text", word,
                        "--output_file", tmp], env=env, check=True,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        w = wave.open(tmp, "rb")
        sr, n = w.getframerate(), w.getnframes()
        s = np.frombuffer(w.readframes(n), dtype=np.int16).astype(np.float32) / 32768.0
        w.close()
        if sr != SR and len(s) > 1:
            no = int(round(len(s) * SR / sr))
            s = np.interp(np.linspace(0, 1, no, endpoint=False),
                          np.linspace(0, 1, len(s), endpoint=False), s)
        o = wave.open(path, "wb")
        o.setnchannels(1); o.setsampwidth(2); o.setframerate(SR)
        o.writeframes((np.clip(s, -1, 1) * 32767).astype("<i2").tobytes())
        o.close()
        print(f"  synth {word!r} <- {voice} @r{rate} (cached)", file=sys.stderr)
    w = wave.open(path, "rb")
    pcm = np.frombuffer(w.readframes(w.getnframes()), dtype="<i2")
    w.close()
    return pcm


def main():
    ap = argparse.ArgumentParser(description="POI 'The Machine' stitched TTS")
    ap.add_argument("text", nargs="?", default="can you hear me")
    ap.add_argument("--rate", type=float, default=1.25, help="piper length_scale")
    ap.add_argument("--gap", type=float, default=0.14, help="silence between words (s)")
    ap.add_argument("--beep_s", type=float, default=0.5, help="leading beep length (s)")
    ap.add_argument("--beep_hz", type=float, default=1000.0, help="beep frequency (Hz)")
    ap.add_argument("--play", default="btspk",
                    help="preferred ALSA device; falls back to --fallback ('' = none)")
    ap.add_argument("--fallback", default="plughw:0,0",
                    help="fallback ALSA device if --play fails (e.g. BT not connected)")
    ap.add_argument("--mute-file", default="/tmp/pi-hear/mute")
    args = ap.parse_args()

    words = args.text.split()
    gap16 = np.zeros(int(args.gap * SR), dtype="<i2")
    beep16 = (np.clip(beep(args.beep_s, args.beep_hz), -1, 1) * 32767).astype("<i2")
    parts = [beep16, np.zeros(int(0.1 * SR), dtype="<i2")]
    for word in words:
        parts.append(get_word(word, args.rate))
        parts.append(gap16)
    out = np.concatenate(parts)
    o = wave.open("/dev/shm/poi.wav", "wb")
    o.setnchannels(1); o.setsampwidth(2); o.setframerate(SR)
    o.writeframes(out.tobytes())
    o.close()
    print(f"poi.wav {len(out) / SR:.1f}s ({len(words)} words, "
          f"rate {args.rate}, gap {args.gap}, beep {args.beep_s}s/{args.beep_hz:.0f}Hz)")

    if args.play:
        os.makedirs(os.path.dirname(args.mute_file), exist_ok=True)
        open(args.mute_file, "w").close()        # half-duplex: don't self-hear
        # Try the preferred device (e.g. BT btspk, detectable: aplay fails fast
        # if not connected), then the fallback (3.5mm plughw:0,0, which has no
        # jack detection and always accepts audio = terminal fallback).
        devices = [d for d in (args.play, args.fallback) if d]
        played = False
        for d in devices:
            try:
                r = subprocess.run(["aplay", "-q", "-D", d, "/dev/shm/poi.wav"],
                                   timeout=30)
                if r.returncode == 0:
                    if d != devices[0]:
                        print(f"poi: {devices[0]} unavailable, played on {d}",
                              file=sys.stderr)
                    played = True
                    break
            except Exception as e:
                print(f"poi: {d} failed: {e}", file=sys.stderr)
        try:
            os.remove(args.mute_file)
        except OSError:
            pass
        if not played:
            print("poi: no audio device available", file=sys.stderr)


if __name__ == "__main__":
    main()
