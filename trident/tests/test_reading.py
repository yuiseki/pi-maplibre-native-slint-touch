"""Categories matched by reading, not by spelling.

駅 was asked for five times and written down five ways: 息, 域, 行き, 液, and
once correctly. Four of those read いき and one reads えき -- one edit apart, or
none. The surface form was never going to be guessable; the sound always was.

This is the mechanism the wake word and the place names already use. It is not
a table of the four spellings, which is what the previous four fixes in this
file's history amounted to and what the fifth would have been.

Needs pykakasi, which the build host does not have, so it skips there and runs
on the deck.
"""
import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "pi-hear"))

import geo

try:
    import romaji_match
except ImportError:                                # pragma: no cover
    romaji_match = None


@unittest.skipIf(romaji_match is None, "pykakasi not installed here")
class ReadingTest(unittest.TestCase):
    def setUp(self):
        self.cats = {w: geo.canonical_category(w) for w in geo.CATEGORIES}

    def find(self, text):
        return romaji_match.find_category(text, self.cats)

    def test_every_way_the_deck_wrote_down_eki(self):
        # Verbatim, 2026-08-23. Five attempts at 駅, five transcripts.
        for said in ("新宿の駅を表示して", "新宿の息を表示して", "新宿の域を表示して",
                     "地図に行きを表示して", "地図に液を表示して", "地図に駅を表示して"):
            self.assertEqual(self.find(said), "station", said)

    def test_the_map_word_can_be_lost_and_the_category_still_found(self):
        # 地図に came back as チーズに and 実に. The category is what matters.
        self.assertEqual(self.find("チーズにカフェを追加して"), "cafe")
        self.assertEqual(self.find("実に公園を追加して"), "park")

    def test_ordinary_sentences_stay_quiet(self):
        for said in ("大きい建物を表示して", "行きたいです", "地図をクリアして",
                     "現在地", "リセットして", "インターネットを切断して",
                     "言語モード 英語", "広島を表示して", "オーケートライデント",
                     "意気込みを表示", "生きています", "沖縄を表示して"):
            self.assertIsNone(self.find(said), said)

    def test_a_short_category_needs_its_particle(self):
        # eki is three letters and three letters turn up inside anything, so
        # the reading alone is not enough: 「行きたい」has いき in it and must not
        # become a station.
        self.assertIsNone(self.find("行きたい"))
        self.assertEqual(self.find("駅を表示して"), "station")

    def test_the_known_collision(self):
        # 秋を / 安芸を read akiwo, one edit from ekiwo, and do become a station
        # search. Recorded rather than fixed: the cost is a wrong search that
        # is undone by saying the next thing, and tightening enough to exclude
        # it also excludes 息 and 域, which are what people actually hit.
        self.assertEqual(self.find("秋を表示して"), "station")

    def test_long_readings_get_proportional_tolerance(self):
        # hakubutsukan is eleven letters; two edits of it is still nothing else.
        self.assertEqual(self.find("博物館を表示して"), "museum")
        self.assertEqual(self.find("コンビニを表示して"), "convenience")


if __name__ == "__main__":
    unittest.main()
