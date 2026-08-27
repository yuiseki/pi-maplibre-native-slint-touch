"""Tests for retrying an area filter with the other name key.

The model writes area["name:en"="X"] because that is what its training data
contains, and its training data is English. Given a Japanese place it writes
the Japanese string into name:en, where nothing matches.

Measured on this deck's own Overpass, counting cafes:

    京都市     name=842   name:en=0
    広島市     name=123   name:en=0
    新宿       name=71    name:en=0
    Kyoto      name=0     name:en=842
    Hiroshima  name=0     name:en=125

One key or the other, never both. So an empty answer from a query with an area
name filter is worth one retry with the key swapped -- the heuristic TRIDENT
uses upstream, and cheaper here than resolving the name through Nominatim.

京都 on its own matches neither: no boundary is named exactly that, it is
京都市 or 京都府. Two calls then find nothing and the deck says so.
"""
import importlib.machinery
import importlib.util
import os
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
TOOL = os.path.join(HERE, "..", "bin", "pi-ql")


def load():
    loader = importlib.machinery.SourceFileLoader("pi_ql", TOOL)
    spec = importlib.util.spec_from_loader("pi_ql", loader)
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


Q = load()

EN = '[out:json][timeout:60];area["name:en"="京都市"]->.a;(nwr["amenity"="cafe"](area.a););out geom;'
JA = '[out:json][timeout:60];area["name"="Kyoto"]->.a;(nwr["amenity"="cafe"](area.a););out geom;'


class SwapTest(unittest.TestCase):
    def test_name_en_becomes_name(self):
        got = Q.swap_area_key(EN)
        self.assertIn('area["name"="京都市"]', got)
        self.assertNotIn("name:en", got)

    def test_name_becomes_name_en(self):
        got = Q.swap_area_key(JA)
        self.assertIn('area["name:en"="Kyoto"]', got)

    def test_only_the_area_filter_is_touched(self):
        """A name filter on the features themselves is a different thing and
        must survive: ["name"="Doutor"] asks for a shop called Doutor."""
        q = ('[out:json];area["name:en"="Kyoto"]->.a;'
             '(nwr["amenity"="cafe"]["name"="Doutor"](area.a););out geom;')
        got = Q.swap_area_key(q)
        self.assertIn('area["name"="Kyoto"]', got)
        self.assertIn('["name"="Doutor"]', got)

    def test_a_query_with_no_area_name_is_unchanged(self):
        """An area id needs no swapping, and a bbox has no name at all."""
        for q in ('[out:json];area(3604097196)->.a;(nwr["shop"="books"](area.a););out geom;',
                  '[out:json];(nwr["amenity"="cafe"](34.9,135.7,35.1,135.8););out geom;'):
            self.assertIsNone(Q.swap_area_key(q), q[:50])

    def test_both_area_filters_swap_together(self):
        """An inner and an outer area, as the fine-tuned model writes them."""
        q = ('[out:json];area["name:en"="Tokyo"]->.outer;'
             'area["name:en"="Shinjuku"]->.inner;'
             '(nwr["amenity"="cafe"](area.inner)(area.outer););out geom;')
        got = Q.swap_area_key(q)
        self.assertNotIn("name:en", got)
        self.assertIn('area["name"="Tokyo"]', got)
        self.assertIn('area["name"="Shinjuku"]', got)


if __name__ == "__main__":
    unittest.main()
