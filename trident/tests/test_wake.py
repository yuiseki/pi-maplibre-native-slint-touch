#!/usr/bin/env python3
"""Waking the deck, in Japanese and in English.

The two languages need different machinery, because whisper-base fails them
differently. Japanese comes back as a mangled *reading* of the wake word
(トライ弦, ポキプライ), which edit distance over romaji absorbs. English does
not come back as a mangled "Trident" at all -- it comes back as "try it",
"try that", "try them", i.e. ordinary English words. No tolerance separates
those from someone simply saying "let me try it again".

What does separate them is the "OK" in front. Every observed wake had it and
no observed non-wake did, so English matches a prefix rule -- "ok" immediately
followed by "tr" -- rather than a distance. All strings below are real: they
are what whisper-base actually returned from recordings on the device.
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "pi-hear"))

import romaji_match as rm  # noqa: E402


class Normalising(unittest.TestCase):
    """Punctuation and spacing come and go at the recogniser's whim."""

    def test_punctuation_and_spaces_are_dropped(self):
        self.assertEqual(rm.to_romaji("Ok, try it."), "oktryit")

    def test_the_same_words_normalise_the_same_however_punctuated(self):
        self.assertEqual(rm.to_romaji("OK Trident"), rm.to_romaji("Ok, Trident."))

    def test_japanese_still_romanises(self):
        self.assertEqual(rm.to_romaji("広島"), "hiroshima")


class EnglishWake(unittest.TestCase):
    # Exactly what whisper-base returned for "OK Trident ..." on the device.
    OBSERVED = [
        "Ok, try it.",
        "Okay, try it in",
        "Okay, try that",
        "Okay, try them to.",
        "Okay, try it. Show Hiroshima.",
        "Ok, Trident, Chohiro Shima",
        "Okay, Trident, show Hiroshima.",
        "OK Trident",
    ]
    # Ordinary English, including the near-misses that ruled out edit distance.
    QUIET = [
        "I want to try it",
        "you can try it",
        "let me try it again",
        "we should try it out",
        "try it yourself",
        "Let me show you the next slide.",
        "any questions",
        "thank you very much",
        "this is a Raspberry Pi",
        "Show, Hiroshima.",
        "Add cafes on map.",
        "Remove cafes from map.",
    ]

    def test_every_observed_wake_fires(self):
        for t in self.OBSERVED:
            with self.subTest(t=t):
                self.assertTrue(rm.wake_match(t)[0])

    def test_ordinary_english_stays_quiet(self):
        for t in self.QUIET:
            with self.subTest(t=t):
                self.assertFalse(rm.wake_match(t)[0])

    def test_a_dropped_ok_does_not_fire(self):
        # Observed once. Without the anchor there is nothing to tell this from
        # someone saying "I try them", so it is left alone on purpose.
        self.assertFalse(rm.wake_match("I try them")[0])

    def test_the_anchor_must_be_adjacent(self):
        # "ok" somewhere and "tr" somewhere is not the wake word.
        self.assertFalse(rm.wake_match("ok, so I need to travel")[0])
        self.assertFalse(rm.wake_match("that looks ok to me, try again")[0])


class JapaneseWake(unittest.TestCase):
    OBSERVED = [
        "オーケートライデント、広島を表示して",
        "OK トライ弦と沖縄を表示して",
        "大きくらいでと広島を表示して",
        "ポキプライでどう?",
        "OK トライ弁当 大阪を表示して",
    ]
    QUIET = ["今日はいい天気ですね", "これはラズベリーパイです"]

    def test_manglings_still_fire(self):
        for t in self.OBSERVED:
            with self.subTest(t=t):
                self.assertTrue(rm.wake_match(t)[0])

    def test_ordinary_japanese_stays_quiet(self):
        for t in self.QUIET:
            with self.subTest(t=t):
                self.assertFalse(rm.wake_match(t)[0])

    def test_japanese_does_not_reach_the_english_rule(self):
        # ookeetoraidento has "ok" but "eetoraidento" after it, so it is the
        # distance that catches it, not the prefix. Worth pinning: if the
        # prefix ever started matching Japanese, the English rule would have
        # quietly widened.
        self.assertFalse(rm.english_wake("オーケートライデント"))
        self.assertTrue(rm.wake_match("オーケートライデント")[0])


class Places(unittest.TestCase):
    """Both languages resolve, and English prose does not."""

    RESOLVES = [
        # English, as whisper-base returned it on the device
        ("Show, Hiroshima.", "hiroshima"),
        ("Shohiro Shima", "hiroshima"),
        ("Steve, Hiroshima.", "hiroshima"),
        ("And, Hiroshima.", "hiroshima"),
        ("Show, the map of Steve, Hiroshima.", "hiroshima"),
        ("Show, Tokyo.", "tokyo"),
        ("Kyoto, please.", "kyoto"),
        ("Show Osaka", "osaka"),
        ("Show Sapporo", "sapporo"),
        ("Show Fukuoka", "fukuoka"),
        ("Show Okinawa", "naha"),
        # Japanese
        ("広島を表示して", "hiroshima"),
        ("サッポロを表示して", "sapporo"),
        ("お気なを表示して", "naha"),
        ("東京を表示して", "tokyo"),
        ("大阪を表示して", "osaka"),
        ("京都を表示して", "kyoto"),
        ("福岡を表示して", "fukuoka"),
        ("那覇を表示して", "naha"),
    ]
    # English prose that must not look like a place. These are the reason the
    # short names need a tighter tolerance: "kyouto" is six letters and "you"
    # is everywhere in English, so a sliding window finds it by accident.
    PROSE = [
        "Let me show you the next slide.",
        "thank you very much",
        "any questions",
        "we built this in a week",
        "the next slide please",
        "this is a Raspberry Pi",
        "Add cafes on map.",
        "Remove cafes from map.",
        "Add restaurants on map.",
        "今日はいい天気ですね",
        "これはラズベリーパイです",
    ]

    def test_places_resolve(self):
        for text, key in self.RESOLVES:
            with self.subTest(text=text):
                place = rm.find_place(text)
                self.assertIsNotNone(place, "no place found")
                self.assertEqual(place[0], key)

    def test_prose_resolves_to_nothing(self):
        for text in self.PROSE:
            with self.subTest(text=text):
                self.assertIsNone(rm.find_place(text))

    def test_short_names_are_matched_more_strictly(self):
        # Not a style preference: at the long names' tolerance, "let me show
        # you the next slide" is 0.33 from "kyouto" and flies to Kyoto.
        short = [r for r, _k, _j, _e in rm.PLACES if len(r) < 8]
        self.assertTrue(short, "expected some short place readings")
        for rom in short:
            with self.subTest(rom=rom):
                self.assertLess(rm.place_thresh(rom), rm.PLACE_THRESH)

    def test_english_spellings_are_targets_too(self):
        # "Kyoto" spoken in English is an exact hit on its own spelling, where
        # the Japanese reading "kyouto" is two edits away and would need the
        # loose tolerance that lets prose in.
        romaji = [r for r, _k, _j, _e in rm.PLACES]
        self.assertIn("kyoto", romaji)
        self.assertIn("kyouto", romaji)


if __name__ == "__main__":
    unittest.main()
