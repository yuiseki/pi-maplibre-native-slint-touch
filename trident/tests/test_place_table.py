"""Tests for when the nine-city place table should stand aside.

The table matches a city anywhere in a sentence, which is right for "show
Hiroshima" and wrong for anything that also names a thing to show. It already
stands aside for zoom requests -- 「広島駅にズームして」 finds 広島 and would
fly to the city, discarding the half that said which station.

The same hole was open for POI searches, and wider than it looked. Traced on
pi5-deck 2026-08-27:

    '京都のパン屋を表示して'   -> WAKE -> flyto kyoto
    '広島市のパン屋を表示して' -> WAKE -> flyto hiroshima

and it is not a Japanese problem. `Show cafes in Kyoto.` matches the table too,
so a POI search naming a place had never reached pi-poi by voice at all. The
routing tests passed because they called for_voice directly, which is not the
path an utterance takes.
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                "..", "pi-hear"))
import intent


class NamesAThingTest(unittest.TestCase):
    THINGS = [
        "京都のパン屋を表示して",
        "広島市のパン屋を表示して",
        "新宿のラーメン屋を表示して",
        "京都のカフェを表示して",
        "この辺のホテルを表示して",
        "Show cafes in Kyoto.",
        "Find bakeries in Kyoto.",
        "Show bookshops in Kyoto.",
        "Show me ramen shops in Shinjuku.",
    ]
    PLACES_ONLY = [
        "京都を表示して",
        "広島を表示して",
        "沖縄を表示します",
        "Show Kyoto.",
        "Show Tokyo!",
        "Shou Hiroshima",
        "京都",
    ]

    def test_a_sentence_that_names_a_thing_holds_the_table_off(self):
        for said in self.THINGS:
            self.assertTrue(intent.names_a_thing(said), repr(said))

    def test_a_bare_place_still_goes_to_the_table(self):
        """This is what the table is for, and it is the fast path: no model, no
        round trip. Breaking it to fix the other case would be a bad trade."""
        for said in self.PLACES_ONLY:
            self.assertFalse(intent.names_a_thing(said), repr(said))

    def test_a_zoom_is_not_claimed_by_this(self):
        """is_zoom_request already holds the table off for those, and the two
        must not disagree about the same sentence."""
        for said in ("広島駅にズームして", "Zoom into Hiroshima station."):
            self.assertTrue(intent.is_zoom_request(said), repr(said))


class PlaceCleanupTest(unittest.TestCase):
    """The model puts the thing inside the place.

    Measured on pi5-deck, asked in Japanese:

        京都のパン屋を表示して   -> place='京都のパン屋'  category='パン'
        新宿のラーメン屋を表示して -> place='新宿のラーメン屋' category='ラーメン屋'
        広島市のパン屋を表示して -> place='広島市'        category='パン屋'

    Two of three carry the concern in the place. Sent on as-is, the geocoder
    looks up "京都のパン屋" and finds nothing.
    """

    def test_the_concern_is_taken_out_of_the_place(self):
        self.assertEqual(intent.place_without_concern("京都のパン屋", "パン"),
                         "京都")
        self.assertEqual(
            intent.place_without_concern("新宿のラーメン屋", "ラーメン屋"),
            "新宿")

    def test_a_clean_place_is_left_alone(self):
        self.assertEqual(intent.place_without_concern("広島市", "パン屋"),
                         "広島市")
        self.assertEqual(intent.place_without_concern("Kyoto", "bakeries"),
                         "Kyoto")

    def test_an_unrelated_no_is_kept(self):
        """Not every の joins a place to a thing. 「四条河原町の交差点」 is one
        name; stripping at の would leave 四条河原町 and lose the crossing."""
        self.assertEqual(
            intent.place_without_concern("四条河原町の交差点", "パン屋"),
            "四条河原町の交差点")

    def test_no_concern_changes_nothing(self):
        self.assertEqual(intent.place_without_concern("京都のパン屋", ""),
                         "京都のパン屋")


if __name__ == "__main__":
    unittest.main()
