"""The state file carries the language, so the map does not have to guess.

The captions on screen -- "考え中...", "お話しください" -- were hard-coded
Japanese while the recogniser was running in English. The map could read
PI_HEAR_LANG itself, but then changing it means restarting both, and forgetting
one leaves the deck listening in one language and captioning in another.

pi-hear knows the language. It says so, and the map does as it is told.
"""
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                "..", "pi-hear"))
import hear_state


class LanguageLineTest(unittest.TestCase):
    def setUp(self):
        self.path = os.path.join(tempfile.mkdtemp(prefix="hear-state."), "s")

    def read(self):
        with open(self.path) as fh:
            return fh.read().split("\n")

    def test_the_language_is_the_third_line(self):
        p = hear_state.StatePublisher(self.path, lang="en")
        p.publish("armed")
        self.assertEqual(self.read()[2], "en")

    def test_it_defaults_to_japanese(self):
        # Everyday use, and what the captions were written in.
        p = hear_state.StatePublisher(self.path)
        p.publish("armed")
        self.assertEqual(self.read()[2], "ja")

    def test_an_unknown_language_falls_back_rather_than_leaking_through(self):
        # PI_HEAR_LANG can be 'auto', and the map has strings for two languages.
        for value in ("auto", "", None, "klingon"):
            p = hear_state.StatePublisher(self.path, lang=value)
            p.publish("armed")
            self.assertIn(self.read()[2], ("ja", "en"), repr(value))

    def test_the_first_two_lines_are_unchanged(self):
        # An older map reads two lines and ignores the rest; a newer map with an
        # older pi-hear finds no third line. Neither may break.
        p = hear_state.StatePublisher(self.path, lang="en")
        p.publish("speaking", "hello there")
        lines = self.read()
        self.assertEqual(lines[0], "speaking")
        self.assertEqual(lines[1], "hello there")

    def test_the_language_can_change_without_a_restart(self):
        p = hear_state.StatePublisher(self.path, lang="ja")
        p.publish("armed")
        self.assertEqual(self.read()[2], "ja")
        p.lang = "en"
        p.publish("heard", "x")
        self.assertEqual(self.read()[2], "en")


if __name__ == "__main__":
    unittest.main()
