"""Zooming to a thing that has a name.

「広島駅にズームして」and 「広島国際会議場にズームして」. Both were heard perfectly
and both flew to the city of Hiroshima, because two other things got to the
sentence first: the nine-city place table found 広島 inside 広島駅, and the
category rules found 駅 on the end of it.

Neither is in this deck's Nominatim -- it was imported with IMPORT_STYLE=admin,
so 広島駅 resolves to 广益街道 in China and 広島国際会議場 to nothing at all.
Overpass has both.
"""
import json
import os
import subprocess
import sys
import tempfile
import threading
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "pi-hear"))

import intent

PI_ZOOM = os.path.join(HERE, "..", "bin", "pi-zoom")


class ZoomRuleTest(unittest.TestCase):
    def plan(self, said):
        return intent.for_voice(said, lang="ja")

    def test_japanese(self):
        for said, want in (
            ("広島駅にズームして", "広島駅"),
            ("広島駅にズームインして", "広島駅"),
            ("広島国際会議場にズームして", "広島国際会議場"),
            ("東京駅にズーム", "東京駅"),
        ):
            plan = self.plan(said)
            self.assertIsNotNone(plan, said)
            self.assertEqual(plan["tool"], "pi-zoom", said)
            self.assertEqual(plan["args"], [want], said)

    def test_english(self):
        for said, want in (
            ("zoom to Hiroshima Station", "Hiroshima Station"),
            ("zoom in on the convention center", "convention center"),
            ("zoom into Ueno Park", "Ueno Park"),
        ):
            self.assertEqual(self.plan(said)["args"], [want], said)

    def test_the_station_on_the_end_is_not_a_category_search(self):
        # 広島駅 ends in 駅 and the category rules would happily turn the whole
        # sentence into "every station near Hiroshima". The verb says otherwise.
        plan = self.plan("広島駅にズームして")
        self.assertNotEqual(plan["tool"], "pi-poi")

    def test_the_city_table_is_told_to_stay_out(self):
        # The voice loop checks nine cities before the rules run, and finds
        # 広島 inside 広島駅. This is the flag that stops it.
        self.assertTrue(intent.is_zoom_request("広島駅にズームして"))
        self.assertTrue(intent.is_zoom_request("zoom to Hiroshima Station"))
        self.assertFalse(intent.is_zoom_request("広島を表示して"))
        self.assertFalse(intent.is_zoom_request("show cafes"))

    def test_zooming_at_nothing_in_particular_is_not_this(self):
        # "zoom in" is about the current view: a different request, and not one
        # that exists. Reading it as a search for a place called "in" would be
        # worse than not answering.
        for said in ("ズームして", "もっとズームして", "ちょっとズーム",
                     "zoom in", "zoom out"):
            plan = self.plan(said)
            if plan is not None:
                self.assertNotEqual(plan["tool"], "pi-zoom", said)

    def test_the_model_is_not_offered_this_either(self):
        # An Overpass name search is expensive and the model invents names.
        self.assertNotIn("zoom_to", intent.MODEL_INTENTS)
        self.assertNotIn("zoom_to", intent.grammar())

    def test_the_pins_from_the_last_thing_are_cleared(self):
        # Same reason as flying anywhere else: the pins belong to where the map
        # was, not to where it is going.
        self.assertTrue(self.plan("広島駅にズームして")["clear_pins"])


def fake_overpass(elements):
    import http.server
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


class PiZoomTest(unittest.TestCase):
    # 広島駅 as Overpass really returns it: one station spread over five nodes,
    # plus the way that is the building.
    STATION = [
        {"type": "node", "id": 1, "lat": 34.39622, "lon": 132.47538},
        {"type": "node", "id": 2, "lat": 34.39566, "lon": 132.47635},
        {"type": "node", "id": 3, "lat": 34.39729, "lon": 132.47331},
        {"type": "way", "id": 9, "center": {"lat": 34.39650, "lon": 132.47500}},
    ]

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="pi-zoom.")
        self.flyto = os.path.join(self.tmp, "flyto")
        self.markers = os.path.join(self.tmp, "markers")
        self.layers = os.path.join(self.tmp, "layers.json")
        with open(self.flyto, "w") as fh:
            fh.write("34.3853 132.4553 12\n")

    def run_zoom(self, args, elements):
        srv = fake_overpass(elements)
        try:
            env = dict(os.environ)
            env.update({
                "PI_POI_OVERPASS": "http://127.0.0.1:%d" % srv.server_port,
                "PI_MAP_FLYTO": self.flyto,
                "PI_POI_MARKERS": self.markers,
                "PI_POI_LAYERS": self.layers,
                "PI_POI_GPS": os.path.join(self.tmp, "no-gps"),
            })
            return subprocess.run([sys.executable, PI_ZOOM] + args,
                                  capture_output=True, text=True, env=env,
                                  timeout=60)
        finally:
            srv.shutdown()
            srv.server_close()

    def test_it_flies_to_the_named_thing(self):
        r = self.run_zoom(["広島駅"], self.STATION)
        self.assertEqual(r.returncode, 0, r.stderr)
        with open(self.flyto) as fh:
            lat, lon, zoom = fh.read().split()
        self.assertAlmostEqual(float(lat), 34.39650, places=4)
        self.assertGreater(int(zoom), 12)

    def test_the_way_wins_over_the_nodes(self):
        # A relation or way has real extent; a node is somebody's guess at the
        # middle of it.
        r = self.run_zoom(["広島駅", "--json"], self.STATION)
        doc = json.loads(r.stdout)
        self.assertEqual(doc["type"], "way")
        self.assertEqual(doc["matches"], 4)

    def test_the_view_covers_all_of_them(self):
        # Five nodes 200m apart are one station, so the answer is the group.
        r = self.run_zoom(["広島駅", "--json"], self.STATION)
        self.assertLessEqual(json.loads(r.stdout)["zoom"], 14)

    def test_it_pins_what_it_found(self):
        self.run_zoom(["広島駅"], self.STATION)
        with open(self.markers) as fh:
            lines = [l for l in fh.read().splitlines() if l]
        self.assertEqual(len(lines), 1)
        self.assertTrue(lines[0].endswith("広島駅"), lines[0])

    def test_the_pin_lands_in_a_layer_so_clear_removes_it(self):
        self.run_zoom(["広島駅"], self.STATION)
        with open(self.layers) as fh:
            self.assertIn("zoom", json.load(fh))

    def test_no_pin_when_asked(self):
        self.run_zoom(["広島駅", "--no-pin"], self.STATION)
        self.assertFalse(os.path.exists(self.markers))

    def test_nothing_found_is_an_error_and_moves_nothing(self):
        before = open(self.flyto).read()
        r = self.run_zoom(["存在しない場所"], [])
        self.assertNotEqual(r.returncode, 0)
        self.assertEqual(open(self.flyto).read(), before)

    def test_a_name_with_a_quote_in_it_does_not_break_the_query(self):
        # The name came out of a microphone and Overpass QL is a language.
        r = self.run_zoom(['say "hi"'], self.STATION)
        self.assertEqual(r.returncode, 0, r.stderr)


if __name__ == "__main__":
    unittest.main()
