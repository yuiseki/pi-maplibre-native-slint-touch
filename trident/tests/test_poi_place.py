"""Tests for naming a place in a POI request.

"show hotels" searches wherever the map is looking, which after "show Tokyo"
is the middle of a prefecture -- so the deck looked like it could only think in
prefectures. It could not: Nominatim resolves 台東区, 浅草, Taito and Shibuya
perfectly well, it was simply never asked. This is the asking.
"""
import json
import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "pi-hear"))

import geo
import intent


class PoiPlaceRuleTest(unittest.TestCase):
    def plan(self, said, lang="ja"):
        return intent.for_voice(said, lang=lang)

    def test_english_in_and_near_and_around(self):
        for said, want in (
            ("show hotels in Shinjuku", ["hotel", "Shinjuku"]),
            ("show cafes in Taito", ["cafe", "Taito"]),
            ("hotels near Ueno", ["hotel", "Ueno"]),
            ("cafes around Asakusa", ["cafe", "Asakusa"]),
            ("show me hotels in New York", ["hotel", "New", "York"]),
        ):
            self.assertEqual(self.plan(said)["args"], want, said)

    def test_japanese_no_joins_place_to_category(self):
        for said, want in (
            ("新宿のホテルを表示して", ["hotel", "新宿"]),
            ("台東区のカフェ", ["cafe", "台東区"]),
            ("浅草のホテルを表示", ["hotel", "浅草"]),
            ("渋谷区のホテル", ["hotel", "渋谷区"]),
        ):
            self.assertEqual(self.plan(said)["args"], want, said)

    def test_a_verb_is_not_required_when_a_place_is_named(self):
        # 「台東区のカフェ」and "hotels near Ueno" have no verb at all. Requiring
        # one dropped exactly the sentences people use when they have a place
        # in mind, which is when naming the place matters most.
        self.assertIsNotNone(self.plan("台東区のカフェ"))
        self.assertIsNotNone(self.plan("hotels near Ueno"))

    def test_near_me_is_still_here_not_a_place(self):
        for said in ("cafes near me", "近くのカフェ", "この辺のカフェ",
                     "周辺のホテル", "cafes near here"):
            args = self.plan(said)["args"]
            self.assertIn("--here", args, said)
            self.assertEqual(len(args), 2, said)

    def test_words_that_are_not_places(self):
        # "in the area" names nowhere; looking it up wastes the time it takes
        # to fail and then searches the wrong place if it accidentally hits.
        for said in ("show cafes in the area", "show hotels in town",
                     "show cafes in view"):
            self.assertEqual(self.plan(said)["args"][-1],
                             self.plan(said)["intent"]["category"], said)

    def test_the_innermost_place_wins(self):
        # 「東京の新宿のホテル」means Shinjuku, the narrower of the two.
        self.assertEqual(self.plan("東京の新宿のホテル")["args"], ["hotel", "新宿"])

    def test_a_place_alone_is_still_a_place(self):
        self.assertEqual(self.plan("show me the map of Hiroshima")["tool"],
                         "pi-geocode")

    def test_a_category_alone_still_uses_the_map(self):
        self.assertEqual(self.plan("show cafes")["args"], ["cafe"])


class PlaceRadiusTest(unittest.TestCase):
    """A ward is not a point, and a prefecture is not searchable."""

    def place(self, south, north):
        body = json.dumps([{
            "lat": (south + north) / 2, "lon": 139.7, "place_rank": 14,
            "display_name": "x",
            "boundingbox": [str(south), str(north), "139.6", "139.8"],
        }])
        return geo.parse_nominatim(body)

    def test_a_ward_gets_a_radius_that_covers_it(self):
        # 新宿区 is about 4km north to south.
        p = self.place(35.6700, 35.7060)
        self.assertGreater(p.radius_m(), 1500)
        self.assertLess(p.radius_m(), 2500)

    def test_a_neighbourhood_gets_a_small_one(self):
        p = self.place(35.7100, 35.7130)     # ~330m
        self.assertEqual(p.radius_m(), geo.PLACE_RADIUS_MIN)

    def test_a_prefecture_is_capped(self):
        # 東京都 reaches a thousand kilometres south to Ogasawara. Uncapped,
        # "hotels in Tokyo" is not a question anything can answer.
        p = self.place(24.0, 35.9)
        self.assertEqual(p.radius_m(), geo.PLACE_RADIUS_MAX)

    def test_no_bounding_box_means_no_opinion(self):
        body = json.dumps([{"lat": "35.7", "lon": "139.7", "place_rank": 14,
                            "display_name": "x"}])
        self.assertIsNone(geo.parse_nominatim(body).radius_m())

    def test_the_extent_survives_parsing(self):
        p = self.place(35.67, 35.706)
        self.assertAlmostEqual(p.south, 35.67)
        self.assertAlmostEqual(p.north, 35.706)


if __name__ == "__main__":
    unittest.main()
