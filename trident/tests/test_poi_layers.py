"""Tests for showing one category alongside another, and fitting the answer.

Two behaviours that only show up when a search is not the first one: asking for
hotels and then cafes has to leave both on the map in different colours, and
the camera has to end up where the answer is rather than wherever it was.
"""
import json
import os
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "pi-hear"))

import geo

PI_POI = os.path.join(HERE, "..", "bin", "pi-poi")


class ColourSlotTest(unittest.TestCase):
    def test_the_pair_that_started_this_gets_two_colours(self):
        self.assertNotEqual(geo.colour_slot("hotel"), geo.colour_slot("cafe"))

    def test_synonyms_and_both_languages_are_one_colour(self):
        # カフェ and "coffee" are the same search, so they are the same colour;
        # two shades of the same thing would read as two searches.
        for words in (("cafe", "coffee", "カフェ", "喫茶店"),
                      ("hotel", "ホテル"),
                      ("toilet", "トイレ")):
            slots = {geo.colour_slot(w) for w in words}
            self.assertEqual(len(slots), 1, words)

    def test_every_slot_is_in_range(self):
        for name in geo.CATEGORIES:
            slot = geo.colour_slot(name)
            self.assertGreaterEqual(slot, 0)
            self.assertLess(slot, geo.COLOUR_COUNT)

    def test_an_unknown_category_still_gets_its_own_slot(self):
        # Not slot 0: two unrecognised searches landing on the palette's first
        # colour would look like one search.
        a, b = geo.colour_slot("zzz-unknown"), geo.colour_slot("qqq-other")
        self.assertNotEqual(a, b)

    def test_the_slot_order_is_append_only(self):
        # Reordering repaints every category. Pin the head of the list: the
        # colours people have got used to are the ones asked for most.
        self.assertEqual(geo.COLOUR_SLOTS[:3],
                         ("amenity=cafe", "tourism=hotel", "amenity=restaurant"))


class MarkerIdTest(unittest.TestCase):
    BODY = json.dumps({"elements": [
        {"id": 1, "lat": 34.40, "lon": 132.45, "tags": {"name": "A"}},
        {"id": 2, "lat": 34.39, "lon": 132.47, "tags": {"name": "B B"}},
    ]})

    def test_the_slot_rides_in_the_id(self):
        lines = geo.marker_lines(self.BODY, 1700000000, slot=1).splitlines()
        self.assertTrue(all(l.startswith("poi1-") for l in lines), lines)

    def test_without_a_slot_the_old_id_is_unchanged(self):
        # The Meshtastic feed and anything reading it predate this.
        lines = geo.marker_lines(self.BODY, 1700000000).splitlines()
        self.assertEqual(lines[0].split()[0], "poi1")

    def test_the_name_stays_one_field(self):
        # _safe_label folds spaces to underscores, so the name is a single
        # field and the line has exactly five. Worth pinning: the map parses
        # this with a stream extractor and a name that split would silently
        # lose everything after the first word.
        lines = geo.marker_lines(self.BODY, 1700000000, slot=3).splitlines()
        self.assertEqual(len(lines[1].split()), 5, lines[1])
        self.assertTrue(lines[1].endswith(" B_B"), lines[1])


class BboxTest(unittest.TestCase):
    def test_it_spans_every_marker(self):
        lines = ("poi0-1 34.40 132.45 1700000000 A\n"
                 "poi0-2 34.30 132.60 1700000000 B\n"
                 "poi0-3 34.50 132.40 1700000000 C")
        self.assertEqual(geo.bbox_of(lines), (34.30, 34.50, 132.40, 132.60))

    def test_no_markers_is_none_not_a_zero_box(self):
        # A box at 0,0 would fly the map into the Gulf of Guinea.
        self.assertIsNone(geo.bbox_of(""))
        self.assertIsNone(geo.bbox_of("garbage\nmore garbage"))

    def test_one_marker_is_a_flat_box_that_still_has_a_zoom(self):
        box = geo.bbox_of("poi0-1 34.40 132.45 1700000000 A")
        self.assertEqual(box, (34.40, 34.40, 132.45, 132.45))
        self.assertGreater(geo.zoom_for_bbox(*box), 0)


