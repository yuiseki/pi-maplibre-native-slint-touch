"""Tests for asking about where the deck actually is.

pi-poi already prefers the map's last flyTo over the GPS fix, and that is right
for "show cafes" after "show me Yokohama". It leaves no way to say "here",
though: once the map has been sent anywhere, the deck's own position stops
being what any command means.
"""
import json
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                "..", "pi-hear"))
import intent


class RecognisedTest(unittest.TestCase):
    def test_english_asks_where_it_is(self):
        for text in ("where am I", "Where am I?", "show my location",
                     "show current location", "Show current location.",
                     "take me home", "go to my location"):
            r = intent.by_rule(text)
            self.assertIsNotNone(r, text)
            self.assertEqual(r["intent"], "show_here", text)

    def test_japanese_asks_where_it_is(self):
        for text in ("現在地", "現在地を表示して", "いまどこ", "ここはどこ"):
            r = intent.by_rule(text)
            self.assertIsNotNone(r, text)
            self.assertEqual(r["intent"], "show_here", text)

    def test_things_near_here(self):
        for text, cat in (("cafes near me", "cafe"),
                          ("show restaurants nearby", "restaurant"),
                          ("近くのカフェ", "cafe"),
                          ("この辺のトイレ", "toilet")):
            r = intent.by_rule(text)
            self.assertIsNotNone(r, text)
            self.assertEqual(r["intent"], "show_poi", text)
            self.assertEqual(r["category"], cat, text)
            self.assertTrue(r["here"], "%r should be anchored here" % text)

    def test_a_plain_poi_request_is_not_anchored_here(self):
        # "show cafes on map" means where the map is looking, which is the
        # behaviour that already exists and is usually what is wanted.
        r = intent.by_rule("show cafes on map")
        self.assertEqual(r["intent"], "show_poi")
        self.assertFalse(r["here"])

    def test_a_place_named_here_is_still_a_place(self):
        r = intent.by_rule("show me the map of Nagoya")
        self.assertEqual(r["intent"], "show_place")


class DispatchTest(unittest.TestCase):
    def test_show_here_flies_to_the_fix(self):
        d = intent.for_voice("where am I")
        self.assertEqual(d["tool"], "pi-here")
        self.assertEqual(d["args"], [])

    def test_show_here_clears_the_pins(self):
        # Going somewhere, like any other move.
        d = intent.for_voice("where am I")
        self.assertTrue(d.get("clear_pins"))

    def test_near_me_asks_pi_poi_for_here(self):
        d = intent.for_voice("cafes near me")
        self.assertEqual(d["tool"], "pi-poi")
        self.assertIn("--here", d["args"])
        self.assertIn("cafe", d["args"])

    def test_near_me_does_not_clear_the_pins(self):
        d = intent.for_voice("cafes near me")
        self.assertFalse(d.get("clear_pins"))


if __name__ == "__main__":
    unittest.main()
