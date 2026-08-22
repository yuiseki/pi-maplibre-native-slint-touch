"""Tests for the geocoding and POI plumbing.

Everything here is the pure half: building queries, reading answers, choosing a
zoom, turning results into the marker lines the map already knows how to draw.
The network half is a thin wrapper around these and is exercised on the machine.
"""
import json
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                "..", "pi-hear"))

import geo


class ZoomTest(unittest.TestCase):
    """A place should arrive filling the screen, not as a dot or a continent."""

    def test_a_country_gets_a_wide_zoom(self):
        # Japan: roughly 20 degrees tall.
        self.assertLessEqual(geo.zoom_for_bbox(24.0, 46.0, 122.0, 154.0), 6)

    def test_a_city_gets_a_city_zoom(self):
        # Hiroshima city: about 0.3 degrees.
        z = geo.zoom_for_bbox(34.30, 34.55, 132.30, 132.60)
        self.assertGreaterEqual(z, 9)
        self.assertLessEqual(z, 12)

    def test_a_building_does_not_zoom_past_the_tiles(self):
        # planet.pmtiles stops at z14; asking for more shows nothing new.
        z = geo.zoom_for_bbox(34.3975, 34.3976, 132.4753, 132.4754)
        self.assertLessEqual(z, 14)

    def test_a_degenerate_bbox_still_gives_a_usable_zoom(self):
        z = geo.zoom_for_bbox(34.0, 34.0, 132.0, 132.0)
        self.assertGreaterEqual(z, 1)
        self.assertLessEqual(z, 14)

    def test_zoom_grows_as_the_box_shrinks(self):
        wide = geo.zoom_for_bbox(30.0, 40.0, 130.0, 140.0)
        narrow = geo.zoom_for_bbox(34.0, 34.5, 132.0, 132.5)
        self.assertGreater(narrow, wide)


class NominatimParseTest(unittest.TestCase):
    HIROSHIMA = json.dumps([{
        "place_id": 1, "lat": "34.3916058", "lon": "132.4518156",
        "display_name": "広島市, 広島県, 日本",
        "boundingbox": ["34.3078", "34.5479", "132.3072", "132.5581"],
        "importance": 0.65, "addresstype": "city",
    }])

    def test_reads_the_first_result(self):
        r = geo.parse_nominatim(self.HIROSHIMA)
        self.assertAlmostEqual(r.lat, 34.3916058, places=5)
        self.assertAlmostEqual(r.lon, 132.4518156, places=5)
        self.assertIn("広島", r.name)

    def test_derives_a_zoom_from_the_bounding_box(self):
        r = geo.parse_nominatim(self.HIROSHIMA)
        self.assertGreaterEqual(r.zoom, 9)
        self.assertLessEqual(r.zoom, 12)

    def test_no_result_is_none_not_an_exception(self):
        self.assertIsNone(geo.parse_nominatim("[]"))

    def test_malformed_json_is_none_not_an_exception(self):
        # The recogniser feeds this whatever it heard; a bad answer must not
        # take the voice loop down.
        self.assertIsNone(geo.parse_nominatim("not json at all"))
        self.assertIsNone(geo.parse_nominatim(""))

    def test_a_result_without_a_bbox_still_works(self):
        one = json.dumps([{"lat": "1.0", "lon": "2.0", "display_name": "X"}])
        r = geo.parse_nominatim(one)
        self.assertEqual((r.lat, r.lon), (1.0, 2.0))
        self.assertGreaterEqual(r.zoom, 1)

    def test_the_flyto_line_is_what_the_map_expects(self):
        r = geo.parse_nominatim(self.HIROSHIMA)
        parts = r.flyto_line().split()
        self.assertEqual(len(parts), 3)
        float(parts[0]); float(parts[1]); int(parts[2])


