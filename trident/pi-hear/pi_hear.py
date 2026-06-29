#!/usr/bin/env python3
"""pi-hear: listen to the DJI mic and transcribe Japanese speech.

The hearing counterpart to pi-say (piper-plus TTS). Captures the DJI MIC MINI
via sounddevice, finds utterance boundaries with an energy VAD, and hands each
utterance to a pluggable ASR engine (moonshine or whisper.cpp).

Why VAD-segmented (not streaming): the Pi 4 CPU can't keep up with continuous
streaming inference, so it drops the back half of anything longer than a word.
Instead a lightweight callback just queues raw frames; an RMS VAD finds
utterance boundaries; and we fire ONE transcription per utterance once the
speaker pauses. Far fewer inferences → the Pi 4 keeps up and we get the whole
phrase. The audio path is engine-agnostic, so --engine swaps the recogniser
without touching capture/VAD.

Wake word: each finished utterance is matched (fuzzy) against トライデント.
Reference: yuiseki/ahear. No PulseAudio here, so capture is pure ALSA.
"""
import argparse
import collections
import contextlib
import os
import queue
import subprocess
import sys
import threading
import time

import numpy as np
import sounddevice as sd

import wake as wakelib
import engines as enginelib
import romaji_match


def find_input_device(name_hint):
    """Index of the first input device whose name contains name_hint.

    Returns None to let PortAudio pick the default input. Matching by name
    (not a fixed index) keeps us robust when USB enumeration order shifts.
    """
    if name_hint is None:
        return None
    hint = name_hint.lower()
    for i, d in enumerate(sd.query_devices()):
        if d["max_input_channels"] > 0 and hint in d["name"].lower():
            return i
    return None


# Place table for --act mode: ASR-text substring -> (pi-flyto key, spoken name).
# A deliberately small, reliable matcher for the voice demo; the eventual
# llama-server "brain" can replace this with general intent resolution.
PLACES = {
    "東京": ("tokyo", "東京"),
    "広島": ("hiroshima", "広島"),
    "大阪": ("osaka", "大阪"),
    "京都": ("kyoto", "京都"),
    "札幌": ("sapporo", "札幌"),
    "福岡": ("fukuoka", "福岡"),
    "那覇": ("naha", "那覇"),
    "沖縄": ("naha", "沖縄"),
    # whisper-base mishears 沖縄 (okinawa) as お気なお/お気なあ/おきなわ
    "お気な": ("naha", "沖縄"),
    "おきなわ": ("naha", "沖縄"),
    "オキナワ": ("naha", "沖縄"),
}


def find_place(text):
    """Longest place name appearing in the (normalised) text, or None."""
    n = wakelib.normalize(text)
    best = None
    for name, (key, spoken) in PLACES.items():
        if name in n and (best is None or len(name) > len(best[0])):
            best = (name, key, spoken)
    return best


