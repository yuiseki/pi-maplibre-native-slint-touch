#!/usr/bin/env python3
"""Speaking to the deck in English as well as Japanese.

The romaji matching already collapses both: a Japanese city said in English
romanises to the same string the Japanese name does. What has to be decided is
which language to answer in, and what to call the place when answering.
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "pi-hear"))

import romaji_match as rm  # noqa: E402


class Matching(unittest.TestCase):
    """Neither language should have been made worse by admitting the other."""

    JA = [
        ("オーケートライデント、広島を表示して", "hiroshima"),
        ("OK トライデント サッポロを表示して", "sapporo"),
        ("OK トライ弦と沖縄を表示して", "naha"),
        ("OKトライデント東京を表示して", "tokyo"),
    ]
    # Written as whisper-base actually returns them, not as they were said.
    EN = [
        ("Okay, try it. Show Hiroshima.", "hiroshima"),
        ("Okay, try it in. Show Tokyo", "tokyo"),
        ("Ok, try it. Kyoto please", "kyoto"),
        ("OK Trident, show Osaka", "osaka"),
    ]
    NEITHER = ["今日はいい天気ですね", "these are the days", "これはラズベリーパイです"]

    def test_japanese_still_wakes_and_resolves(self):
        for text, key in self.JA:
            with self.subTest(text=text):
                self.assertTrue(rm.wake_match(text)[0])
                place = rm.find_place(text)
                self.assertIsNotNone(place, "no place found")
                self.assertEqual(place[0], key)

    def test_english_wakes_and_resolves(self):
        for text, key in self.EN:
            with self.subTest(text=text):
                self.assertTrue(rm.wake_match(text)[0], "wake word missed")
                place = rm.find_place(text)
                self.assertIsNotNone(place, "no place found")
                self.assertEqual(place[0], key)

    def test_ordinary_speech_still_does_not_wake(self):
        for text in self.NEITHER:
            with self.subTest(text=text):
                self.assertFalse(rm.wake_match(text)[0])


class ReplyLanguage(unittest.TestCase):
    """Answer in the language you were asked in."""

    def test_japanese_text_is_japanese(self):
        for t in ("広島を表示して", "オーケートライデント", "ひらしまう証拠して"):
            with self.subTest(t=t):
                self.assertEqual(rm.reply_language(t), "ja")

    def test_english_text_is_english(self):
        for t in ("show Hiroshima", "OK Trident", "Kyoto please"):
            with self.subTest(t=t):
                self.assertEqual(rm.reply_language(t), "en")

    def test_mixed_counts_as_japanese(self):
        # whisper in ja mode transliterates English, so anything with kana in
        # it came out of a Japanese decode and should be answered in Japanese.
        self.assertEqual(rm.reply_language("OK Trident 広島"), "ja")

    def test_empty_is_japanese(self):
        # The device's resting language; nothing to infer from.
        self.assertEqual(rm.reply_language(""), "ja")


class PlaceNames(unittest.TestCase):
    def test_a_place_carries_a_name_in_both_languages(self):
        place = rm.find_place("show Hiroshima")
        self.assertIsNotNone(place)
        key, spoken_ja, spoken_en, _dist = place
        self.assertEqual(key, "hiroshima")
        self.assertEqual(spoken_ja, "広島")
        self.assertEqual(spoken_en, "Hiroshima")

    def test_every_place_has_both_names(self):
        for rom, key, ja, en in rm.PLACES:
            with self.subTest(key=key):
                self.assertTrue(ja and en, f"{key} is missing a name")

    def test_okinawa_still_flies_to_naha_under_either_name(self):
        for text in ("沖縄を表示して", "show Okinawa"):
            with self.subTest(text=text):
                self.assertEqual(rm.find_place(text)[0], "naha")


class Confirmations(unittest.TestCase):
    def test_japanese_confirmation(self):
        self.assertEqual(rm.confirmation("ja", "広島", "Hiroshima"),
                         "承知しました。広島を表示します。")

    def test_english_confirmation(self):
        self.assertEqual(rm.confirmation("en", "広島", "Hiroshima"),
                         "Okay. Showing Hiroshima.")


if __name__ == "__main__":
    unittest.main()
