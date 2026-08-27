"""Tests for the third tier: a generated query when the table has no word.

The deck answers POI searches from geo.CATEGORIES, which is 20 English words
here. The model can already say what it heard -- measured on pi5-deck, "Find
bakeries in Kyoto." comes back as

    {"intent":"show_poi","place":"Kyoto","category":"bakeries"}

and parse() then empties the category, deliberately: an unknown one becomes no
category rather than a nearby wrong one. That guard is right and stays. What
was missing is anywhere for the discarded word to go.

So the word is kept as `concern`, and a show_poi with a concern, a place and no
category routes to pi-ql, which asks the fine-tuned model for the query.
Measured end to end on the deck: Bakeries in Kyoto 145, Bookshops in Kyoto 69.
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                "..", "pi-hear"))
import intent


class KeepTest(unittest.TestCase):
    def test_an_unknown_category_is_kept_as_a_concern(self):
        r = intent.parse('{"intent":"show_poi","place":"Kyoto","category":"bakeries"}')
        self.assertEqual(r["category"], "", "the guard still empties it")
        self.assertEqual(r["concern"], "bakeries")

    def test_a_known_category_leaves_no_concern(self):
        """pi-poi is the better answer where it applies: deterministic, no
        generation, no chance of the wrong tag."""
        r = intent.parse('{"intent":"show_poi","place":"Kyoto","category":"cafe"}')
        self.assertEqual(r["category"], "cafe")
        self.assertEqual(r["concern"], "")

    def test_the_junk_words_are_not_concerns(self):
        """A small model fills the field with "city", "map", "place". Those are
        what the guard exists for; passing them to a query generator would ask
        Overpass for a tag nobody wants."""
        for junk in ("city", "map", "place", "area", "location"):
            r = intent.parse(
                '{"intent":"show_poi","place":"Kyoto","category":"%s"}' % junk)
            self.assertEqual(r["concern"], "", junk)

    def test_an_empty_category_stays_empty(self):
        r = intent.parse('{"intent":"show_poi","place":"Kyoto","category":""}')
        self.assertEqual(r["concern"], "")


class RouteTest(unittest.TestCase):
    def plan(self, blob, lang="en"):
        r = intent.parse(blob)
        return intent.plan_for(r) if hasattr(intent, "plan_for") else None

    def test_a_known_category_still_goes_to_pi_poi(self):
        p = intent.for_voice_result(intent.parse(
            '{"intent":"show_poi","place":"Kyoto","category":"cafe"}'))
        self.assertEqual(p["tool"], "pi-poi")

    def test_an_unknown_one_goes_to_pi_ql(self):
        p = intent.for_voice_result(intent.parse(
            '{"intent":"show_poi","place":"Kyoto","category":"bakeries"}'))
        self.assertEqual(p["tool"], "pi-ql")
        self.assertEqual(p["args"][0], "bakeries")
        self.assertIn("Kyoto", p["args"])

    def test_without_a_place_there_is_nothing_to_bound_it_with(self):
        """pi-ql needs an area. On a planet database a query without one asks
        for everything, so no place means no third tier."""
        p = intent.for_voice_result(intent.parse(
            '{"intent":"show_poi","place":"","category":"bakeries"}'))
        self.assertIsNone(p)

    def test_the_third_tier_gets_a_longer_budget(self):
        """Generation is 2-4s on top of the query itself."""
        p = intent.for_voice_result(intent.parse(
            '{"intent":"show_poi","place":"Kyoto","category":"bakeries"}'))
        self.assertGreaterEqual(p["timeout"], 120)


if __name__ == "__main__":
    unittest.main()
