"""What the deck writes down, and what it must do with it.

Two rules learned the hard way, in that order:

1. Test against what the recogniser returns, not against what a person types.
2. When the recogniser returns rubbish, fix the recogniser. Rules written to
   decode rubbish look like they work -- the tests pass -- and then misfire on
   sentences nobody thought about.

The transcripts here are verbatim from 2026-08-23. The first group is what the
deck produced once the encoder was given its whole context; the second is what
the same audio produced before that, kept only to pin what must NOT happen.
"""
import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "pi-hear"))

import intent


class HeardCorrectlyTest(unittest.TestCase):
    """Full audio context, beam 5. These are the transcripts to serve."""

    def test_language_switch(self):
        for said in ("language mode Japanese", "Language Mode Japanese",
                     "Language mode Japanese"):
            plan = intent.for_voice(said, lang="en")
            self.assertIsNotNone(plan, said)
            self.assertEqual(plan["tool"], "pi-lang", said)
            self.assertEqual(plan["args"], ["ja"], said)

    def test_the_phrase_without_a_language_means_the_other_one(self):
        # "Language mode." with the name lost. Listening in English, the only
        # thing it can mean is Japanese.
        plan = intent.for_voice("Language mode.", lang="en")
        self.assertEqual(plan["args"], ["ja"])

    def test_a_category_and_a_place(self):
        for said in ("show hotels in Shinjuku", "Show Hotels in Shinjuku"):
            plan = intent.for_voice(said, lang="en")
            self.assertEqual(plan["args"], ["hotel", "Shinjuku"], said)


class HeardBadlyTest(unittest.TestCase):
    """Same audio, truncated encoder context. None of this is served.

    These stay as tests because the tempting fix was to decode them -- fuzzy
    category matching for "hot abs", a rule keyed on a trailing language name
    for the rest. Both were written, both passed, and both were thrown away in
    favour of fixing the encoder. What matters now is only that they are inert.
    """

    GARBLED = ("show hot abs in Shinjuku",
               "Nange is mode Japanese",
               "Rangage Mode, Japanese",
               "Languise Mode",
               "Langue smooth Japanese")

    def test_nothing_is_invented_from_them(self):
        for said in self.GARBLED:
            plan = intent.for_voice(said, lang="en")
            if plan is None:
                continue
            # If anything is done at all it must not be flying the map to a
            # word the model made up out of a mangled command. That is how
            # these announced themselves: the view was lost as well.
            self.assertNotEqual(plan["tool"], "pi-geocode", said)

    def test_a_failed_switch_never_becomes_a_place(self):
        for said in ("Languise Mode", "Rangage Mode", "Langue smooth"):
            plan = intent.for_voice(said, lang="en")
            if plan is not None:
                self.assertNotEqual(plan["tool"], "pi-geocode", said)


class PlaceBeatsCategoryTest(unittest.TestCase):
    def test_an_explicit_map_of_wins(self):
        # Not a transcription question: "map of Hotel California" says plainly
        # that it names a place, and it contains a category word.
        plan = intent.for_voice("show me the map of Hotel California", lang="en")
        self.assertEqual(plan["tool"], "pi-geocode")
        self.assertEqual(plan["args"], ["--fly", "Hotel California"])


if __name__ == "__main__":
    unittest.main()
