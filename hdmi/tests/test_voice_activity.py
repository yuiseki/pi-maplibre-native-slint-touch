#!/usr/bin/env python3
"""Tests for which pi-hear states hold the screensaver off.

The idle clock was touch-only on purpose -- waking a black screen on a stray
noise is worse than not waking it -- but deferring was missing with it, so a
conversation with the map could be interrupted by the saver mid-sentence. That
is worse than an ordinary blank: pi-hear pauses itself once the saver is up, so
the deck stops listening exactly when it is being spoken to, and only a touch
brings it back.

The whole rule is which words count, and getting it wrong is silent in both
directions: too few and the saver still interrupts, too many -- "listening"
above all -- and it never arrives at all.
"""
import subprocess
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
SRC = HERE.parent / "src" / "voice_activity.cpp"
HARNESS = HERE / "voice_activity_main.cpp"


def build():
    exe = Path(tempfile.mkdtemp()) / "voice_activity_test"
    r = subprocess.run(
        ["g++", "-std=c++20", "-O0", "-I", str(SRC.parent),
         str(SRC), str(HARNESS), "-o", str(exe)],
        capture_output=True, text=True)
    if r.returncode != 0:
        raise AssertionError("harness build failed:\n" + r.stderr)
    return exe


class VoiceActivity(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.exe = build()

    def active(self, *words):
        r = subprocess.run([str(self.exe), *words], capture_output=True,
                           text=True, timeout=30)
        self.assertEqual(r.returncode, 0, r.stderr)
        return [line == "1" for line in r.stdout.split()]

    def test_a_conversation_holds_the_saver_off(self):
        """Every state pi-hear passes through between the wake word and its
        answer. Miss one and the saver can land in the gap."""
        self.assertEqual(self.active("armed", "heard", "asr", "speaking"),
                         [True, True, True, True])

    def test_resting_does_not(self):
        """"listening" is present whenever pi-hear is alive. Counting it would
        mean the screensaver never arrives, which is not a subtle bug but it is
        an easy one to write."""
        self.assertEqual(self.active("listening"), [False])

    def test_not_in_a_conversation_does_not(self):
        self.assertEqual(self.active("muted", "paused", "down"),
                         [False, False, False])

    def test_an_unknown_word_does_not(self):
        """A new state added to pi-hear must not silently disable the saver;
        it should have to be listed here on purpose."""
        self.assertEqual(self.active("", "thinking", "ARMED", "armed "),
                         [False, False, False, False])


if __name__ == "__main__":
    unittest.main()