class ImportanceRankingTest(unittest.TestCase):
    """Nominatim does not return its results best-first.

    Real answers from the deck: asking for "kyoto" puts a neighbourhood in
    Indonesia ahead of 京都府, and "tokyo" puts a hamlet in Benin ahead of 東京都.
    Taking the first result sends you to the wrong hemisphere. The importance
    field is right even when the order is not.
    """

    KYOTO = json.dumps([
        {"lat": "-6.378273", "lon": "106.960071", "importance": 0.1333,
         "place_rank": 20, "addresstype": "neighbourhood",
         "display_name": "Kyoto, Kota Wisata, Indonesia",
         "boundingbox": ["-6.38", "-6.37", "106.95", "106.97"]},
        {"lat": "35.0", "lon": "135.76", "importance": 0.2133,
         "place_rank": 14, "addresstype": "city",
         "display_name": "京都市, 京都府, 日本",
         "boundingbox": ["34.87", "35.2", "135.6", "135.9"]},
        {"lat": "35.2", "lon": "135.5", "importance": 0.2933,
         "place_rank": 8, "addresstype": "province",
         "display_name": "京都府, 日本",
         "boundingbox": ["34.7", "35.8", "134.8", "136.1"]},
    ])

    TOKYO = json.dumps([
        {"lat": "9.0", "lon": "1.7", "importance": 0.1333, "place_rank": 20,
         "display_name": "Tokyo, Bassila, Donga, Bénin"},
        {"lat": "-5.8", "lon": "142.9", "importance": 0.1333, "place_rank": 20,
         "display_name": "Tokyo, Hela, Papua New Guinea"},
        {"lat": "35.68", "lon": "139.75", "importance": 0.2933, "place_rank": 8,
         "display_name": "東京都, 日本"},
    ])

    def test_the_best_result_wins_not_the_first(self):
        r = geo.parse_nominatim(self.KYOTO)
        self.assertIn("京都", r.name)
        self.assertGreater(r.lat, 30)

    def test_tokyo_lands_in_japan(self):
        r = geo.parse_nominatim(self.TOKYO)
        self.assertIn("東京", r.name)
        self.assertAlmostEqual(r.lat, 35.68, places=1)

    def test_a_lower_place_rank_breaks_a_tie(self):
        # Same importance, different size: the bigger place is what a person
        # means by a bare name.
        tie = json.dumps([
            {"lat": "1.0", "lon": "1.0", "importance": 0.2, "place_rank": 20,
             "display_name": "small"},
            {"lat": "2.0", "lon": "2.0", "importance": 0.2, "place_rank": 8,
             "display_name": "big"},
        ])
        self.assertEqual(geo.parse_nominatim(tie).name, "big")

    def test_results_without_importance_keep_their_order(self):
        # Older responses, and some object types, carry no importance at all.
        plain = json.dumps([
            {"lat": "1.0", "lon": "1.0", "display_name": "first"},
            {"lat": "2.0", "lon": "2.0", "display_name": "second"},
        ])
        self.assertEqual(geo.parse_nominatim(plain).name, "first")


class SettlementPreferenceTest(unittest.TestCase):
    """A bare city name means the city, not the prefecture containing it.

    Ranking purely by importance picks the largest administrative unit, because
    on this import importance is derived from place_rank alone -- there is no
    Wikipedia importance data. So "kyoto" landed on 京都府 and "hiroshima" on
    広島県. Both are in the right place, and neither is what was asked for.
    """

    def _r(self, name, lat, rank, atype, imp, box=None):
        d = {"lat": str(lat), "lon": "135.0", "importance": imp,
             "place_rank": rank, "addresstype": atype, "display_name": name}
        if box:
            d["boundingbox"] = [str(x) for x in box]
        return d

    def test_the_city_beats_the_prefecture(self):
        body = json.dumps([
            self._r("京都府, 日本", 35.4, 8, "province", 0.2933),
            self._r("京都市, 京都府, 日本", 35.0, 14, "city", 0.2133),
        ])
        self.assertIn("京都市", geo.parse_nominatim(body).name)

    def test_a_far_away_namesake_still_loses(self):
        # The Indonesian neighbourhood is a settlement type too; importance
        # still has to decide between settlements.
        body = json.dumps([
            self._r("Kyoto, Indonesia", -6.3, 20, "neighbourhood", 0.1333),
            self._r("京都市, 日本", 35.0, 14, "city", 0.2133),
        ])
        self.assertIn("京都市", geo.parse_nominatim(body).name)

    def test_a_prefecture_wins_when_there_is_no_city(self):
        # 東京都 has no city-level equivalent; the prefecture is the answer.
        body = json.dumps([self._r("東京都, 日本", 35.68, 8, "province", 0.2933)])
        self.assertIn("東京都", geo.parse_nominatim(body).name)

    def test_a_distant_namesake_settlement_does_not_beat_a_prefecture(self):
        # Real answer for "tokyo": three hamlets called Tokyo elsewhere in the
        # world, and 東京都. Preferring settlements outright sent the map to
        # Benin. The preference only holds among comparable places.
        body = json.dumps([
            self._r("Tokyo, Bassila, Donga, Bénin", 8.7, 20, "neighbourhood", 0.1333),
            self._r("Tokyo, Hela, Papua New Guinea", -5.8, 20, "hamlet", 0.1333),
            self._r("東京都, 日本", 35.68, 8, "province", 0.2933),
        ])
        self.assertIn("東京都", geo.parse_nominatim(body).name)

    def test_the_city_still_wins_when_it_is_one_step_down(self):
        # 京都市 is one rank step below 京都府 and is the intended answer;
        # the rule has to keep this while rejecting the case above.
        body = json.dumps([
            self._r("京都府, 日本", 35.4, 8, "province", 0.2933),
            self._r("京都市, 京都府, 日本", 35.0, 14, "city", 0.2133),
        ])
        self.assertIn("京都市", geo.parse_nominatim(body).name)


