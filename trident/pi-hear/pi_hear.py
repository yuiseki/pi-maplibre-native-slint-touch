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
import re
import queue
import subprocess
import sys
import threading
import wave
import time

import numpy as np
import sounddevice as sd

import hear_state
import wake as wakelib
import engines as enginelib
import romaji_match
import intent as intent_mod


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


def make_alsa_reader(proc, audio_q, stop, blocksize, on_death=None):
    """Feed audio_q from arecord's stdout, and say so when the stream ends.

    Plugging in a USB audio adapter re-enumerates the bus, and arecord loses
    its stream: "pcm_read: read error: No such device". This loop used to break
    and the thread simply ended -- nothing else noticed. pi-hear stayed up,
    systemd reported active, Restart=always never fired, and the deck listened
    to silence for half an hour until the greyed-out microphone icon on the map
    gave it away.

    Reporting healthy while deaf is the worst failure available here, so the
    end of the stream is reported. `stop` being set is not a death: shutting
    down is not the microphone failing, and treating it as one would put a
    fault in the journal on every clean exit.
    """
    nbytes = blocksize * 2                          # int16 mono

    def reader():
        while not stop.is_set():
            buf = proc.stdout.read(nbytes)
            if not buf or len(buf) < nbytes:
                if not stop.is_set() and on_death is not None:
                    on_death("capture ended: read %d of %d bytes (rc=%s)"
                             % (len(buf) if buf else 0, nbytes, proc.poll()))
                return
            audio_q.put(
                np.frombuffer(buf, dtype="<i2").astype(np.float32) / 32768.0)

    return reader



def capture_card_names(pcm_text, cards_text):
    """ALSA card names that can actually record.

    Two files, because neither answers on its own: /proc/asound/pcm says which
    card numbers have a capture stream, and /proc/asound/cards maps a number to
    the name that plughw:CARD= wants. Card numbers move when USB enumerates in a
    different order; the names do not, which is why the config uses them.
    """
    nums = set()
    for line in (pcm_text or "").splitlines():
        if "capture" not in line:
            continue
        head = line.split(":", 1)[0].strip()
        try:
            nums.add(int(head.split("-")[0]))
        except ValueError:
            continue
    out = []
    for line in (cards_text or "").splitlines():
        m = re.match(r"\s*(\d+)\s*\[([^\]]+)\]", line)
        if m and int(m.group(1)) in nums:
            out.append(m.group(2).strip())
    return out


def read_capture_cards(pcm="/proc/asound/pcm", cards="/proc/asound/cards"):
    def read(path):
        try:
            with open(path, encoding="utf-8", errors="replace") as fh:
                return fh.read()
        except OSError:
            return ""
    return capture_card_names(read(pcm), read(cards))


def pick_alsa_device(spec, available):
    """The first PCM in a "|"-separated preference list that can record.

    "|" rather than "," because a PCM name already contains commas
    (plughw:CARD=MINI,DEV=0).

    An entry that names no card is returned unchecked: a bluealsa PCM is not a
    card and trying it is the only way to find out, and refusing would break the
    case --alsa-device was added for.

    An attached mic that nobody listed is used rather than nothing. Naming a mic
    that is absent is not a quiet degradation -- arecord exits, pi-hear exits
    with it, and systemd restarts the pair forever -- so the fallback is what
    keeps a newly-bought device working without an edit.
    """
    entries = [e.strip() for e in (spec or "").split("|") if e.strip()]
    for entry in entries:
        m = re.search(r"CARD=([^,\s]+)", entry)
        if not m:
            return entry
        if m.group(1) in available:
            return entry
    if available:
        return "plughw:CARD=%s,DEV=0" % available[0]
    return None


