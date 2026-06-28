"""Offline check of the whisper.cpp engine: synth JP speech with piper, feed it
through WhisperCppEngine (which resamples 22.05k→16k and shells out), compare."""
import os
import subprocess
import sys
import time
import wave

import numpy as np

sys.path.insert(0, os.path.expanduser("~/src/pi-hear"))
import engines

WAV = "/dev/shm/whisper_test.wav"
PIPER = os.path.expanduser("~/piper-tts/piper/bin/piper")
BIN = os.path.expanduser("~/src/whisper.cpp/build/bin/whisper-cli")
MODEL = os.path.expanduser("~/src/whisper.cpp/models/ggml-small.bin")
PHRASES = ["OKトライデント", "OKトライデント、東京を表示して", "大阪に行きたい"]

env = dict(os.environ, LC_ALL="C.UTF-8", LANG="C.UTF-8")
eng = engines.WhisperCppEngine(BIN, MODEL, language="ja", prompt="トライデント", threads=4)

for text in PHRASES:
    subprocess.run([PIPER, "--model", "tsukuyomi", "--output_file", WAV,
                    "--text", text], env=env, check=True,
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    w = wave.open(WAV, "rb")
    sr, n = w.getframerate(), w.getnframes()
    samples = np.frombuffer(w.readframes(n), dtype=np.int16).astype(np.float32) / 32768.0
    w.close()
    t0 = time.time()
    out = eng.transcribe(samples, sr)
    dt = time.time() - t0
    print(f"say : {text}")
    print(f"hear: {out}   ({dt:.1f}s for {n / sr:.1f}s audio)")
    print()