class FitZoomTest(unittest.TestCase):
    # 53 hotels within 1500m of Hiroshima station, from the deck.
    HOTELS = (34.376963, 34.39837, 132.445176, 132.471375)

    def test_it_is_tighter_than_the_place_lookup_zoom(self):
        # The bug this exists for: fitting through zoom_for_bbox put a 3km
        # box on a screen showing 12km, and the answer looked like a dot.
        self.assertGreater(geo.fit_zoom(*self.HOTELS),
                           geo.zoom_for_bbox(*self.HOTELS))

    def test_the_box_actually_fits_and_the_next_zoom_would_not(self):
        z = geo.fit_zoom(*self.HOTELS)
        south, north, west, east = self.HOTELS
        # Longitude spans 360/2^z * (720/256) degrees across the screen.
        shown = 360.0 * geo.SCREEN_W / (256.0 * 2 ** z)
        self.assertGreater(shown, east - west)
        if z < geo.MAX_ZOOM:
            tighter = 360.0 * geo.SCREEN_W / (256.0 * 2 ** (z + 1))
            self.assertLess(tighter, (east - west) / 0.88 * 0.999 + 1e9)

    def test_latitude_is_projected_not_just_subtracted(self):
        # The same box in degrees is taller on screen the further north it is.
        # Ignoring that put a fifth of the markers under the status bar.
        low = geo.fit_zoom(0.0, 0.0214, 10.0, 10.0262)
        high = geo.fit_zoom(70.0, 70.0214, 10.0, 10.0262)
        self.assertGreater(low, high)

    def test_it_never_exceeds_the_basemap(self):
        # planet.pmtiles stops at z14; past it the same tiles are stretched and
        # read as a rendering bug rather than as detail.
        self.assertEqual(geo.fit_zoom(34.4, 34.4, 132.45, 132.45), geo.MAX_ZOOM)

    def test_a_whole_country_still_fits(self):
        z = geo.fit_zoom(24.0, 46.0, 123.0, 146.0)
        self.assertGreaterEqual(z, geo.MIN_ZOOM)
        self.assertLess(z, 7)


class FocusTest(unittest.TestCase):
    """A dense knot with a few strays -- what a POI search actually looks like."""

    KNOT = ["poi0-%d 34.3930 132.4580 1 A" % i for i in range(20)]
    STRAYS = ["poi0-90 34.3300 132.4000 1 far-south",
              "poi0-91 34.4500 132.5200 1 far-north"]

    def block(self):
        return "\n".join(self.KNOT + self.STRAYS)

    def test_the_centre_is_the_knot_not_the_middle_of_the_box(self):
        # The strays are not symmetric, so the box centre drifts off the knot
        # and the answer ends up in a clot at the edge of the screen.
        lat, lon, _, _, _, _ = geo.focus_of(self.block())
        self.assertAlmostEqual(lat, 34.3930, places=4)
        self.assertAlmostEqual(lon, 132.4580, places=4)
        box = geo.bbox_of(self.block())
        self.assertNotAlmostEqual(lat, (box[0] + box[1]) / 2.0, places=3)

    def test_the_strays_do_not_set_the_extent(self):
        _, _, south, north, west, east = geo.focus_of(self.block())
        self.assertGreater(south, 34.38)
        self.assertLess(north, 34.40)
        full = geo.bbox_of(self.block())
        self.assertGreater(geo.fit_zoom(south, north, west, east),
                           geo.fit_zoom(*full))

    def test_keep_one_covers_everything(self):
        _, _, south, north, west, east = geo.focus_of(self.block(), keep=1.0)
        self.assertEqual((south, north, west, east), geo.bbox_of(self.block()))

    def test_a_handful_of_markers_are_all_kept(self):
        # With five results there is no distribution to trim, and every one of
        # them is the answer.
        few = "\n".join(self.KNOT[:3] + self.STRAYS[:1])
        _, _, south, north, west, east = geo.focus_of(few)
        self.assertEqual((south, north, west, east), geo.bbox_of(few))

    def test_no_markers_is_none(self):
        self.assertIsNone(geo.focus_of(""))

    def test_one_marker_is_its_own_centre(self):
        lat, lon, s_, n_, w_, e_ = geo.focus_of("poi0-1 34.4 132.45 1 A")
        self.assertEqual((lat, lon), (34.4, 132.45))
        self.assertEqual((s_, n_, w_, e_), (34.4, 34.4, 132.45, 132.45))


def fake_overpass(port_file, elements):
    """A tiny HTTP server standing in for Overpass, returning fixed elements."""
    import http.server
    import threading
    body = json.dumps({"elements": elements}).encode()

    class H(http.server.BaseHTTPRequestHandler):
        def do_POST(self):
            self.rfile.read(int(self.headers.get("Content-Length", 0)))
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *a):
            pass

    srv = http.server.HTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


def stop(srv):
    srv.shutdown()
    srv.server_close()


