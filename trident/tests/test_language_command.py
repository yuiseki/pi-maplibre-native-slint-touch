"""Tests for switching the listening language by voice.

The catch is that the language being switched is the one the recogniser is
using, so a request can only be understood in the language currently in force.
"Japanese mode, please" said in Japanese while listening in English comes back
as noise. The commands are therefore each phrased in the language doing the
asking: English asks for Japanese, Japanese asks for English.
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                "..", "pi-hear"))
import intent


class RecognisedTest(unittest.TestCase):
    def test_english_asks_for_japanese(self):
        for text in ("language mode Japanese",
                     "Language mode Japanese.",
                     "LANGUAGE MODE JAPANESE",
                     "switch to Japanese"):
            r = intent.by_rule(text)
            self.assertIsNotNone(r, text)
            self.assertEqual(r["intent"], "set_language", text)
            self.assertEqual(r["lang"], "ja", text)

    def test_japanese_asks_for_english(self):
        for text in ("言語モード 英語", "言語モード英語", "英語モードにして"):
            r = intent.by_rule(text)
            self.assertIsNotNone(r, text)
            self.assertEqual(r["intent"], "set_language", text)
            self.assertEqual(r["lang"], "en", text)

    def test_asking_for_the_language_already_in_use_is_still_understood(self):
        # Harmless, and refusing to parse it would look like mishearing.
        r = intent.by_rule("language mode English")
        self.assertEqual(r["intent"], "set_language")
        self.assertEqual(r["lang"], "en")
        r = intent.by_rule("言語モード 日本語")
        self.assertEqual(r["lang"], "ja")

    def test_a_place_called_english_is_not_a_language_command(self):
        # "show me the map of English Bay" is a place in Vancouver.
        r = intent.by_rule("show me the map of English Bay")
        self.assertEqual(r["intent"], "show_place")

    def test_talking_about_language_is_not_a_command(self):
        self.assertIsNone(intent.by_rule("what languages do you speak"))


class DispatchTest(unittest.TestCase):
    def test_it_becomes_a_pi_lang_call(self):
        d = intent.for_voice("language mode Japanese")
        self.assertEqual(d["tool"], "pi-lang")
        self.assertEqual(d["args"], ["ja"])

    def test_it_does_not_clear_the_pins(self):
        # Changing language is not going somewhere; what is on the map stays.
        d = intent.for_voice("language mode Japanese")
        self.assertFalse(d.get("clear_pins"))

    def test_it_is_answered_before_the_restart(self):
        # pi-hear restarts to take the new language, which kills the process
        # that would have spoken. The plan has to say so, or the deck goes
        # silent and looks like it crashed.
        d = intent.for_voice("language mode Japanese")
        self.assertTrue(d.get("speak_first"), d)

    def test_the_reply_is_in_the_language_being_switched_to(self):
        # Confirming in the outgoing language, then listening in the incoming
        # one, is the wrong way round: the last thing heard should match what
        # the deck is about to expect.
        self.assertEqual(intent.reply_for_language("ja"), "日本語モードにします。")
        self.assertEqual(intent.reply_for_language("en"),
                         "Switching to English.")


if __name__ == "__main__":
    unittest.main()