def main():
    ap = argparse.ArgumentParser(
        description="pi-hear: Japanese speech → text (VAD-segmented, pluggable ASR)"
    )
    ap.add_argument("--engine", default="moonshine",
                    choices=["moonshine", "whisper"],
                    help="ASR engine (default: moonshine)")
    # Which language the recogniser decodes. Fixed rather than detected: on
    # this machine `-l auto` costs a flat ~3.2s against ~1.2s for a language
    # that is simply told, and it decides wrongly often enough to matter (a
    # Japanese-accented English utterance came back as Chinese). Everyday use
    # is Japanese; a demo in English is a service restart, not a rebuild.
    ap.add_argument("--language", default=os.environ.get("PI_HEAR_LANG", "ja"),
                    help="whisper decode language: ja (default), en, or auto "
                         "(auto is ~2.6x slower and can pick wrong). Also "
                         "settable as PI_HEAR_LANG, e.g. from "
                         "/etc/default/pi-hear")
    ap.add_argument(
        "--device",
        default="DJI",
        help="sounddevice input name substring (default: DJI), or 'default'",
    )
    ap.add_argument(
        "--alsa-device", default=None,
        help="capture via arecord from this ALSA PCM instead of sounddevice "
             "(e.g. 'bluealsa:DEV=20:74:CF:D2:A3:84,PROFILE=sco' for a BT mic "
             "that PortAudio can't enumerate). Implies S16_LE mono at "
             "--samplerate. Several may be given in preference order separated "
             "by '|' -- the first whose card can record is used, so the deck "
             "works with whichever mic happens to be plugged in",
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
    ap.add_argument("--whisper-ac", type=int, default=0,
                    help="whisper encoder audio context (0 = the whole 30s "
                         "window, the default; 512≈10s is ~3x faster and was "
                         "what a Pi 4 running tiny needed). On a Pi 5 running "
                         "base, 512 got 0 of 6 real utterances right and the "
                         "full window got 4 -- see engines.py")
    ap.add_argument("--whisper-bs", type=int, default=5,
                    help="whisper beam size (1 = greedy and ~10%% faster, but "
                         "it is what turned 'show hotels' into 'show hot abs')")
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
    # Empty by default, and empty means "do not pass --device at all".
    #
    # pi-say already knows which speaker this machine has: /etc/default/pi-say
    # holds PI_SAY_DEVICE because, as its own docstring puts it, what the
    # speaker needs is a property of the speaker rather than of every caller.
    # Defaulting this to plughw:0,0 overrode that from here, and on a deck whose
    # 3.5mm jack is card 0 = HDMI the confirmations went to a socket with
    # nothing plugged into it: the map flew to Okinawa in silence while
    # `pi-say "..."` typed by hand was perfectly audible.
    #
    # Pass a device here only to override the machine's own setting -- a
    # Bluetooth headset for one session, say.
    ap.add_argument("--say-device", default="",
                    help="ALSA device for confirmations. Empty (the default) "
                         "leaves the choice to pi-say and /etc/default/pi-say, "
                         "which is where this machine's speaker is described")
    ap.add_argument("--mute-file", default="/tmp/pi-hear/mute",
                    help="while this file exists, drop all audio (half-duplex: "
                         "pi-say creates it during playback so we don't self-hear)")
    ap.add_argument("--record-dir",
                    default=os.environ.get("PI_HEAR_RECORD_DIR", ""),
                    help="save every utterance here as a wav, so two "
                         "recognisers can be compared on the same audio. "
                         "Off by default: this is a microphone in someone's "
                         "room. Defaults from PI_HEAR_RECORD_DIR so it can be "
                         "turned on for a session without editing the unit's "
                         "ExecStart")
    ap.add_argument("--saver-file", default="/dev/shm/pi-saver-stage",
                    help="map writes its screensaver stage here (0=active, >=1 idle)")
    ap.add_argument("--saver-pause-stage", type=int, default=1,
                    help="pause listening while saver stage >= this (idle is "
                         "touch-to-wake, not voice-wake; 0 disables)")
    ap.add_argument("--level-file", default="/dev/shm/pi-hear-level",
                    help="publish the current input level (0..1) here, for the "
                         "map's waveform; empty string disables")
    ap.add_argument("--state-file", default="/dev/shm/pi-hear-state",
                    help="publish what we are doing here (listening / asr / "
                         "muted / paused) so the map can show a mic indicator; "
                         "empty string disables")
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
            publish_state("speaking", text, hold=25.0, override=True)
            cmd = ["/usr/local/bin/pi-say"]
            if args.say_device:
                cmd += ["--device", args.say_device]
            subprocess.run(cmd + [text], timeout=20)
        except Exception as e:
            print(f"[act] pi-say error: {e}", file=sys.stderr)
        finally:
            time.sleep(0.8)  # let the speaker tail pass before un-muting
            try:
                os.remove(args.mute_file)
            except OSError:
                pass
            # Only now release the hold: doing it before the mute file goes
            # lets the capture loop publish a stray "muted" in between.
            publish_state("listening", override=True)

    # Publish what we are doing, for the map's caption strip and mic icon.
    # The precedence rules live in hear_state.StatePublisher, which is tested;
    # they are fiddly enough that getting them wrong is invisible until you
    # watch the screen (recognition flickered, the transcription never showed).
    # The map captions in whichever language we are listening in; it reads
    # this from the state file rather than the config, so changing the language
    # is one restart rather than two that must agree.
    _pub = hear_state.StatePublisher(args.state_file, lang=args.language)
    _level = hear_state.LevelPublisher(args.level_file, args.threshold)

    def publish_state(word, text="", hold=0.0, override=False):
        _pub.publish(word, text, hold=hold, override=override)

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
        key, spoken_ja, spoken_en, dist = place
        # Answer in whatever language was spoken to us. `text` is what came
        # back from the recogniser, so its script is the honest signal -- no
        # need to ask whisper which language it decided on.
        lang = romaji_match.reply_language(text)
        print(f"WAKE -> flyto {key} [{lang}]  '{text}'", flush=True)
        say_muted(romaji_match.confirmation(lang, spoken_ja, spoken_en))
        # Same reason as in do_intent: pins are tied to a place, and this is a
        # different place. The nine-city table goes through here, not there.
        subprocess.run(["/usr/local/bin/pi-poi", "clear"],
                       capture_output=True, timeout=20)
        subprocess.run(["/usr/local/bin/pi-flyto", key], timeout=10)

    def do_intent(text):
        """Handle what the place table could not. True if something was done."""
        # The current language is what makes「ゲンゴモード」without a readable
        # language name interpretable; see intent._other_language.
        plan = intent_mod.for_voice(text, lang=args.language)
        if plan is None:
            return False
        lang = romaji_match.reply_language(text)
        what = plan["intent"]
        print(f"WAKE -> {plan['tool']} {plan['args']} [{lang}]  '{text}'",
              flush=True)
        if plan.get("speak_first"):
            # The tool about to run restarts this process, so anything said
            # afterwards is never said. Speak now, and in the language being
            # switched to -- the last thing heard should match what the deck is
            # about to expect.
            say_muted(plan["say"])
        if plan.get("clear_pins"):
            # Whatever is pinned belongs to where the map is now, not to where
            # it is going.
            subprocess.run(["/usr/local/bin/pi-poi", "clear"],
                           capture_output=True, timeout=20)
        if what["intent"] == "show_place":
            say_muted("承知しました。" if lang == "ja" else "Understood.")
        failed = False
        try:
            r = subprocess.run(["/usr/local/bin/" + plan["tool"]] + plan["args"],
                               timeout=plan["timeout"])
            failed = r.returncode != 0
        except Exception as e:                      # noqa: BLE001
            # A slow Overpass or a missing tool must not take the loop down.
            print(f"[act] {plan['tool']}: {e}", file=sys.stderr)
            failed = True
        # Say so rather than going quiet. Not for speak_first plans: those
        # restart this process, so anything said here is never said, and the
        # thing that was promised has already been announced.
        if failed and not plan.get("speak_first"):
            say_muted(intent_mod.failure_reply(lang))
        return True

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
        # The place table holds nine cities and matches them anywhere in the
        # sentence, which is right for "show Hiroshima" and wrong for
        # 「広島駅にズームして」-- it finds 広島 and flies to the city, discarding
        # the half of the sentence that said which station. A sentence that
        # names something to zoom to goes to the intent rules whole.
        place = (None if intent_mod.is_zoom_request(text)
                 else romaji_match.find_place(text))
        armed = time.time() < armed_until[0]

        if matched and place:                 # wake + place in one breath
            armed_until[0] = 0.0
            do_flyto(place, text)
        elif matched:                          # wake only -> arm for the place
            armed_until[0] = time.time() + ARM_WINDOW
            # Dim the map and start the waveform: from here until the window
            # closes, the device is listening to this person rather than
            # merely running. Held for the window so the capture loop's
            # "listening" cannot take it back mid-sentence.
            publish_state("armed", "", hold=ARM_WINDOW, override=True)
            print(f"WAKE (armed {ARM_WINDOW:.0f}s, awaiting place) '{text}'",
                  flush=True)
        elif armed and place:                  # place arrived just after the wake
            armed_until[0] = 0.0
            do_flyto(place, text)
        elif (matched or armed) and do_intent(text):
            # The place table has nine cities in it and the planet has millions,
            # and "show cafes on map" is not a place at all. Anything the table
            # cannot answer gets one rule-based reading before being given up
            # on -- rules only, because the model costs about nine seconds and
            # this is the middle of someone's sentence.
            armed_until[0] = 0.0
        elif armed:                            # still armed, nothing understood
            print(f"---- (armed, nothing understood) '{text}'", flush=True)
        else:
            print(f"---- s={score:.2f} '{text}'", flush=True)

    def emit(text):
        if not text:
            return
        # Show the transcription even when it goes nowhere: on a device whose
        # screen freezes during recognition, "it heard this and did nothing" is
        # the one thing the user cannot otherwise tell from "it heard nothing".
        publish_state("heard", text, hold=3.0, override=True)
        if args.act and not args.no_wake:
            act(text)
        elif args.no_wake:
            print(text, flush=True)
        else:
            matched, score, _r = romaji_match.wake_match(text)
            tag = "WAKE" if matched else "----"
            print(f"{tag} s={score:.2f} '{text}'", flush=True)

    def _keep_utterance(directory, samples, rate):
        """Write one utterance to a wav, named by the clock. Never fatal."""
        try:
            os.makedirs(directory, exist_ok=True)
            path = os.path.join(
                directory, time.strftime("%Y%m%d-%H%M%S") + ".wav")
            pcm = np.clip(np.asarray(samples, dtype=np.float32), -1.0, 1.0)
            with wave.open(path, "wb") as fh:
                fh.setnchannels(1)
                fh.setsampwidth(2)
                fh.setframerate(int(rate))
                fh.writeframes((pcm * 32767.0).astype("<i2").tobytes())
        except Exception as exc:                        # noqa: BLE001
            # Recording is a debugging aid. Losing it must not cost the deck
            # its ears.
            print(f"[record] {exc}", file=sys.stderr)


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
            publish_state("asr", hold=120.0, override=True)
            # Keep the audio when asked to. Every comparison of one recogniser
            # against another so far has been argued from typed strings or from
            # TTS, and both lie: the tsukuyomi voice is not a person at a
            # microphone, and neither is a sentence somebody wrote down. This
            # is how the same utterance gets fed to two models.
            if args.record_dir:
                _keep_utterance(args.record_dir, samples, sr)
            try:
                text = engine.transcribe(samples, sr)
            finally:
                try:
                    os.remove("/dev/shm/pi-map-pause")
                except OSError:
                    pass
                publish_state("listening", override=True)
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
        available = read_capture_cards()
        chosen = pick_alsa_device(args.alsa_device, available)
        if chosen is None:
            print("pi-hear: no capture device at all (asked for %r); "
                  "exiting for systemd to retry" % args.alsa_device,
                  file=sys.stderr, flush=True)
            publish_state("down", "", hold=0.0, override=True)
            return 1
        if chosen != args.alsa_device:
            print("pi-hear: using %s (attached: %s)"
                  % (chosen, ", ".join(available) or "none"),
                  file=sys.stderr, flush=True)
        args.alsa_device = chosen
    if args.alsa_device:
        # arecord-based capture for ALSA PCMs PortAudio can't enumerate (e.g. a
        # bluealsa BT mic). A reader thread feeds audio_q exactly like the
        # sounddevice callback, so VAD/worker downstream are unchanged.
        arec = subprocess.Popen(
            ["arecord", "-q", "-D", args.alsa_device, "-f", "S16_LE",
             "-r", str(sr), "-c", "1", "-t", "raw"],
            stdout=subprocess.PIPE)

        def capture_died(why):
            # Exit rather than try to recover in place: the unit already has
            # Restart=always and RestartSec=2, and re-opening a device that has
            # just been re-enumerated is the sort of thing that half-works.
            # Dying honestly is the whole fix.
            print(f"pi-hear: {why}; exiting for systemd to restart",
                  file=sys.stderr, flush=True)
            publish_state("down", "", hold=0.0, override=True)
            os._exit(1)

        threading.Thread(
            target=make_alsa_reader(arec, audio_q, stop, args.blocksize,
                                    on_death=capture_died),
            daemon=True).start()
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
                    publish_state("muted")
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
                                publish_state("paused")
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
                publish_state("listening")
                if args.gain != 1.0:
                    chunk = np.clip(chunk * args.gain, -1.0, 1.0)
                rms = float(np.sqrt(np.mean(chunk ** 2)))
                _level.publish(rms)
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
