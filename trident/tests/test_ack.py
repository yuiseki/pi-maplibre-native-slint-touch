"""Tests for saying what the map is about to do.

"zoom into ..." and "show cafes" moved the map in silence. Overpass takes
seconds, so the gap between asking and anything happening is long enough to
wonder whether the deck heard at all -- and the only other sound it makes is
the apology when nothing was found, which means silence had to carry both
"working on it" and "it worked".

The wording echoes the speaker's own word rather than the normalised category.
"cafe" is what the map searches for, but "Show cafes" answered with "showing
cafe" sounds broken, and naive pluralising gets "parkings" and "fuels". Saying
back what was said is right in both languages at once: カフェ stays カフェ.
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                "..", "pi-hear"))
import intent


def result(name, place="", category="", here=False):
    return {"intent": name, "place": place, "category": category,
            "lang": "", "here": here}


class AckTest(unittest.TestCase):
    def test_a_named_place_is_said_back(self):
        r = result("zoom_to", place="Hiroshima station")
        self.assertEqual(intent.ack_reply(r, "en"),
                         "OK, showing Hiroshima station.")

    def test_japanese_says_it_in_japanese(self):
        r = result("zoom_to", place="広島駅")
        self.assertEqual(intent.ack_reply(r, "ja"),
                         "承知しました。広島駅を表示します。")

    def test_a_category_uses_the_word_that_was_spoken(self):
        """"cafe" is the search term; "cafes" is what was said."""
        r = result("show_poi", category="cafe")
        self.assertEqual(intent.ack_reply(r, "en", said="Show, cafes."),
                         "OK, showing cafes.")

    def test_a_japanese_category_stays_japanese(self):
        r = result("show_poi", category="cafe")
        self.assertEqual(intent.ack_reply(r, "ja", said="カフェを表示して"),
                         "承知しました。カフェを表示します。")

    def test_without_the_transcript_it_still_names_something(self):
        """The fallback must not say an English word inside a Japanese
        sentence, which is what using the raw category would do."""
        r = result("show_poi", category="cafe")
        self.assertEqual(intent.ack_reply(r, "en"), "OK, showing cafe.")
        ja = intent.ack_reply(r, "ja")
        self.assertTrue(ja.startswith("承知しました。"), ja)
        self.assertNotIn("cafe", ja)

    def test_an_awkward_plural_is_never_invented(self):
        """"parkings" and "fuels" are why the spoken word is preferred."""
        for cat in ("parking", "fuel", "coffee"):
            r = result("show_poi", category=cat)
            said = intent.ack_reply(r, "en")
            self.assertNotIn(cat + "s", said, cat)

    def test_a_category_in_a_place_names_both(self):
        r = result("show_poi", category="cafe", place="Hiroshima")
        got = intent.ack_reply(r, "en", said="show cafes in Hiroshima")
        self.assertIn("cafes", got)
        self.assertIn("Hiroshima", got)

    def test_here_is_said_as_here(self):
        r = result("show_here")
        self.assertIn("here", intent.ack_reply(r, "en").lower())
        self.assertIn("現在地", intent.ack_reply(r, "ja"))

    def test_anything_else_still_gets_an_answer(self):
        """The minimum the request asked for: never move the map in silence."""
        for name in ("clear_poi", "something_new"):
            self.assertEqual(intent.ack_reply(result(name), "en"), "Understood.")
            self.assertEqual(intent.ack_reply(result(name), "ja"), "承知しました。")

    def test_an_empty_place_does_not_produce_a_gap(self):
        """"OK, showing ." is worse than "Understood."."""
        self.assertEqual(intent.ack_reply(result("zoom_to", place="  "), "en"),
                         "Understood.")



class NearbyTest(unittest.TestCase):
    """"hotels near here" is a hotel search, not a request to see where I am.

    The first version let the "here" flag win over the category, so
    「この辺のホテル」was answered「現在地を表示します」-- which describes a
    different command, and one the map was not about to run. An acknowledgement
    that names the wrong thing is worse than the silence it replaced: it is
    confidently wrong rather than merely unhelpful.
    """

    def test_a_nearby_search_names_what_is_searched_for(self):
        r = result("show_poi", category="hotel", here=True)
        got = intent.ack_reply(r, "ja", said="この辺のホテル")
        self.assertIn("ホテル", got)
        self.assertNotEqual(got, "承知しました。現在地を表示します。")

    def test_english_says_nearby(self):
        r = result("show_poi", category="hotel", here=True)
        got = intent.ack_reply(r, "en", said="hotels near here")
        self.assertIn("hotels", got)
        self.assertIn("nearby", got)

    def test_showing_where_i_am_still_says_here(self):
        """The command that really is about the deck's own position."""
        self.assertIn("現在地", intent.ack_reply(result("show_here"), "ja"))


if __name__ == "__main__":
    unittest.main()
