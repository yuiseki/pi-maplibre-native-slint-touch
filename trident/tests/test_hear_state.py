#!/usr/bin/env python3
"""Tests for the state pi-hear publishes to the map's caption strip.

The rules here are the whole point of the module: the capture loop comes round
many times a second and publishes the least interesting state there is, so
without a notion of "this one stays up for a while" it overwrites everything
the worker says. Recognition showed for about half a second and the
transcription never appeared at all.
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "pi-hear"))

from hear_state import StatePublisher  # noqa: E402


class FakeClock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t

    def advance(self, dt):
        self.t += dt


def make(tmp):
    clock = FakeClock()
    return StatePublisher(str(tmp), clock=clock), clock


class Publishing(unittest.TestCase):
    def setUp(self):
        import tempfile
        self.path = Path(tempfile.mkdtemp()) / "state"
        self.pub, self.clock = make(self.path)

    def read(self):
        return self.path.read_text().split("\n")[:2]

    def test_writes_word_and_caption_as_two_lines(self):
        self.pub.publish("heard", "広島を表示して")
        self.assertEqual(self.read(), ["heard", "広島を表示して"])

    def test_caption_line_is_present_even_when_empty(self):
        # The map reads line 2 unconditionally; a missing line would make it
        # read whatever came before.
        self.pub.publish("listening")
        self.assertEqual(self.read(), ["listening", ""])

    def test_repeating_the_same_state_is_throttled(self):
        self.assertTrue(self.pub.publish("listening"))
        self.clock.advance(0.3)
        self.assertFalse(self.pub.publish("listening"))
        self.clock.advance(1.0)
        self.assertTrue(self.pub.publish("listening"))

    def test_a_different_state_is_never_throttled(self):
        self.pub.publish("listening")
        self.clock.advance(0.01)
        self.assertTrue(self.pub.publish("asr"))

    def test_disabled_when_no_path(self):
        pub = StatePublisher("", clock=FakeClock())
        self.assertFalse(pub.publish("listening"))


class Holds(unittest.TestCase):
    """A held state outranks the capture loop until it expires."""

    def setUp(self):
        import tempfile
        self.path = Path(tempfile.mkdtemp()) / "state"
        self.pub, self.clock = make(self.path)

    def word(self):
        return self.path.read_text().split("\n")[0]

    def test_capture_loop_cannot_overwrite_a_held_state(self):
        self.pub.publish("asr", hold=120.0, override=True)
        for _ in range(20):
            self.clock.advance(1.0)
            self.pub.publish("listening")
        self.assertEqual(self.word(), "asr")

    def test_hold_expires(self):
        self.pub.publish("heard", "広島", hold=3.0, override=True)
        self.clock.advance(2.0)
        self.pub.publish("listening")
        self.assertEqual(self.word(), "heard")
        self.clock.advance(1.5)
        self.pub.publish("listening")
        self.assertEqual(self.word(), "listening")

    def test_override_ends_a_hold_early(self):
        self.pub.publish("asr", hold=120.0, override=True)
        self.clock.advance(1.0)
        self.pub.publish("listening", override=True)
        self.assertEqual(self.word(), "listening")

    def test_a_new_hold_replaces_an_older_one(self):
        self.pub.publish("asr", hold=120.0, override=True)
        self.clock.advance(1.0)
        self.pub.publish("heard", "広島", hold=3.0, override=True)
        self.assertEqual(self.word(), "heard")
        self.clock.advance(3.5)
        self.pub.publish("listening")
        self.assertEqual(self.word(), "listening")

    def test_holding_does_not_block_the_state_that_set_it(self):
        # The worker refreshes "asr" while transcribing; that must not be
        # dropped as if it were the capture loop.
        self.pub.publish("asr", hold=120.0, override=True)
        self.clock.advance(2.0)
        self.assertTrue(self.pub.publish("asr", hold=120.0, override=True))


class RealSequence(unittest.TestCase):
    """The sequence one spoken command actually produces."""

    def setUp(self):
        import tempfile
        self.path = Path(tempfile.mkdtemp()) / "state"
        self.pub, self.clock = make(self.path)
        self.seen = []

    def loop(self, seconds):
        """The capture loop, ticking away in the background."""
        for _ in range(int(seconds * 4)):
            self.clock.advance(0.25)
            self.pub.publish("listening")
            self.seen.append(self.path.read_text().split("\n")[0])

    def test_recognising_then_result_then_reply_all_get_seen(self):
        self.loop(1)
        self.pub.publish("asr", hold=120.0, override=True)
        self.loop(2)                                     # transcribing
        self.pub.publish("heard", "広島を表示して", hold=3.0, override=True)
        self.loop(1)                                     # result on screen
        self.pub.publish("speaking", "承知しました。広島を表示します。",
                         hold=25.0, override=True)
        self.loop(3)                                     # speaking
        self.pub.publish("listening", override=True)
        self.loop(1)

        self.assertIn("asr", self.seen, "recognising never reached the screen")
        self.assertIn("heard", self.seen, "the transcription never appeared")
        self.assertIn("speaking", self.seen, "the spoken reply never appeared")
        self.assertGreaterEqual(self.seen.count("asr"), 4,
                                "recognising flickered instead of holding")
        self.assertEqual(self.seen[-1], "listening")


if __name__ == "__main__":
    unittest.main()