class PiPoiTest(unittest.TestCase):
    """The tool end to end, with Overpass faked and /dev/shm redirected."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="pi-poi.")
        self.markers = os.path.join(self.tmp, "markers")
        self.layers = os.path.join(self.tmp, "layers.json")
        self.flyto = os.path.join(self.tmp, "flyto")

    def run_poi(self, args, elements):
        srv = fake_overpass(None, elements)
        try:
            env = dict(os.environ)
            env.update({
                "PI_POI_OVERPASS": "http://127.0.0.1:%d" % srv.server_port,
                "PI_POI_MARKERS": self.markers,
                "PI_POI_LAYERS": self.layers,
                "PI_MAP_FLYTO": self.flyto,
                "PI_POI_CONF": os.path.join(self.tmp, "no-such-conf"),
            })
            return subprocess.run([sys.executable, PI_POI] + args,
                                  capture_output=True, text=True, env=env,
                                  timeout=60)
        finally:
            stop(srv)

    HOTELS = [{"id": 10, "lat": 34.40, "lon": 132.45, "tags": {"name": "H1"}},
              {"id": 11, "lat": 34.42, "lon": 132.47, "tags": {"name": "H2"}}]
    CAFES = [{"id": 20, "lat": 34.39, "lon": 132.44, "tags": {"name": "C1"}}]

    def markers_now(self):
        with open(self.markers) as fh:
            return [l for l in fh.read().splitlines() if l]

    def test_a_second_category_does_not_erase_the_first(self):
        r = self.run_poi(["hotel", "34.4", "132.45"], self.HOTELS)
        self.assertEqual(r.returncode, 0, r.stderr)
        r = self.run_poi(["cafe", "34.4", "132.45"], self.CAFES)
        self.assertEqual(r.returncode, 0, r.stderr)
        lines = self.markers_now()
        self.assertEqual(len(lines), 3, lines)
        slots = {l.split()[0][:4] for l in lines}
        self.assertEqual(slots, {"poi%d" % geo.colour_slot("hotel"),
                                 "poi%d" % geo.colour_slot("cafe")})

    def test_the_same_category_twice_replaces_itself(self):
        self.run_poi(["cafe", "34.4", "132.45"], self.CAFES)
        self.run_poi(["cafe", "34.4", "132.45"], self.CAFES)
        self.assertEqual(len(self.markers_now()), 1)

    def test_clear_takes_everything_off_and_stays_off(self):
        self.run_poi(["hotel", "34.4", "132.45"], self.HOTELS)
        self.run_poi(["clear"], [])
        self.assertEqual(self.markers_now(), [])
        # The layer file has to go too: left behind, the next search would put
        # the hotels straight back.
        self.run_poi(["cafe", "34.4", "132.45"], self.CAFES)
        self.assertEqual(len(self.markers_now()), 1)

    def test_a_category_that_finds_nothing_is_removed_not_kept(self):
        self.run_poi(["hotel", "34.4", "132.45"], self.HOTELS)
        self.run_poi(["hotel", "34.4", "132.45"], [])
        self.assertEqual(self.markers_now(), [])

    def test_it_fits_the_camera_to_what_it_found(self):
        r = self.run_poi(["hotel", "34.4", "132.45"], self.HOTELS)
        self.assertEqual(r.returncode, 0, r.stderr)
        with open(self.flyto) as fh:
            lat, lon, zoom = fh.read().split()
        # The median of the two hotels, not the search centre.
        self.assertAlmostEqual(float(lat), 34.41, places=4)
        self.assertAlmostEqual(float(lon), 132.46, places=4)
        self.assertGreater(int(zoom), 0)

    def test_it_fits_the_new_search_not_everything_on_the_map(self):
        # The question just asked is the one that should be on screen.
        self.run_poi(["hotel", "34.4", "132.45"], self.HOTELS)
        self.run_poi(["cafe", "34.4", "132.45"], self.CAFES)
        with open(self.flyto) as fh:
            lat, lon, _ = fh.read().split()
        self.assertAlmostEqual(float(lat), 34.39, places=4)

    def test_no_fit_leaves_the_camera_alone(self):
        self.run_poi(["hotel", "34.4", "132.45", "--no-fit"], self.HOTELS)
        self.assertFalse(os.path.exists(self.flyto))

    def test_finding_nothing_does_not_move_the_camera(self):
        # Flying to an empty bounding box is how you end up at 0,0.
        self.run_poi(["hotel", "34.4", "132.45"], [])
        self.assertFalse(os.path.exists(self.flyto))


if __name__ == "__main__":
    unittest.main()
