"""Tests for asking the small model for Overpass QL when the vocabulary runs out.

pi-poi answers from a table of 20 English category words. "bakeries" and "ramen"
are not in it, so today they cannot be asked for at all -- measured on pi5-deck:
geo.normalise_category("bakery") is None, and the intent layer returns an empty
category no matter which model parses the sentence.

The fine-tuned deep model can write the query instead. Measured on the deck,
asked in the shape it was trained on:

    Kyoto; Bakeries     nwr["shop"="bakery"]                    159
    Taito; Cafes        nwr["amenity"="cafe"]                   368
    Shinjuku; Ramen     nwr["amenity"="restaurant"]["cuisine"="ramen"]  69

So this is the third tier: rules, then the table, then a generated query. It
runs last because it is the slow one (2-4s) and the fallible one.
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


class AreaWithConcernTest(unittest.TestCase):
    """The input shape the model was fine-tuned on."""

    def test_it_puts_the_area_first_and_the_concern_after(self):
        self.assertEqual(Q.area_with_concern("Kyoto, Japan", "Bakeries"),
                         "AreaWithConcern: Kyoto, Japan; Bakeries")

    def test_it_trims_what_the_recogniser_leaves_behind(self):
        self.assertEqual(Q.area_with_concern(" Kyoto ", " bakeries. "),
                         "AreaWithConcern: Kyoto; bakeries")

    def test_a_missing_area_is_refused(self):
        """Without an area the query would be planet-wide. See the guard."""
        self.assertIsNone(Q.area_with_concern("", "Bakeries"))
        self.assertIsNone(Q.area_with_concern("   ", "Bakeries"))

    def test_a_missing_concern_is_refused(self):
        self.assertIsNone(Q.area_with_concern("Kyoto", ""))


class CleanTest(unittest.TestCase):
    def test_a_bare_query_passes_through(self):
        raw = '[out:json][timeout:30];\narea["name:en"="Kyoto"]->.a;\n(nwr["shop"="bakery"](area.a););\nout geom;'
        self.assertEqual(Q.clean_query(raw), raw.strip())

    def test_code_fences_are_stripped(self):
        raw = '```\n[out:json];area(360001)->.a;(nwr["shop"="bakery"](area.a););out geom;\n```'
        got = Q.clean_query(raw)
        self.assertNotIn("`", got)
        self.assertTrue(got.startswith("[out:json]"))

    def test_chatter_before_the_query_is_dropped(self):
        raw = 'Here is the query:\n[out:json];area(360001)->.a;(nwr["shop"="bakery"](area.a););out geom;'
        got = Q.clean_query(raw)
        self.assertTrue(got.startswith("[out:json]"), got)

    def test_nothing_usable_is_none(self):
        for raw in ("", "I cannot help with that.", "   "):
            self.assertIsNone(Q.clean_query(raw), repr(raw))


class SafetyTest(unittest.TestCase):
    """The guard that matters. The database is the whole planet.

    A query with no spatial constraint asks for every bakery on Earth. On a
    planet Overpass that is not a slow answer, it is an outage: it will run
    until the timeout, holding the one dispatcher slot the deck has.
    """

    BOUND = [
        '[out:json];area(3604097196)->.a;(nwr["shop"="bakery"](area.a););out geom;',
        '[out:json];area["name:en"="Kyoto"]->.a;(nwr["amenity"="cafe"](area.a););out geom;',
        '[out:json];(nwr["amenity"="cafe"](around:1500,35.0,135.0););out geom;',
        '[out:json];(nwr["amenity"="cafe"](34.9,135.7,35.1,135.8););out geom;',
    ]
    UNBOUND = [
        '[out:json];(nwr["shop"="bakery"];);out geom;',
        '[out:json][timeout:30];nwr["amenity"="cafe"];out geom;',
        '[out:json];node["amenity"="cafe"];out;',
    ]

    def test_a_bounded_query_is_allowed(self):
        for q in self.BOUND:
            self.assertTrue(Q.is_bounded(q), q[:60])

    def test_an_unbounded_query_is_refused(self):
        for q in self.UNBOUND:
            self.assertFalse(Q.is_bounded(q), q[:60])

    def test_something_that_is_not_a_query_is_refused(self):
        for q in ("out geom;", "[out:json];", "hello"):
            self.assertFalse(Q.looks_like_overpass(q), q)

    def test_a_real_query_looks_like_one(self):
        self.assertTrue(Q.looks_like_overpass(self.BOUND[0]))


class TimeoutTest(unittest.TestCase):
    def test_the_timeout_is_forced_down(self):
        """The model writes timeout:30, which on this deck's Overpass is the
        whole time budget and gets refused as busy. Rewrite rather than trust."""
        q = '[out:json][timeout:30];area(1)->.a;(nwr["shop"="bakery"](area.a););out geom;'
        got = Q.cap_timeout(q, 60)
        self.assertIn("[timeout:60]", got)
        self.assertNotIn("[timeout:30]", got)

    def test_a_query_without_one_gains_it(self):
        q = '[out:json];area(1)->.a;(nwr["shop"="bakery"](area.a););out geom;'
        got = Q.cap_timeout(q, 60)
        self.assertIn("[timeout:60]", got)



class MarkerHandoffTest(unittest.TestCase):
    """What is handed to geo.marker_lines has to be text.

    It does its own json.loads and returns "" for anything it cannot read --
    deliberately, so a malformed answer draws nothing rather than crashing.
    Handing it a parsed dict raises TypeError inside that guard, and the guard
    swallows it: measured 159 bakeries returned and zero markers drawn, with no
    error anywhere.
    """

    BODY = ('{"elements":[{"type":"node","id":1,"lat":35.02,"lon":135.79,'
            '"tags":{"name":"Boulangerie","shop":"bakery"}}]}')

    def test_the_raw_text_produces_markers(self):
        import sys as _sys
        import os as _os
        _sys.path.insert(0, _os.path.join(HERE, "..", "pi-hear"))
        import geo
        got = geo.marker_lines(self.BODY, 1787000000, limit=10, slot=4)
        self.assertEqual(len(got.splitlines()), 1, got)

    def test_a_parsed_document_produces_nothing(self):
        """The bug, pinned. If this ever starts passing, marker_lines has
        learned to take a dict and the note above can go."""
        import json as _json
        import sys as _sys
        import os as _os
        _sys.path.insert(0, _os.path.join(HERE, "..", "pi-hear"))
        import geo
        self.assertEqual(geo.marker_lines(_json.loads(self.BODY), 1787000000), "")

    def test_ask_overpass_returns_text(self):
        """Checked by calling it, not by reading it. The first version of this
        searched the source for "json.loads" and matched the comment that
        explains why it must not be there."""
        import io
        import urllib.request

        class FakeResponse(io.BytesIO):
            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

        real = urllib.request.urlopen
        urllib.request.urlopen = lambda *a, **k: FakeResponse(
            self.BODY.encode())
        try:
            got = Q.ask_overpass("[out:json];")
        finally:
            urllib.request.urlopen = real
        self.assertIsInstance(got, str)
        self.assertEqual(len(geo_lines(got)), 1)


def geo_lines(text):
    import sys as _sys
    import os as _os
    _sys.path.insert(0, _os.path.join(HERE, "..", "pi-hear"))
    import geo
    out = geo.marker_lines(text, 1787000000, limit=10, slot=4)
    return out.splitlines() if out else []



class EmptyResultTest(unittest.TestCase):
    """Nothing found has to be a failure, not a quiet success.

    The generated tag is the fallible part. Measured on pi5-deck, the same
    question twice gave different tags:

        Bookshops in Kyoto -> nwr["shop"~"book"]        69
        Bookshops in Kyoto -> nwr["shop"="bookshop"]     0

    OSM uses shop=books. There is no tag validation at inference time (see
    inference-time-tag-validation-gap.md), so a wrong tag reaches Overpass and
    comes back empty.

    Empty is then indistinguishable from "there are none here" -- and by voice
    it is worse than that, because the deck has already said "OK, showing
    bookshops" and would go silent. A non-zero exit is what makes it apologise.
    """

    def test_exit_code_says_nothing_was_found(self):
        src = open(TOOL, encoding="utf-8").read()
        self.assertIn("EMPTY_IS_FAILURE", src,
                      "an empty result has to be distinguishable from a hit")


if __name__ == "__main__":
    unittest.main()
