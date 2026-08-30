"""Tests for reading the position file, and for tolerating a longer one.

The file gained a sixth field naming its writer, so pi-mesh can tell a
trilaterated position from one it wrote itself out of the node's own fix. When
that field was added the claim was that five-field readers would be unaffected
because they index the fields they know.

This reader did not. `len(parts) != 5` rejected the line outright, so on both
decks /dev/shm/pi-gps went on being written every ten minutes while
/dev/shm/pi-gps-lastfix -- the file everything downstream actually reads --
sat unchanged for six hours, pointing at where the deck had been.

Nothing looked broken. The publisher reported success, the timer reported
success, the file was fresh, and the one number anyone consumes was stale.
That is the failure mode worth a test: a format change is not backwards
compatible because it was intended to be, only because a reader was run
against it.

So a longer line parses, its extra fields are ignored, and a shorter one is
still refused.
"""
import importlib.machinery
import importlib.util
import os
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
TOOL = os.path.join(HERE, "..", "bin", "pi-gps-track")


def load():
    loader = importlib.machinery.SourceFileLoader("pi_gps_track", TOOL)
    spec = importlib.util.spec_from_loader("pi_gps_track", loader)
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


t = load()


class ParseShm(unittest.TestCase):
    def test_the_five_fields_pi_gps_writes(self):
        d = t.parse_shm("34.385000 132.455000 1 7 9\n")
        self.assertIsNotNone(d)
        self.assertAlmostEqual(d["lat"], 34.385)
        self.assertAlmostEqual(d["lon"], 132.455)
        self.assertEqual(d["fix"], 1)
        self.assertEqual(d["sats_used"], 7)
        self.assertEqual(d["sats_in_view"], 9)

    def test_a_sixth_field_does_not_reject_the_line(self):
        d = t.parse_shm("34.387635 132.454823 1 0 0 wigle\n")
        self.assertIsNotNone(d)
        self.assertAlmostEqual(d["lat"], 34.387635)
        self.assertAlmostEqual(d["lon"], 132.454823)
        self.assertEqual(d["fix"], 1)

    def test_further_fields_are_ignored_rather_than_guessed_at(self):
        d = t.parse_shm("1.0 2.0 1 0 0 wigle something else\n")
        self.assertIsNotNone(d)
        self.assertAlmostEqual(d["lat"], 1.0)

    def test_too_few_fields_is_still_not_a_position(self):
        for bad in ("", "1.0", "1.0 2.0", "1.0 2.0 1", "1.0 2.0 1 0"):
            self.assertIsNone(t.parse_shm(bad), bad)

    def test_nonsense_in_a_known_field_is_still_refused(self):
        self.assertIsNone(t.parse_shm("a b 1 0 0"))
        self.assertIsNone(t.parse_shm("1.0 2.0 x 0 0"))

    def test_no_fix_is_carried_through_rather_than_dropped(self):
        # The caller decides what fix=0 means; the parser only reads.
        d = t.parse_shm("0.000000 0.000000 0 0 1")
        self.assertIsNotNone(d)
        self.assertEqual(d["fix"], 0)


if __name__ == "__main__":
    unittest.main()
