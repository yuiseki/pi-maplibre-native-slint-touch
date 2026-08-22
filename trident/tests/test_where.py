"""Tests for deciding where "here" is when nobody said.

"show cafes on map" names no place. After a reboot the map is showing the whole
world -- the style starts at 0,0 zoom 1 -- and /dev/shm is empty, so there is
genuinely nothing to centre on and the honest answer used to be to refuse.
There are better answers than refusing, in a definite order.
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                "..", "pi-hear"))
import geo


class WhereTest(unittest.TestCase):
    def setUp(self):
        import tempfile
        self.dir = tempfile.mkdtemp(prefix="pi-poi-where.")

    def path(self, name, body=None):
        p = os.path.join(self.dir, name)
        if body is not None:
            with open(p, "w") as fh:
                fh.write(body)
        return p

    def test_the_maps_last_flyto_wins(self):
        r = geo.where_now(flyto=self.path("f", "34.39 132.45 11\n"),
                          gps=self.path("g", "43.06 141.35 1700000000\n"),
                          home="35.68 139.76")
        self.assertEqual((round(r[0], 2), round(r[1], 2)), (34.39, 132.45))

    def test_the_decks_own_fix_comes_next(self):
        r = geo.where_now(flyto=self.path("missing"),
                          gps=self.path("g", "43.06 141.35 1700000000\n"),
                          home="35.68 139.76")
        self.assertEqual((round(r[0], 2), round(r[1], 2)), (43.06, 141.35))

    def test_then_the_configured_home(self):
        r = geo.where_now(flyto=self.path("missing"),
                          gps=self.path("also-missing"),
                          home="35.68 139.76")
        self.assertEqual((round(r[0], 2), round(r[1], 2)), (35.68, 139.76))

    def test_nothing_at_all_is_none(self):
        # Refusing is still right when there is no answer; guessing a city the
        # user has never been to is worse than saying so.
        self.assertIsNone(geo.where_now(flyto=self.path("a"),
                                        gps=self.path("b"), home=""))

    def test_an_empty_flyto_falls_through(self):
        # /dev/shm/pi-map-flyto exists but is empty after `pi-poi clear`-ish
        # accidents; an empty file is not a position.
        r = geo.where_now(flyto=self.path("f", ""),
                          gps=self.path("g", "43.06 141.35 1700000000\n"),
                          home="")
        self.assertEqual(round(r[0], 2), 43.06)

    def test_a_malformed_flyto_falls_through(self):
        r = geo.where_now(flyto=self.path("f", "not a position\n"),
                          gps=self.path("missing"), home="35.68 139.76")
        self.assertEqual(round(r[0], 2), 35.68)

    def test_a_malformed_home_is_not_a_position(self):
        self.assertIsNone(geo.where_now(flyto=self.path("a"),
                                        gps=self.path("b"), home="nonsense"))

    def test_out_of_range_coordinates_are_rejected(self):
        # A garbled file can parse as two floats and still not be a place.
        self.assertIsNone(geo.where_now(flyto=self.path("f", "999 999\n"),
                                        gps=self.path("b"), home=""))

    def test_it_says_which_source_was_used(self):
        r = geo.where_now(flyto=self.path("missing"),
                          gps=self.path("g", "43.06 141.35 1700000000\n"),
                          home="35.68 139.76")
        self.assertEqual(r[2], "gps")

class ConfFileTest(unittest.TestCase):
    """The tools are run by hand and by pi-hear, not only by systemd.

    An EnvironmentFile only reaches a unit's own process. pi-poi is invoked
    from the voice loop and from a shell, and in both cases /etc/default was
    never read -- the home position was configured and had no effect. pi-say
    already reads its own file for the same reason.
    """

    def read(self, body):
        import tempfile
        with tempfile.NamedTemporaryFile("w", suffix=".conf",
                                         delete=False) as fh:
            fh.write(body)
            path = fh.name
        return geo.read_conf(path)

    def test_reads_a_shell_style_assignment(self):
        d = self.read("PI_POI_HOME=34.39 132.45" + chr(10))
        self.assertEqual(d["PI_POI_HOME"], "34.39 132.45")

    def test_ignores_comments_and_blanks(self):
        nl = chr(10)
        d = self.read("# a comment" + nl + nl + "PI_POI_RADIUS=1500" + nl)
        self.assertEqual(d["PI_POI_RADIUS"], "1500")
        self.assertEqual(len(d), 1)

    def test_strips_quotes(self):
        nl = chr(10)
        d = self.read('PI_POI_HOME="34.39 132.45"' + nl + "X='y'" + nl)
        self.assertEqual(d["PI_POI_HOME"], "34.39 132.45")
        self.assertEqual(d["X"], "y")

    def test_a_missing_file_is_empty_not_an_error(self):
        self.assertEqual(geo.read_conf("/nonexistent/pi-poi"), {})

    def test_a_line_without_an_equals_is_skipped(self):
        nl = chr(10)
        self.assertEqual(self.read("nonsense line" + nl + "A=1" + nl),
                         {"A": "1"})


if __name__ == "__main__":
    unittest.main()