def main():
    ap = argparse.ArgumentParser(
        description="pi-hear: Japanese speech → text (VAD-segmented, pluggable ASR)"
    )
    ap.add_argument("--engine", default="moonshine",
                    choices=["moonshine", "whisper"],
                    help="ASR engine (default: moonshine)")
    ap.add_argument("--language", default="ja")
    ap.add_argument(
        "--device",
        default="DJI",
        help="sounddevice input name substring (default: DJI), or 'default'",
    )
    ap.add_argument(
        "--alsa-device", default=None,
        help="capture via arecord from this ALSA PCM instead of sounddevice "
             "(e.g. 'bluealsa:DEV=20:74:CF:D2:A3:84,PROFILE=sco' for a BT mic "
             "that PortAudio can't enumerate). Implies S16_LE mono at --samplerate.",
    )
    ap.add_argument("--samplerate", type=int, default=48000,
                    help="capture rate; engines resample to 16k as needed")
    ap.add_argument("--blocksize", type=int, default=2048,
                    help="frames per audio callback (~43ms at 48k); callback only "
                         "queues, so this just sets VAD time resolution")
    ap.add_argument("--gain", type=float, default=1.0,
                    help="software gain applied to captured audio. PortAudio can "
                         "capture some USB mics (e.g. Razer Seiren) ~3x quieter "
                         "than arecord; ~3.5 restores a healthy level")
    ap.add_argument("--threshold", type=float, default=0.015,
                    help="RMS level above which a block counts as speech")
    ap.add_argument("--silence", type=float, default=0.7,
                    help="seconds of silence that ends an utterance")
    ap.add_argument("--min-speech", type=float, default=0.3,
                    help="ignore utterances shorter than this many seconds")
    ap.add_argument("--max-speech", type=float, default=15.0,
                    help="force-flush an utterance after this many seconds")
    ap.add_argument("--preroll", type=float, default=0.5,
                    help="seconds of audio kept before speech onset (anti-clip)")
    ap.add_argument("--debug", action="store_true",
                    help="print VAD state and per-utterance timing to stderr")
    # whisper.cpp backend
    ap.add_argument("--whisper-bin", default="/home/yuiseki/src/whisper.cpp/build/bin/whisper-cli",
                    help="path to whisper.cpp whisper-cli binary")
    ap.add_argument("--whisper-model", default="/home/yuiseki/src/whisper.cpp/models/ggml-tiny.bin",
                    help="path to a ggml whisper model (tiny is the viable one on Pi 4)")
    ap.add_argument("--whisper-prompt", default="トライデント",
                    help="initial prompt to bias whisper toward domain words "
                         "('' to disable)")
    ap.add_argument("--whisper-ac", type=int, default=512,
                    help="whisper encoder audio context (1500≈30s; 512≈10s is "
                         "~3x faster and avoids silence-hallucination on short clips)")
    ap.add_argument("--whisper-bs", type=int, default=1,
                    help="whisper beam size (1 = greedy, fastest)")
    ap.add_argument("--threads", type=int, default=4,
                    help="whisper.cpp inference threads (Pi 4 has 4 cores)")
    # wake word
    ap.add_argument("--wake-word", default=wakelib.DEFAULT_WAKE,
                    help="wake word to detect (default: トライデント)")
    ap.add_argument("--wake-core", default=wakelib.DEFAULT_CORE,
                    help="robust core substring that counts as a sure hit")
    ap.add_argument("--wake-threshold", type=float, default=wakelib.DEFAULT_THRESHOLD,
                    help="fuzzy-match ratio above which the wake word fires")
    ap.add_argument("--no-wake", action="store_true",
                    help="disable wake detection; just print transcriptions")
    ap.add_argument("--act", action="store_true",
                    help="on a wake-matched utterance, resolve a place name, "
                         "confirm via pi-say, and fly the map via pi-flyto")
    ap.add_argument("--say-device", default="plughw:0,0",
                    help="ALSA device pi-say uses for confirmations (default "
                         "3.5mm; use 'btspk' for the Bluetooth headset)")
    ap.add_argument("--mute-file", default="/tmp/pi-hear/mute",
                    help="while this file exists, drop all audio (half-duplex: "
                         "pi-say creates it during playback so we don't self-hear)")
    ap.add_argument("--saver-file", default="/dev/shm/pi-saver-stage",
                    help="map writes its screensaver stage here (0=active, >=1 idle)")
    ap.add_argument("--saver-pause-stage", type=int, default=1,
                    help="pause listening while saver stage >= this (idle is "
                         "touch-to-wake, not voice-wake; 0 disables)")
    args = ap.parse_args()

    dev = None if args.device == "default" else find_input_device(args.device)
    if args.device != "default" and dev is None:
        print(f"pi-hear: mic '{args.device}' not found; using default input",
              file=sys.stderr)

    print(f"pi-hear: loading engine '{args.engine}' ({args.language})…",
          file=sys.stderr, flush=True)
    engine = enginelib.build_engine(args)

    sr = args.samplerate
    block_dur = args.blocksize / sr
    preroll_blocks = max(1, int(args.preroll / block_dur))

    audio_q = queue.Queue()
    utt_q = queue.Queue()
    # whisper-base on the Pi 4 A72 can't keep up with continuous speech/noise, so
    # an unbounded utt_q grows to dozens of stale utterances (observed ~70) that
    # keep firing minutes-old commands long after they were spoken. For a voice
    # appliance only the most recent utterance matters, so cap the backlog and
    # drop the oldest when full.
    UTT_Q_MAX = 3
    stop = threading.Event()

    def callback(indata, frames, time_info, status):
        if status and args.debug:
            print(f"[audio status] {status}", file=sys.stderr)
        audio_q.put(indata[:, 0].copy())  # mono, copied out of reused buffer

    if args.mute_file:
        os.makedirs(os.path.dirname(args.mute_file), exist_ok=True)

    def say_muted(text):
        # Speak via pi-say while muting capture (half-duplex), so the
        # confirmation isn't transcribed back as input.
        try:
            open(args.mute_file, "w").close()
            subprocess.run(["/usr/local/bin/pi-say", "--device", args.say_device,
                            text], timeout=20)
        except Exception as e:
            print(f"[act] pi-say error: {e}", file=sys.stderr)
        finally:
            time.sleep(0.8)  # let the speaker tail pass before un-muting
            try:
                os.remove(args.mute_file)
            except OSError:
                pass

    def saver_active():
        # True while the map's screensaver is up (stage >= pause threshold).
        if args.saver_pause_stage <= 0:
            return False
        try:
            with open(args.saver_file) as _sf:
                return int(_sf.read().strip() or "0") >= args.saver_pause_stage
        except (OSError, ValueError):
            return False

    # After the wake word, accept the place from a *following* utterance for this
    # many seconds. Users naturally pause between "トライデント" and the place name,
    # which the VAD splits into two utterances; without this the wake-only segment
    # and the place-only segment each fail (one lacks a place, the other a wake).
    ARM_WINDOW = 8.0
    armed_until = [0.0]   # wall-clock; > now while armed (mutable cell for closure)

    def do_flyto(place, text):
        key, spoken, dist = place
        print(f"WAKE -> flyto {key}  '{text}'", flush=True)
        say_muted(f"承知しました。{spoken}を表示します。")
        subprocess.run(["/usr/local/bin/pi-flyto", key], timeout=10)

    def act(text):
        # Touch-to-wake, not voice-wake: while the screensaver is up, ignore
        # voice commands entirely. An utterance already in flight in the worker
        # can finish after the screensaver engages; acting on it would wake the
        # screen and contradict the touch-to-wake design.
        if saver_active():
            print(f"---- (saver up, ignored) '{text}'", flush=True)
            return
        # Romaji + edit-distance matching: collapses kanji/katakana/hiragana
        # mis-hearings (札幌/サッポロ, 沖縄/お気な, トライデント/トライ弦) by reading.
        matched, score, _r = romaji_match.wake_match(text)
        place = romaji_match.find_place(text)
        armed = time.time() < armed_until[0]

        if matched and place:                 # wake + place in one breath
            armed_until[0] = 0.0
            do_flyto(place, text)
        elif matched:                          # wake only -> arm for the place
            armed_until[0] = time.time() + ARM_WINDOW
            print(f"WAKE (armed {ARM_WINDOW:.0f}s, awaiting place) '{text}'",
                  flush=True)
        elif armed and place:                  # place arrived just after the wake
            armed_until[0] = 0.0
            do_flyto(place, text)
        elif armed:                            # still armed, no place yet
            print(f"---- (armed, no place yet) '{text}'", flush=True)
        else:
            print(f"---- s={score:.2f} '{text}'", flush=True)

    def emit(text):
        if not text:
            return
        if args.act and not args.no_wake:
            act(text)
        elif args.no_wake:
            print(text, flush=True)
        else:
            matched, score, _r = romaji_match.wake_match(text)
            tag = "WAKE" if matched else "----"
            print(f"{tag} s={score:.2f} '{text}'", flush=True)

    def transcribe_worker():
        # Transcription runs OFF the capture loop. The whisper engine takes
        # ~2 s/utterance; if that ran inline, the capture loop would stall and
        # backlog raw audio (incl. self-heard TTS), defeating the mute check.
        # Here the capture loop stays real-time and the mute decision is made
        # at capture time, so pi-say output is dropped before it ever queues.
        while not stop.is_set():
            try:
                item = utt_q.get(timeout=0.3)
            except queue.Empty:
                continue
            if item is None:
                break
            samples, dur, peak = item
            # Pause the map's heavy V3D render while we run the CPU-bound ASR, so
            # the recogniser gets the full CPU and responds faster. The map
            # (maplibre-slint-gl) watches this file and skips rendering while it
            # is fresh; we remove it as soon as transcription finishes.
            try:
                open("/dev/shm/pi-map-pause", "w").close()
            except OSError:
                pass
            try:
                text = engine.transcribe(samples, sr)
            finally:
                try:
                    os.remove("/dev/shm/pi-map-pause")
                except OSError:
                    pass
            if args.debug:
                print(f"[flush] dur={dur:.1f}s peak={peak:.4f} "
                      f"uttq={utt_q.qsize()} -> '{text}'",
                      file=sys.stderr, flush=True)
            emit(text)

    worker = threading.Thread(target=transcribe_worker, daemon=True)
    worker.start()

    preroll = collections.deque(maxlen=preroll_blocks)
    speech = []
    silence_dur = 0.0
    in_speech = False
    peak = 0.0

    arec = None
    if args.alsa_device:
        # arecord-based capture for ALSA PCMs PortAudio can't enumerate (e.g. a
        # bluealsa BT mic). A reader thread feeds audio_q exactly like the
        # sounddevice callback, so VAD/worker downstream are unchanged.
        arec = subprocess.Popen(
            ["arecord", "-q", "-D", args.alsa_device, "-f", "S16_LE",
             "-r", str(sr), "-c", "1", "-t", "raw"],
            stdout=subprocess.PIPE)

        def alsa_reader():
            nbytes = args.blocksize * 2  # int16 mono
            while not stop.is_set():
                buf = arec.stdout.read(nbytes)
                if not buf or len(buf) < nbytes:
                    break
                audio_q.put(
                    np.frombuffer(buf, dtype="<i2").astype(np.float32) / 32768.0)

        threading.Thread(target=alsa_reader, daemon=True).start()
        stream_ctx = contextlib.nullcontext()
        print(f"pi-hear: listening (engine={args.engine}, arecord "
              f"{args.alsa_device}, lang={args.language}, thr={args.threshold}); "
              f"Ctrl+C to stop", file=sys.stderr, flush=True)
    else:
        stream_ctx = sd.InputStream(
            samplerate=sr, blocksize=args.blocksize, device=dev,
            channels=1, dtype="float32", callback=callback,
        )
        print(f"pi-hear: listening (engine={args.engine}, device={dev}, "
              f"lang={args.language}, thr={args.threshold}); Ctrl+C to stop",
              file=sys.stderr, flush=True)

    with stream_ctx:
        try:
            while True:
                chunk = audio_q.get()
                # Half-duplex: while muted (pi-say is playing), drop audio and
                # reset VAD state so the speaker output is never transcribed.
                if args.mute_file and os.path.exists(args.mute_file):
                    in_speech = False
                    speech = []
                    preroll.clear()
                    silence_dur = 0.0
                    continue
                # Pause while the map's screensaver is up: idle is touch-to-wake,
                # not voice-wake (fewer false triggers, lower CPU). The map
                # publishes its stage to args.saver_file.
                if args.saver_pause_stage > 0:
                    try:
                        with open(args.saver_file) as _sf:
                            if int(_sf.read().strip() or "0") >= args.saver_pause_stage:
                                in_speech = False
                                speech = []
                                preroll.clear()
                                silence_dur = 0.0
                                # Also drop anything already queued: a backlog
                                # captured just before the screensaver engaged
                                # must not drain-fire commands (and wake the
                                # screen) while the device is meant to be idle.
                                while not utt_q.empty():
                                    try:
                                        utt_q.get_nowait()
                                    except queue.Empty:
                                        break
                                continue
                    except (OSError, ValueError):
                        pass
                if args.gain != 1.0:
                    chunk = np.clip(chunk * args.gain, -1.0, 1.0)
                rms = float(np.sqrt(np.mean(chunk ** 2)))
                voiced = rms >= args.threshold

                if not in_speech:
                    preroll.append(chunk)
                    if voiced:
                        in_speech = True
                        speech = list(preroll)
                        preroll.clear()
                        silence_dur = 0.0
                        peak = rms
                        if args.debug:
                            print(f"[onset] rms={rms:.4f} qlen={audio_q.qsize()}",
                                  file=sys.stderr, flush=True)
                    continue

                speech.append(chunk)
                peak = max(peak, rms)
                silence_dur = 0.0 if voiced else silence_dur + block_dur
                total_dur = sum(len(c) for c in speech) / sr

                if silence_dur >= args.silence or total_dur >= args.max_speech:
                    in_speech = False
                    samples = np.concatenate(speech)
                    speech = []
                    dur = len(samples) / sr
                    if dur < args.min_speech:
                        if args.debug:
                            print(f"[drop] dur={dur:.1f}s peak={peak:.4f} (short)",
                                  file=sys.stderr, flush=True)
                        continue
                    # hand the utterance to the worker; never block capture.
                    # Drop the oldest queued utterances when the worker has
                    # fallen behind, so the backlog can't snowball (see UTT_Q_MAX).
                    while utt_q.qsize() >= UTT_Q_MAX:
                        try:
                            utt_q.get_nowait()
                        except queue.Empty:
                            break
                    utt_q.put((samples, dur, peak))
        except KeyboardInterrupt:
            pass
        finally:
            stop.set()
            utt_q.put(None)
            if arec is not None:
                arec.terminate()
            engine.close()


if __name__ == "__main__":
    main()
