"""Pluggable ASR engines for pi-hear.

The capture + VAD front-end produces one float32 mono utterance (samples in
[-1, 1]) per detected speech segment. An Engine turns that buffer into text.
Because the audio path is engine-agnostic, swapping recognisers is just
`--engine moonshine|whisper` — no change to capture/VAD.

  - MoonshineEngine:  moonshine_voice base-ja, resamples internally (C API).
  - WhisperCppEngine: shells out to whisper.cpp's whisper-cli on a 16 kHz WAV;
                      supports an initial --prompt to bias toward domain words
                      like トライデント (moonshine has no such biasing).
"""
import os
import subprocess
import tempfile
import wave

import numpy as np


def to_16k_mono_int16(samples, sr):
    """Resample float32 [-1,1] mono to 16 kHz little-endian int16.

    Linear interpolation (no anti-alias filter). Speech energy is mostly
    <4 kHz so 48k→16k aliasing is minor; good enough for ASR. whisper.cpp's
    WAV reader requires exactly 16 kHz mono, hence the conversion.
    """
    samples = np.asarray(samples, dtype=np.float32)
    if sr != 16000 and len(samples) > 1:
        n_out = int(round(len(samples) * 16000 / sr))
        if n_out > 0:
            x_old = np.linspace(0.0, 1.0, num=len(samples), endpoint=False)
            x_new = np.linspace(0.0, 1.0, num=n_out, endpoint=False)
            samples = np.interp(x_new, x_old, samples).astype(np.float32)
    return (np.clip(samples, -1.0, 1.0) * 32767.0).astype("<i2")


class Engine:
    """Interface: transcribe one mono float32 utterance to a string."""
    name = "base"

    def transcribe(self, samples, sample_rate):
        raise NotImplementedError

    def close(self):
        pass


class MoonshineEngine(Engine):
    name = "moonshine"

    def __init__(self, language="ja"):
        from moonshine_voice import get_model_for_language
        from moonshine_voice.transcriber import Transcriber
        model_path, model_arch = get_model_for_language(wanted_language=language)
        self._t = Transcriber(model_path, model_arch)

    def transcribe(self, samples, sample_rate):
        # moonshine resamples to its internal 16 kHz itself.
        tr = self._t.transcribe_without_streaming(
            np.asarray(samples, dtype=np.float32).tolist(), sample_rate)
        return " ".join(line.text for line in tr.lines).strip()

    def close(self):
        self._t.close()


class WhisperCppEngine(Engine):
    name = "whisper"

    def __init__(self, binary, model, language="ja", prompt=None, threads=4,
                 audio_ctx=512, beam_size=1):
        if not os.path.exists(binary):
            raise FileNotFoundError(f"whisper binary not found: {binary}")
        if not os.path.exists(model):
            raise FileNotFoundError(f"whisper model not found: {model}")
        self.binary = binary
        self.model = model
        self.language = language
        self.prompt = prompt
        self.threads = threads
        # audio_ctx caps the encoder context (1500 ≈ 30s). For short utterances
        # 512 (~10s) cuts compute ~3x AND avoids the decoder hallucinating from
        # the silent tail of the padded 30s window (verified on a Pi 4: tiny
        # went 5.4s/garbage → 1.8s/correct). beam_size=1 = greedy. See
        # whisper.cpp discussions/166.
        self.audio_ctx = audio_ctx
        self.beam_size = beam_size

    def transcribe(self, samples, sample_rate):
        pcm = to_16k_mono_int16(samples, sample_rate)
        fd, wav_path = tempfile.mkstemp(suffix=".wav", dir="/dev/shm")
        os.close(fd)
        try:
            with wave.open(wav_path, "wb") as w:
                w.setnchannels(1)
                w.setsampwidth(2)
                w.setframerate(16000)
                w.writeframes(pcm.tobytes())
            cmd = [self.binary, "-m", self.model, "-f", wav_path,
                   "-l", self.language, "-nt", "-np", "-t", str(self.threads),
                   "-ac", str(self.audio_ctx), "-bs", str(self.beam_size)]
            if self.prompt:
                cmd += ["--prompt", self.prompt]
            out = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
            # -nt -np prints just the transcript; collapse whitespace/newlines.
            return " ".join(out.stdout.split()).strip()
        finally:
            try:
                os.unlink(wav_path)
            except OSError:
                pass


def build_engine(args):
    """Construct the engine selected by parsed args (see pi_hear.py)."""
    if args.engine == "whisper":
        return WhisperCppEngine(
            binary=args.whisper_bin,
            model=args.whisper_model,
            language=args.language,
            prompt=(args.whisper_prompt or None),
            threads=args.threads,
            audio_ctx=args.whisper_ac,
            beam_size=args.whisper_bs,
        )
    return MoonshineEngine(language=args.language)
