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


class RealJapaneseTranscriptTest(unittest.TestCase):
    """Verbatim from the deck. whisper-base does not write 言語モード in kanji.

    Every one of these was an attempt at「言語モード 英語」and every one failed,
    because the rule was written in the kanji a person types rather than in
    what comes back from the recogniser. The same mistake as the English rules
    a day earlier, in a different alphabet.
    """

    HEARD = [
        ("げんごモード 英語", "en"),
        ("言語モード 英語", "en"),
        ("ゲンゴモード エイゴ", "en"),
        ("言語モード 日本語", "ja"),
        ("ゲンゴモード ニホンゴ", "ja"),
    ]

    def test_what_was_actually_heard(self):
        for text, want in self.HEARD:
            r = intent.by_rule(text)
            self.assertIsNotNone(r, "%r matched nothing" % text)
            self.assertEqual(r["intent"], "set_language", text)
            self.assertEqual(r["lang"], want, text)

    def test_the_language_name_can_be_lost_and_still_be_understood(self):
        """「英語」came back as「絵を」and「行こう」-- the name is the fragile part.

        The structure survives it. Asking to switch while listening in Japanese
        can only mean English: nobody asks for the language already in use.
        Given the phrase and no readable name, go to the other one.
        """
        for text in ("ゲンゴモードを絵を", "ゲンゴモードへ行こう", "言語モード"):
            r = intent.by_rule(text, lang="ja")
            self.assertIsNotNone(r, text)
            self.assertEqual(r["intent"], "set_language", text)
            self.assertEqual(r["lang"], "en", text)

    def test_the_same_inference_the_other_way_round(self):
        r = intent.by_rule("language mode", lang="en")
        self.assertEqual(r["lang"], "ja")

    def test_a_readable_name_still_wins_over_the_inference(self):
        # Asking for the language already in use is harmless and understood;
        # the inference must not override what was actually said.
        r = intent.by_rule("言語モード 日本語", lang="ja")
        self.assertEqual(r["lang"], "ja")

    def test_without_knowing_the_current_language_it_stays_cautious(self):
        # by_rule is also called from pi-intent, which has no session language.
        self.assertIsNone(intent.by_rule("ゲンゴモードを絵を"))

    def test_a_transcript_too_mangled_to_read_is_left_alone(self):
        # 「銀行もど、英語」was also 言語モード 英語, but a rule that reads 銀行
        # as 言語 will read other things wrongly too. This is the model's to
        # attempt, or nobody's.
        self.assertIsNone(intent.by_rule("銀行もど、英語"))

    def test_a_place_that_merely_mentions_a_language_is_still_a_place(self):
        r = intent.by_rule("英語村を表示して")
        self.assertEqual(r["intent"], "show_place")


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



class MisheardStemTest(unittest.TestCase):
    """The stem of 言語 is not reliable; the language name is.

    Real transcripts from pi5-deck, 2026-08-27, of "オーケートライデント 言語
    モード 英語" spoken twice: both came back as 銀河モード 英語. げんご and
    ぎんが are not close as sounds, and whisper-base picked the commoner word.

    Enumerating mishearings of the stem is a losing game -- 元号, 原稿 and 減５
    are all one bad frame away. So the rule keys on the half that survives: the
    language name, with モード present to stop a bare 英語 switching anything.
    """

    HEARD = (
        ("銀河モード 英語", "en"),
        ("銀河モード 日本語", "ja"),
        ("元号モード 英語", "en"),
        ("モード 英語", "en"),
        ("ぎんがモード エイゴ", "en"),
    )

    def test_a_misheard_stem_still_switches(self):
        for text, want in self.HEARD:
            r = intent.by_rule(text)
            self.assertIsNotNone(r, text)
            self.assertEqual(r["intent"], "set_language", text)
            self.assertEqual(r["lang"], want, text)

    def test_the_name_alone_is_not_a_switch(self):
        """Otherwise "show me an English pub" would change the language."""
        for text in ("英語", "日本語", "英語の看板"):
            r = intent.by_rule(text)
            if r is not None:
                self.assertNotEqual(r["intent"], "set_language", text)

    def test_a_place_that_merely_contains_the_word_is_not_a_switch(self):
        r = intent.by_rule("銀河高原ビール")
        if r is not None:
            self.assertNotEqual(r["intent"], "set_language")



class NoDeadEndTest(unittest.TestCase):
    """Getting into English must not be a one-way trip.

    The way back is spoken in English, so it is recognised in English, where the
    stem is just as fragile: the comment on _LANG_ATTEMPT records the map flying
    to "Rangage" and "Languise" when these attempts failed to parse. A stem-only
    rule leaves the deck stuck in a language its owner may not want, with no
    voice route home.
    """

    def test_a_misheard_stem_still_gets_back(self):
        for text in ("Rangage mode Japanese",
                     "Languise mode Japanese",
                     "Language mode, Japanese."):
            r = intent.by_rule(text, lang="en")
            self.assertIsNotNone(r, text)
            self.assertEqual(r["intent"], "set_language", text)
            self.assertEqual(r["lang"], "ja", text)

    def test_english_asks_for_english_is_still_honoured(self):
        """Not a no-op to guard against: saying it twice must not toggle away."""
        r = intent.by_rule("Rangage mode English", lang="en")
        self.assertIsNotNone(r)
        self.assertEqual(r["lang"], "en")

    def test_a_language_name_alone_is_not_a_switch(self):
        for text in ("Japanese", "an English pub", "Japanese restaurant"):
            r = intent.by_rule(text, lang="en")
            if r is not None:
                self.assertNotEqual(r["intent"], "set_language", text)


if __name__ == "__main__":
    unittest.main()
