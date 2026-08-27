"""Tests for refusing a generated query whose tags do not exist in OSM.

The generated tag is the fallible part of the third tier, and it fails in a way
that looks like an empty area. Measured on pi5-deck, all three of these reached
Overpass and came back with nothing:

    shop=bookshop        (OSM uses shop=books, 56,507)
    shop=baker           (OSM uses shop=bakery, 246,173)
    amenity=coffee_shop  (OSM uses amenity=cafe, 618,379)

None of the three exists in OSM at all -- not rare, absent. A 7.8 MB table of
the 124,570 tags used at least 1,000 times catches every one of them in about
0.03 ms, before Overpass is asked.

The threshold is 1000 because text2geoql-dataset validates its training pairs
at exactly that. Inference and training then agree about what a real tag is.
"""
import os
import sqlite3
import tempfile
import unittest
import importlib.machinery
import importlib.util

HERE = os.path.dirname(os.path.abspath(__file__))
TOOL = os.path.join(HERE, "..", "bin", "pi-ql")


def load():
    loader = importlib.machinery.SourceFileLoader("pi_ql", TOOL)
    spec = importlib.util.spec_from_loader("pi_ql", loader)
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


Q = load()

REAL = [("amenity", "cafe", 618379), ("shop", "bakery", 246173),
        ("shop", "books", 56507), ("tourism", "hotel", 442848),
        ("cuisine", "ramen", 8213), ("amenity", "restaurant", 1534553)]


def a_table():
    path = os.path.join(tempfile.mkdtemp(prefix="tags."), "tags.db")
    db = sqlite3.connect(path)
    db.execute("create table tags (key text, value text, count_all integer)")
    db.executemany("insert into tags values (?,?,?)", REAL)
    db.execute("create unique index tags_kv on tags(key, value)")
    db.commit()
    db.close()
    return path


class ExtractTest(unittest.TestCase):
    def test_it_finds_the_pairs_in_a_query(self):
        q = ('[out:json];area(1)->.a;(nwr["shop"="bakery"](area.a);'
             'nwr["amenity"="cafe"](area.a););out geom;')
        self.assertEqual(sorted(Q.query_tags(q)),
                         [("amenity", "cafe"), ("shop", "bakery")])

    def test_a_regex_match_is_marked_not_dropped(self):
        """This test said the opposite an hour ago, and the earlier judgement
        was wrong. Patterns are checkable -- does any real tag under this key
        match -- and skipping them let through every wrong answer measured for
        a Japanese request. See RegexTest."""
        q = '[out:json];area(1)->.a;(nwr["shop"~"book"](area.a););out geom;'
        self.assertEqual(Q.query_tags(q), [("shop", "~book")])

    def test_descriptive_keys_are_not_classifiers(self):
        """text2geoql skips these for the same reason: they say what a thing is
        called, not what kind of thing it is, so their values are unbounded."""
        q = ('[out:json];area(1)->.a;(nwr["amenity"="cafe"]["name"="Doutor"]'
             '["addr:city"="Kyoto"](area.a););out geom;')
        self.assertEqual(Q.query_tags(q), [("amenity", "cafe")])

    def test_the_area_filter_is_not_a_tag(self):
        q = '[out:json];area["name:en"="Kyoto"]->.a;(nwr["shop"="books"](area.a););out geom;'
        self.assertEqual(Q.query_tags(q), [("shop", "books")])


class CheckTest(unittest.TestCase):
    def setUp(self):
        self.db = a_table()

    def test_real_tags_pass(self):
        self.assertEqual(Q.unknown_tags([("amenity", "cafe"),
                                         ("shop", "bakery")], self.db), [])

    def test_the_three_that_bit_are_refused(self):
        bad = [("shop", "bookshop"), ("shop", "baker"),
               ("amenity", "coffee_shop")]
        self.assertEqual(sorted(Q.unknown_tags(bad, self.db)), sorted(bad))

    def test_nothing_to_check_is_not_a_refusal(self):
        """A query with only a regex match has no checkable tag. Refusing it
        would throw away the answer that worked (shop~book found 69)."""
        self.assertEqual(Q.unknown_tags([], self.db), [])

    def test_a_missing_table_does_not_refuse_everything(self):
        """The deck must keep working with no tag table installed. Losing the
        check costs the old behaviour; refusing everything costs the feature."""
        self.assertEqual(Q.unknown_tags([("shop", "bookshop")],
                                        "/nonexistent/tags.db"), [])



class RegexTest(unittest.TestCase):
    """A regex filter can be checked too, and it is where the wrong answers are.

    An earlier version skipped them, on the grounds that `shop~book` was the
    form that worked (69 results) where the exact `shop=bookshop` failed. True,
    but incomplete: measured on pi5-deck, every wrong answer for a Japanese
    request was a regex.

        パン屋 in 広島市   -> shop~pan            0 results
        Bakeries in 広島市 -> shop~baker          works, but sloppy
        パン in 京都       -> amenity~restaurant  200 restaurants

    The table answers the first one: no shop value contains "pan" at all. It
    cannot answer the third -- amenity=restaurant is real, it is simply not
    what パン means. That is the model's limit, not a tag problem, and
    pretending a tag check catches it would be worse than saying so.
    """

    def setUp(self):
        self.db = a_table()

    def test_a_regex_matching_a_real_tag_passes(self):
        self.assertEqual(Q.unknown_tags([("shop", "~bake")], self.db), [])

    def test_a_regex_matching_nothing_is_refused(self):
        self.assertEqual(Q.unknown_tags([("shop", "~pan")], self.db),
                         [("shop", "~pan")])

    def test_the_marker_survives_extraction(self):
        """query_tags has to say which were patterns, or the check cannot tell
        an exact lookup from a search."""
        q = '[out:json];area(1)->.a;(nwr["shop"~"book"](area.a););out geom;'
        self.assertEqual(Q.query_tags(q), [("shop", "~book")])


if __name__ == "__main__":
    unittest.main()