class ZoomFloorTest(unittest.TestCase):
    """A place must not arrive so far out that it is not the subject any more.

    東京都 reaches a thousand kilometres south to Ogasawara, so its bounding box
    fits at zoom 3 -- which shows the whole of Japan and answers a different
    question than the one asked.
    """

    def test_a_prefecture_with_remote_islands_still_arrives_close(self):
        body = json.dumps([{
            "lat": "35.6769", "lon": "139.7639", "importance": 0.2933,
            "place_rank": 8, "addresstype": "province",
            "display_name": "東京都, 日本",
            "boundingbox": ["20.2", "35.9", "136.0", "153.9"],
        }])
        self.assertGreaterEqual(geo.parse_nominatim(body).zoom, 7)

    def test_a_country_is_still_allowed_to_be_wide(self):
        body = json.dumps([{
            "lat": "36.0", "lon": "138.0", "importance": 0.4,
            "place_rank": 4, "addresstype": "country",
            "display_name": "日本",
            "boundingbox": ["24.0", "46.0", "122.0", "154.0"],
        }])
        self.assertLessEqual(geo.parse_nominatim(body).zoom, 6)

    def test_a_tight_bbox_is_not_pushed_wider(self):
        body = json.dumps([{
            "lat": "34.39", "lon": "132.45", "importance": 0.2133,
            "place_rank": 14, "addresstype": "city",
            "display_name": "広島市",
            "boundingbox": ["34.30", "34.55", "132.30", "132.60"],
        }])
        z = geo.parse_nominatim(body).zoom
        self.assertGreaterEqual(z, 9)
        self.assertLessEqual(z, 12)


class OverpassQueryTest(unittest.TestCase):
    def test_builds_a_bbox_query_for_a_category(self):
        q = geo.overpass_query("cafe", 34.39, 132.45, 1000)
        self.assertIn("amenity=cafe", q)
        self.assertIn("out:json", q)
        # South < north and west < east, or Overpass returns nothing at all.
        import re
        m = re.search(r"\(([-\d.]+),([-\d.]+),([-\d.]+),([-\d.]+)\)", q)
        s, w, n, e = (float(x) for x in m.groups())
        self.assertLess(s, n)
        self.assertLess(w, e)

    def test_a_bigger_radius_makes_a_bigger_box(self):
        small = geo.overpass_query("cafe", 34.39, 132.45, 500)
        big = geo.overpass_query("cafe", 34.39, 132.45, 5000)
        self.assertNotEqual(small, big)

    def test_known_categories_map_to_their_tags(self):
        self.assertIn("amenity=restaurant", geo.overpass_query("restaurant", 0, 0, 100))
        self.assertIn("amenity=toilets", geo.overpass_query("toilet", 0, 0, 100))
        self.assertIn("shop=convenience", geo.overpass_query("convenience", 0, 0, 100))

    def test_an_unknown_category_is_refused_rather_than_guessed(self):
        # Guessing a tag produces an empty map and no explanation.
        with self.assertRaises(ValueError):
            geo.overpass_query("bicycle-powered submarine", 0, 0, 100)

    def test_categories_are_matched_loosely(self):
        for word in ("cafe", "Cafes", "CAFÉ", "coffee", "カフェ"):
            self.assertIn("amenity=cafe", geo.overpass_query(word, 0, 0, 100))


