"""Tests for pi-wigle, which asks WiGLE where the deck is when GPS cannot say.

A GPS receiver indoors reports fix=0 and one satellite in view, and that is not
a fault to be fixed -- it is what the sky looks like from inside a building. The
deck then falls back to a configured home, which is the honest answer at home
and the wrong one in a hotel in another city.

The visible access points are a different kind of sky, and they are strongest
exactly where GPS is weakest. WiGLE maps BSSIDs to positions, so the same scan
that finds no satellites finds thirty landmarks.

The parts worth testing are the ones that decide a number: turning nmcli's
0-100 quality back into dBm, weighting a centroid by it, and reading WiGLE's
reply. None of them need a radio, a key, or a network.

The cache format is deliberately the same as the firmware's
(m5-cardputer-offgrid-tiny-map, src/wigle.cpp), so a cache built by either side
is readable by the other -- the Cardputer surveys on foot and the Pi has the
disk to keep it.
"""
import importlib.machinery
import importlib.util
import os
import sys
import tempfile
import time
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
TOOL = os.path.join(HERE, "..", "bin", "pi-wigle")


def load():
    loader = importlib.machinery.SourceFileLoader("pi_wigle", TOOL)
    spec = importlib.util.spec_from_loader("pi_wigle", loader)
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


w = load()


class Bssid(unittest.TestCase):
    def test_colons_are_dropped_and_case_folded(self):
        self.assertEqual(w.normalize_bssid("AA:BB:CC:DD:EE:FF"), "aabbccddeeff")

    def test_other_separators_work_too(self):
        self.assertEqual(w.normalize_bssid("aa-bb-cc-dd-ee-ff"), "aabbccddeeff")
        self.assertEqual(w.normalize_bssid("aabbccddeeff"), "aabbccddeeff")

    def test_nmcli_escaping_is_undone(self):
        # `nmcli -t` escapes the separator, so a BSSID arrives as
        # `92\:85\:C4\:75\:D6\:6A` even before it is split into fields.
        self.assertEqual(w.normalize_bssid(r"92\:85\:C4\:75\:D6\:6A"),
                         "9285c475d66a")

    def test_anything_not_twelve_hex_digits_is_refused(self):
        for bad in ("", "aabbccddee", "aabbccddeeffaa", "zz:bb:cc:dd:ee:ff",
                    "aa:bb:cc:dd:ee"):
            self.assertIsNone(w.normalize_bssid(bad), bad)


class Netid(unittest.TestCase):
    """What goes in the query string is not what goes in the cache path.

    WiGLE validates `netid` and refuses bare hex outright -- the reply is
    HTTP 200 with `success:false, "BSSID/MAC fails validation"`, which is easy
    to misread as "this access point is not in the database". The cache path
    wants the bare form (and the firmware's cache is laid out that way), so the
    two representations have to be kept apart deliberately.
    """

    def test_bare_hex_becomes_a_separated_mac(self):
        self.assertEqual(w.bssid_to_netid("100c6beccd08"), "10:0C:6B:EC:CD:08")

    def test_it_is_uppercase(self):
        self.assertEqual(w.bssid_to_netid("aabbccddeeff"), "AA:BB:CC:DD:EE:FF")

    def test_anything_not_twelve_hex_digits_is_refused(self):
        for bad in ("", "aabbccddee", "zzzzzzzzzzzz"):
            self.assertIsNone(w.bssid_to_netid(bad), bad)


class QualityToDbm(unittest.TestCase):
    """nmcli reports 0-100, and the weighting needs dBm.

    NetworkManager takes it from wpa_supplicant, which computes
    `quality = 2 * (dBm + 100)` clamped to 0..100. Inverting that is exact in
    the middle of the range and saturates at the ends, which is the same
    information the scan actually carried.
    """

    def test_the_documented_midpoints(self):
        self.assertEqual(w.quality_to_dbm(90), -55)
        self.assertEqual(w.quality_to_dbm(72), -64)
        self.assertEqual(w.quality_to_dbm(50), -75)

    def test_the_ends_saturate_rather_than_run_away(self):
        self.assertEqual(w.quality_to_dbm(100), -50)
        self.assertEqual(w.quality_to_dbm(0), -100)

    def test_out_of_range_input_is_clamped(self):
        self.assertEqual(w.quality_to_dbm(120), -50)
        self.assertEqual(w.quality_to_dbm(-5), -100)


class Estimate(unittest.TestCase):
    """Centroid weighted by 10^(rssi/20), as the firmware does."""

    def test_a_single_ap_is_its_own_answer(self):
        self.assertEqual(w.estimate([(34.0, 132.0, -60)]), (34.0, 132.0))

    def test_equal_signals_give_the_plain_midpoint(self):
        lat, lon = w.estimate([(34.0, 132.0, -60), (36.0, 134.0, -60)])
        self.assertAlmostEqual(lat, 35.0, places=6)
        self.assertAlmostEqual(lon, 133.0, places=6)

    def test_the_stronger_ap_pulls_the_answer_towards_itself(self):
        lat, lon = w.estimate([(34.0, 132.0, -40), (36.0, 134.0, -80)])
        self.assertLess(lat, 34.1)      # 100x the weight
        self.assertGreater(lat, 34.0)

    def test_no_access_points_is_no_answer(self):
        self.assertIsNone(w.estimate([]))


