"""Tests for the arming window after the wake word.

A person says "OK Trident", waits to see the deck acknowledge, then says the
place. The VAD splits that into two utterances, so the second one is accepted
only while the wake is still armed -- eight seconds.

The bug this covers: the deadline was compared against the moment the
*transcript* arrived, which put the recogniser's own thinking time inside the
window. Observed on pi5-deck, 2026-08-28, after the model changed from
ggml-base.bin to ggml-small-q8_0.bin for English:

    17:07:09  WAKE (armed 8s, awaiting place) 'OK, Trident.'
    17:07:12  ...the place is spoken here, three seconds later
    17:07:18  ---- s=0.20 'Show Hiroshima!'

The place was spoken well inside the window and transcribed perfectly. It was
thrown away because ASR took six seconds and the check ran afterwards, one
second past the deadline. With the faster model the same eight seconds had been
enough, so the window's size was never the problem -- what it was measured
against was.

The window is a claim about how long a person pauses. Measuring it on the
machine's clock ties it to whichever model is loaded, which means a model
change silently breaks speech that is recognised correctly. So it is judged on
when the audio was captured.
"""
import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "pi-hear"))

import pi_hear


class WithinArmWindow(unittest.TestCase):

    def test_speech_inside_the_window_counts(self):
        self.assertTrue(pi_hear.within_arm_window(105.0, 108.0))

    def test_speech_after_the_deadline_does_not(self):
        self.assertFalse(pi_hear.within_arm_window(109.0, 108.0))

    def test_a_slow_recogniser_does_not_close_the_window(self):
        # The case from the journal: armed until t=108, the place was spoken
        # (captured) at t=103, and the transcript did not arrive until t=112.
        # It is the capture time that decides.
        self.assertTrue(pi_hear.within_arm_window(103.0, 108.0))

    def test_an_unarmed_deck_accepts_nothing(self):
        # armed_until is 0.0 when disarmed, and every real capture time is
        # larger than that, so a plain `<` would arm the deck forever.
        self.assertFalse(pi_hear.within_arm_window(1_700_000_000.0, 0.0))

    def test_the_deadline_itself_is_too_late(self):
        self.assertFalse(pi_hear.within_arm_window(108.0, 108.0))

    def test_a_missing_capture_time_falls_back_to_now(self):
        # Callers that have no timestamp (a test harness, a future path that
        # does not come through the capture queue) must not be silently armed
        # or silently refused; None means "judge it as arriving now".
        self.assertTrue(pi_hear.within_arm_window(None, 108.0, now=105.0))
        self.assertFalse(pi_hear.within_arm_window(None, 108.0, now=109.0))


if __name__ == "__main__":
    unittest.main()