class CanonicalCategoryTest(unittest.TestCase):
    """One spelling downstream, whatever was said upstream."""

    def test_japanese_and_english_agree(self):
        self.assertEqual(geo.canonical_category("カフェ"),
                         geo.canonical_category("coffee"))
        self.assertEqual(geo.canonical_category("カフェ"), "cafe")

    def test_it_is_the_ascii_name(self):
        for word in ("コンビニ", "convenience"):
            self.assertEqual(geo.canonical_category(word), "convenience")

    def test_unknown_is_none(self):
        self.assertIsNone(geo.canonical_category("submarine"))

    def test_voiced_marks_survive_normalisation(self):
        # NFKD splits ビ into ヒ + a combining mark, and stripping every
        # combining character turned コンビニ into コンヒニ. Latin accents still
        # have to go, so the two cases are not the same rule.
        for word in ("コンビニ", "スーパー", "レストラン", "ホテル", "博物館"):
            self.assertIsNotNone(geo.canonical_category(word), word)
        self.assertEqual(geo.canonical_category("CAFÉ"), "cafe")

    def test_the_canonical_name_is_itself_a_valid_category(self):
        for word in ("カフェ", "病院", "駅", "コンビニ"):
            name = geo.canonical_category(word)
            self.assertEqual(geo.CATEGORIES[name], geo.CATEGORIES[
                geo.normalise_category(word)])


class MarkerLineTest(unittest.TestCase):
    """The map reads '<id> <lat> <lon> <epoch> <name>' per line."""

    ELEMENTS = json.dumps({"elements": [
        {"type": "node", "id": 11, "lat": 34.39, "lon": 132.45,
         "tags": {"amenity": "cafe", "name": "コメダ珈琲店"}},
        {"type": "node", "id": 12, "lat": 34.40, "lon": 132.46,
         "tags": {"amenity": "cafe"}},
        {"type": "way", "id": 13, "center": {"lat": 34.41, "lon": 132.47},
         "tags": {"amenity": "cafe", "name": "Starbucks Coffee"}},
    ]})

    def test_each_element_becomes_one_line_of_five_fields(self):
        lines = geo.marker_lines(self.ELEMENTS, epoch=1000).splitlines()
        self.assertEqual(len(lines), 3)
        for line in lines:
            self.assertEqual(len(line.split()), 5)

    def test_a_way_uses_its_centre(self):
        lines = geo.marker_lines(self.ELEMENTS, epoch=1000).splitlines()
        self.assertTrue(any("34.41" in l and "132.47" in l for l in lines))

    def test_spaces_in_names_are_collapsed(self):
        # The map parses the name with a single >>, so a space would truncate it
        # and leave the rest of the line as garbage.
        lines = geo.marker_lines(self.ELEMENTS, epoch=1000).splitlines()
        star = [l for l in lines if "Starbucks" in l][0]
        self.assertEqual(len(star.split()), 5)
        self.assertIn("Starbucks_Coffee", star)

    def test_an_unnamed_element_still_gets_a_label(self):
        lines = geo.marker_lines(self.ELEMENTS, epoch=1000).splitlines()
        self.assertTrue(all(len(l.split()) == 5 for l in lines))

    def test_ids_are_prefixed_so_they_cannot_collide_with_mesh_nodes(self):
        # POIs borrow the Meshtastic marker feed; on a deck with a radio the two
        # would otherwise share an id space.
        lines = geo.marker_lines(self.ELEMENTS, epoch=1000).splitlines()
        self.assertTrue(all(l.split()[0].startswith("poi") for l in lines))

    def test_the_epoch_is_the_one_given(self):
        line = geo.marker_lines(self.ELEMENTS, epoch=4242).splitlines()[0]
        self.assertEqual(line.split()[3], "4242")

    def test_empty_result_is_an_empty_string_not_a_crash(self):
        self.assertEqual(geo.marker_lines(json.dumps({"elements": []}), 1), "")

    def test_malformed_json_is_empty_not_an_exception(self):
        self.assertEqual(geo.marker_lines("nope", 1), "")

    def test_elements_without_coordinates_are_skipped(self):
        bad = json.dumps({"elements": [{"type": "node", "id": 1, "tags": {}}]})
        self.assertEqual(geo.marker_lines(bad, 1), "")

    def test_a_cap_keeps_the_map_readable(self):
        many = {"elements": [
            {"type": "node", "id": i, "lat": 34.0 + i / 10000.0, "lon": 132.0,
             "tags": {"amenity": "cafe"}} for i in range(500)]}
        lines = geo.marker_lines(json.dumps(many), 1, limit=200).splitlines()
        self.assertEqual(len(lines), 200)


if __name__ == "__main__":
    unittest.main()
