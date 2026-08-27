"""Tests for waiting until a microphone is plugged in.

On pi5-deck the mic is normally **absent**. It gets plugged in when someone
wants to talk to the map, and unplugged again -- a mic left in a noisy room, or
in a room where someone is working, mishears and acts on things nobody said.

So "no capture device" is the resting state, not a fault. Treating it as a
fault cost a measured 15 restarts a minute at 1.076s of CPU each, roughly a
quarter of a core burned continuously, and 7,056 journal lines an hour.

Waiting is also faster when the mic does arrive: polling notices it within the
poll interval instead of on the next restart.
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                "..", "pi-hear"))
import pi_hear


class Probe:
    """A capture-device probe that starts empty and fills in after N calls."""

    def __init__(self, appears_at=None, cards=("Device",)):
        self.calls = 0
        self.appears_at = appears_at
        self.cards = list(cards)

    def __call__(self):
        self.calls += 1
        if self.appears_at is not None and self.calls >= self.appears_at:
            return list(self.cards)
        return []


class Sleeper:
    def __init__(self):
        self.slept = []

    def __call__(self, secs):
        self.slept.append(secs)


class WaitTest(unittest.TestCase):
    def test_a_mic_already_there_is_not_waited_for(self):
        probe, sleep = Probe(appears_at=1), Sleeper()
        got = pi_hear.wait_for_capture(probe, timeout=60, interval=2,
                                       sleep=sleep)
        self.assertEqual(got, ["Device"])
        self.assertEqual(sleep.slept, [], "must not sleep before the first probe")

    def test_it_waits_and_then_finds_one(self):
        probe, sleep = Probe(appears_at=4), Sleeper()
        got = pi_hear.wait_for_capture(probe, timeout=60, interval=2,
                                       sleep=sleep)
        self.assertEqual(got, ["Device"])
        self.assertEqual(probe.calls, 4)
        self.assertEqual(sleep.slept, [2, 2, 2])

    def test_it_gives_up_after_the_timeout(self):
        """Not forever by default: a host that will never have a mic should
        still surface that, and systemd's restart is the surfacing."""
        probe, sleep = Probe(appears_at=None), Sleeper()
        got = pi_hear.wait_for_capture(probe, timeout=6, interval=2,
                                       sleep=sleep)
        self.assertIsNone(got)
        self.assertLessEqual(len(sleep.slept), 4)

    def test_it_does_not_spin(self):
        """The whole point. Every probe after the first must be paced."""
        probe, sleep = Probe(appears_at=None), Sleeper()
        pi_hear.wait_for_capture(probe, timeout=10, interval=2, sleep=sleep)
        self.assertTrue(all(s >= 1 for s in sleep.slept), sleep.slept)
        self.assertGreaterEqual(probe.calls, 2)

    def test_zero_timeout_is_a_single_look(self):
        """Keeps the old behaviour available for a host where the mic is
        permanent and its absence really is a fault."""
        probe, sleep = Probe(appears_at=None), Sleeper()
        self.assertIsNone(pi_hear.wait_for_capture(probe, timeout=0,
                                                   interval=2, sleep=sleep))
        self.assertEqual(probe.calls, 1)
        self.assertEqual(sleep.slept, [])

    def test_it_reports_only_once_while_waiting(self):
        """7,056 journal lines an hour is what this replaces."""
        said = []
        probe, sleep = Probe(appears_at=5), Sleeper()
        pi_hear.wait_for_capture(probe, timeout=60, interval=2, sleep=sleep,
                                 announce=said.append)
        self.assertEqual(len(said), 1, said)


class FlagTest(unittest.TestCase):
    """Through --help, because the parser is built inside main().

    An earlier version of this asked for pi_hear.build_parser(), found none,
    and skipped -- a test that reported OK while exercising nothing.
    """

    def test_the_wait_is_configurable_and_on_by_default(self):
        import subprocess
        tool = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                            "..", "pi-hear", "pi_hear.py")
        r = subprocess.run([sys.executable, tool, "--help"],
                           capture_output=True, text=True, timeout=60)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("--mic-wait", r.stdout)
        self.assertIn("default 600", r.stdout,
                      "the default has to be a real wait, not zero")


if __name__ == "__main__":
    unittest.main()
