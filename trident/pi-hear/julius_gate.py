"""Deciding whether an utterance is worth a whisper call.

The deck spends most of its day listening to a room that is not talking to
it. Every utterance the VAD cuts out goes through whisper, and the wake word
is then looked for in the text. That is expensive and it is lossy: whisper
writes the wake word as "OK トライドエンド" or "OK、とりあえず" often enough
that matching on text loses a third of them.

Julius with a grammar matches the sound instead. The grammar holds the wake
word and a loop over every phone, so a wake word only wins when it fits the
audio better than arbitrary phones do -- without that loop the grammar has
nothing else to say and answers "wake word" to everything.

Measured on 92 recordings the deck saved on 2026-08-23 (pi5-deck, 4 threads):

    method                       found   false   RTF
    Julius grammar               36/42     0     0.107
    wake word in whisper's text  26/42     0     0.35

This is a gate, not a recogniser: the command that follows the wake word
("show Hiroshima") is not in the grammar and never will be, so whisper still
runs -- just only when someone is talking to the deck.
"""
import os
import subprocess
import tempfile
import wave

from engines import to_16k_mono_int16

# The word as it is written in wake.voca. Julius echoes the first column of
# the entry it recognised, so this is what a detection looks like.
WAKE_TOKEN = "WAKE"


def wake_in_output(text):
    """True when Julius's recognised sentence contains the wake word.

    Only `sentence1:` counts. The word also appears in the grammar dump and
    in warnings, and matching anywhere in the output would fire at startup.
    """
    for line in (text or "").splitlines():
        line = line.strip()
        if not line.startswith("sentence1:"):
            continue
        if WAKE_TOKEN in line.split()[1:]:
            return True
    return False


def needs_gate(has_gate, armed):
    """Whether Julius should be asked about this utterance at all.

    An armed deck is waiting for the command, which is not in the grammar.
    Asking would cost 0.24s to be told "no wake word" about the sentence the
    user woke the deck to say.
    """
    return bool(has_gate) and not armed


def should_transcribe(has_gate, armed, heard_wake):
    """Whether this utterance is worth a whisper call."""
    if not needs_gate(has_gate, armed):
        return True
    return bool(heard_wake)


def build_command(binary, hmm, hlist, dfa, dictionary, list_path):
    """The julius invocation, with every model named by absolute path.

    The grammar kit's jconf names its model relative to the kit directory,
    so `-C hmm_ptm.jconf` only works from inside it. pi-hear runs from
    wherever systemd starts it, so the parts are passed directly.

    The audio arrives as a one-line file list. A waveform given as a bare
    argument is refused: julius prints "Try `-help'" and exits 255.
    """
    return [binary,
            "-h", hmm,
            "-hlist", hlist,
            "-dfa", dfa,
            "-v", dictionary,
            "-input", "rawfile",
            "-filelist", list_path,
            "-nostrip"]


def decide(returncode, stdout, stderr):
    """The gate's answer, or an error when there wasn't one.

    "The decoder would not start" and "nobody said the wake word" are the
    same empty output, and only one of them should keep the deck quiet.
    """
    if returncode != 0:
        detail = (stderr or stdout or "").strip().splitlines()
        raise RuntimeError(
            "julius exited %d: %s" % (returncode, detail[-1] if detail else "no output"))
    return wake_in_output(stdout)


class JuliusGate:
    """Ask Julius whether this utterance contains the wake word."""

    name = "julius"

    def __init__(self, binary, hmm, hlist, dfa, dictionary, timeout=10.0):
        # Fail here rather than at the first utterance. A gate that cannot
        # run must not be read as "nobody said anything": that produces a
        # deck which never answers and a log that looks like a quiet room.
        for label, path in (("julius binary", binary), ("acoustic model", hmm),
                            ("hmmlist", hlist), ("grammar dfa", dfa),
                            ("grammar dict", dictionary)):
            if not os.path.exists(path):
                raise FileNotFoundError(f"{label} not found: {path}")
        self.binary = binary
        self.hmm = hmm
        self.hlist = hlist
        self.dfa = dfa
        self.dictionary = dictionary
        self.timeout = timeout

    def hears_wake(self, samples, sample_rate):
        """True when the wake word is in this buffer.

        A decoder that fails to run raises, for the reason in __init__.
        """
        pcm = to_16k_mono_int16(samples, sample_rate)
        fd, wav_path = tempfile.mkstemp(suffix=".wav", dir="/dev/shm")
        os.close(fd)
        try:
            with wave.open(wav_path, "wb") as w:
                w.setnchannels(1)
                w.setsampwidth(2)
                w.setframerate(16000)
                w.writeframes(pcm.tobytes())
            list_path = wav_path + ".list"
            with open(list_path, "w") as fh:
                fh.write(wav_path + "\n")
            out = subprocess.run(
                build_command(self.binary, self.hmm, self.hlist, self.dfa,
                              self.dictionary, list_path),
                capture_output=True, text=True, timeout=self.timeout)
            return decide(out.returncode, out.stdout, out.stderr)
        finally:
            for path in (wav_path, wav_path + ".list"):
                try:
                    os.unlink(path)
                except OSError:
                    pass
