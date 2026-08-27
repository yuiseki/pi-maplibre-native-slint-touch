"""Tests for saying so when a request found nothing.

Whiffing silently is the worst of the three outcomes. Success moves the map,
a misparse moves it somewhere wrong and the mistake is visible, but a tool that
exits 1 leaves the deck sitting there looking exactly as it does when it is
still thinking -- and the speaker cannot tell whether to wait, repeat, or
rephrase.

Real transcripts from pi5-deck, 2026-08-27, of 新宿 / 池袋 / 渋谷 / 上野 asked
for in English: "Shinjuku Station" found its station, while "Ekebukro Station",
"whenostation" and "severe station" each found nothing and said nothing.
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                "..", "pi-hear"))
import intent


class FailureReplyTest(unittest.TestCase):
    def test_it_asks_for_another_try_in_english(self):
        self.assertEqual(intent.failure_reply("en"),
                         "Sorry, could you please try again?")

    def test_it_asks_in_japanese_when_listening_in_japanese(self):
        """The reply language follows what was heard, not what is configured:
        the speaker just spoke, and being answered in the other language reads
        as a second failure."""
        self.assertIn("もう一度", intent.failure_reply("ja"))

    def test_an_unknown_language_still_gets_an_answer(self):
        """Silence is the thing being fixed; an unexpected code must not
        reintroduce it."""
        for lang in ("", None, "fr"):
            self.assertTrue(intent.failure_reply(lang), repr(lang))

    def test_it_does_not_blame_the_speaker(self):
        """"I didn't catch that" invites a repeat. Naming what was misheard
        would be worse than useless here -- "whenostation" is not a word the
        speaker said, and reading it back suggests they said it."""
        for lang in ("en", "ja"):
            self.assertNotIn("whenostation", intent.failure_reply(lang))


if __name__ == "__main__":
    unittest.main()