class ParseResponse(unittest.TestCase):
    def test_a_hit_yields_the_trilateration(self):
        r = w.parse_response(
            '{"success":true,"totalResults":1,"results":[{"trilat":34.3853,'
            '"trilong":132.4553,"ssid":"x"}]}')
        self.assertTrue(r["ok"])
        self.assertEqual(r["total"], 1)
        self.assertTrue(r["has_pos"])
        self.assertAlmostEqual(r["lat"], 34.3853)
        self.assertAlmostEqual(r["lon"], 132.4553)

    def test_zero_results_is_a_valid_answer_of_no(self):
        # Distinct from a failed request: this AP is genuinely not in WiGLE,
        # and remembering that is what stops it being asked again.
        r = w.parse_response('{"success":true,"totalResults":0,"results":[]}')
        self.assertTrue(r["ok"])
        self.assertEqual(r["total"], 0)
        self.assertFalse(r["has_pos"])

    def test_the_first_result_wins_when_there_are_several(self):
        r = w.parse_response(
            '{"totalResults":2,"results":[{"trilat":1.5,"trilong":2.5},'
            '{"trilat":9.9,"trilong":9.9}]}')
        self.assertAlmostEqual(r["lat"], 1.5)
        self.assertAlmostEqual(r["lon"], 2.5)

    def test_rubbish_is_not_mistaken_for_an_answer(self):
        for bad in ("", "not json", "{}", "[]"):
            self.assertFalse(w.parse_response(bad)["ok"], bad)

    def test_a_null_position_is_not_a_position(self):
        # WiGLE returns the record with null coordinates for a network it knows
        # of but has never located.
        r = w.parse_response(
            '{"totalResults":1,"results":[{"trilat":null,"trilong":null}]}')
        self.assertTrue(r["ok"])
        self.assertFalse(r["has_pos"])


class Cache(unittest.TestCase):
    """Same layout and same one-line format as the firmware's."""

    def test_the_path_is_split_two_levels_deep(self):
        self.assertEqual(w.cache_path("/c", "aabbccddeeff"),
                         "/c/a/aa/aabbccddeeff.txt")

    def test_a_hit_round_trips(self):
        line = w.format_cache(True, 34.3853, 132.4553)
        self.assertTrue(line.startswith("1 "))
        self.assertEqual(w.parse_cache(line), (True, 34.3853, 132.4553))

    def test_a_miss_round_trips(self):
        # Misses are remembered too: access points do not move, and an unlisted
        # one would otherwise be looked up on every single scan.
        self.assertEqual(w.format_cache(False, 0, 0).strip(), "0")
        self.assertEqual(w.parse_cache("0"), (False, 0.0, 0.0))

    def test_a_damaged_line_is_not_half_believed(self):
        for bad in ("", "1", "1 34.0", "x 1 2", "1 a b"):
            self.assertIsNone(w.parse_cache(bad), bad)

    def test_reading_and_writing_go_through_the_disk(self):
        d = tempfile.mkdtemp(prefix="pi-wigle.")
        self.assertIsNone(w.cache_get(d, "aabbccddeeff"))
        w.cache_put(d, "aabbccddeeff", True, 1.25, 2.5)
        self.assertEqual(w.cache_get(d, "aabbccddeeff"), (True, 1.25, 2.5))
        w.cache_put(d, "ffeeddccbbaa", False, 0, 0)
        self.assertEqual(w.cache_get(d, "ffeeddccbbaa"), (False, 0.0, 0.0))


class ScanParsing(unittest.TestCase):
    def test_nmcli_terse_output_becomes_bssid_and_dbm(self):
        # The real shape, escaping and all, including an AP with a blank SSID.
        out = ("92\\:85\\:C4\\:75\\:D6\\:6A:90:2437 MHz:drhhiroshima\n"
               "02\\:1B\\:F0\\:31\\:79\\:61:75:5240 MHz:\n")
        aps = w.parse_scan(out)
        self.assertEqual(len(aps), 2)
        self.assertEqual(aps[0][0], "9285c475d66a")
        self.assertEqual(aps[0][1], -55)
        self.assertEqual(aps[1][0], "021bf0317961")

    def test_lines_that_are_not_access_points_are_dropped(self):
        self.assertEqual(w.parse_scan("\n:::\ngarbage\n"), [])

    def test_the_strongest_are_kept_first(self):
        out = ("AA\\:AA\\:AA\\:AA\\:AA\\:AA:40:2437 MHz:weak\n"
               "BB\\:BB\\:BB\\:BB\\:BB\\:BB:90:2437 MHz:strong\n")
        aps = w.parse_scan(out)
        self.assertEqual(aps[0][0], "bbbbbbbbbbbb")


class Publish(unittest.TestCase):
    """The five fields pi-gps writes, plus a sixth naming the writer.

    pi-mesh is the only thing that can tell the Meshtastic node where it is,
    and it must not send back a position that came from the node itself. The
    five-field format cannot say which is which, so the writer signs its work.
    Readers that only ever wanted five are unaffected: they index the fields
    they know about.
    """

    def out(self):
        return os.path.join(tempfile.mkdtemp(prefix="pi-wigle-pub."), "pi-gps")

    def test_the_first_five_fields_are_what_pi_gps_writes(self):
        p = self.out()
        w.publish(p, 34.387604, 132.454813)
        f = open(p).read().split()
        self.assertEqual(f[0], "34.387604")
        self.assertEqual(f[1], "132.454813")
        self.assertEqual(f[2], "1")          # a position is a position
        self.assertEqual(f[3], "0")          # from no satellites
        self.assertEqual(f[4], "0")

    def test_the_sixth_field_says_who_wrote_it(self):
        p = self.out()
        w.publish(p, 1.0, 2.0)
        self.assertEqual(open(p).read().split()[5], "wigle")

    def test_it_is_one_line(self):
        p = self.out()
        w.publish(p, 1.0, 2.0)
        self.assertEqual(len(open(p).read().strip().splitlines()), 1)

    def test_a_live_receiver_is_not_overwritten(self):
        p = self.out()
        with open(p, "w") as fh:
            fh.write("34.0 132.0 1 7 9\n")
        self.assertTrue(w.gps_is_live(p))

    def test_a_stale_file_is_free_to_take(self):
        p = self.out()
        with open(p, "w") as fh:
            fh.write("34.0 132.0 1 7 9\n")
        self.assertFalse(w.gps_is_live(p, now=time.time() + 3600))

    def test_a_missing_file_is_free_to_take(self):
        self.assertFalse(w.gps_is_live("/nonexistent/pi-gps"))


class Key(unittest.TestCase):
    def test_name_and_token_split_at_the_first_colon(self):
        self.assertEqual(w.parse_key(" AID123:tok:en \n"), ("AID123", "tok:en"))

    def test_a_line_without_a_colon_is_not_a_key(self):
        self.assertIsNone(w.parse_key("just-a-token"))

    def test_an_empty_half_is_not_a_key(self):
        self.assertIsNone(w.parse_key(":token"))
        self.assertIsNone(w.parse_key("name:"))

    def test_the_encoded_form_from_wigles_account_page(self):
        # WiGLE shows the pair pre-encoded as "Encoded for use", which is the
        # base64 the Authorization header wants. Taking it verbatim removes a
        # step where the two halves get swapped by hand.
        import base64
        enc = base64.b64encode(b"AID123:secrettoken").decode()
        self.assertEqual(w.parse_encoded(enc), ("AID123", "secrettoken"))

    def test_encoded_rubbish_is_refused(self):
        for bad in ("", "!!!!", "bm90LWEtcGFpcg=="):   # last decodes to "not-a-pair"
            self.assertIsNone(w.parse_encoded(bad), bad)


class KeyFromEnvFile(unittest.TestCase):
    """A shell-style env file, which is where this machine already keeps it."""

    def write(self, text):
        d = tempfile.mkdtemp(prefix="pi-wigle-env.")
        p = os.path.join(d, ".env")
        with open(p, "w") as fh:
            fh.write(text)
        return p

    def test_the_encoded_variable_is_enough_on_its_own(self):
        import base64
        enc = base64.b64encode(b"AID:tok").decode()
        p = self.write("UNRELATED=1\nWIGLE_API_ENCODED=%s\n" % enc)
        self.assertEqual(w.key_from_env_file(p), ("AID", "tok"))

    def test_name_and_token_variables_also_work(self):
        p = self.write("WIGLE_API_NAME=AID\nWIGLE_API_TOKEN=tok\n")
        self.assertEqual(w.key_from_env_file(p), ("AID", "tok"))

    def test_quotes_and_export_are_tolerated(self):
        p = self.write('export WIGLE_API_NAME="AID"\n'
                       "export WIGLE_API_TOKEN='tok'\n")
        self.assertEqual(w.key_from_env_file(p), ("AID", "tok"))

    def test_a_file_with_nothing_relevant_is_no_key(self):
        self.assertIsNone(w.key_from_env_file(self.write("FOO=bar\n")))

    def test_a_missing_file_is_no_key(self):
        self.assertIsNone(w.key_from_env_file("/nonexistent/.env"))

    def test_a_name_with_no_token_is_no_key(self):
        p = self.write("WIGLE_API_NAME=AID\n")
        self.assertIsNone(w.key_from_env_file(p))


if __name__ == "__main__":
    unittest.main()
