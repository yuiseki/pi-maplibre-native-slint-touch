"""Tests for choosing a microphone that is actually plugged in.

The deck has four USB ports and the mic competes for them, so which one is
attached changes from day to day: a small PnP stick normally, a DJI Mic Mini
receiver when the speaker wants to walk away from the desk. Naming one card in
the config makes the other a restart loop -- arecord exits, pi-hear exits with
it, and the map's mic icon stays grey because pi-hear never gets far enough to
say it is listening.

So the setting names a preference order and the tool takes the first entry that
can actually record.
"""
import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "pi-hear"))

import pi_hear

PCM = """\
00-00: USB Audio : USB Audio : playback 1
01-00: USB Audio : USB Audio : capture 1
02-00: MAI PCM i2s-hifi-0 : MAI PCM i2s-hifi-0 : playback 1
"""

CARDS = """\
 0 [Audio          ]: USB-Audio - KT USB Audio
 1 [Device         ]: USB-Audio - USB PnP Sound Device
 2 [vc4hdmi0       ]: vc4-hdmi - vc4-hdmi-0
"""

BOTH_CARDS = CARDS + " 3 [MINI           ]: USB-Audio - DJI MIC MINI\n"
BOTH_PCM = PCM + "03-00: USB Audio : USB Audio : capture 1\n"

PREFER_DJI = "plughw:CARD=MINI,DEV=0|plughw:CARD=Device,DEV=0"


class CaptureCardsTest(unittest.TestCase):
    def test_only_cards_that_can_record_are_listed(self):
        """A speaker is not a microphone. card 0 here is playback-only."""
        self.assertEqual(pi_hear.capture_card_names(PCM, CARDS), ["Device"])

    def test_both_mics_attached(self):
        self.assertEqual(pi_hear.capture_card_names(BOTH_PCM, BOTH_CARDS),
                         ["Device", "MINI"])

    def test_nothing_attached_is_an_empty_list_not_a_crash(self):
        self.assertEqual(pi_hear.capture_card_names("", ""), [])


class PickTest(unittest.TestCase):
    def test_the_preferred_mic_wins_when_it_is_there(self):
        got = pi_hear.pick_alsa_device(PREFER_DJI, ["Device", "MINI"])
        self.assertEqual(got, "plughw:CARD=MINI,DEV=0")

    def test_the_second_choice_is_used_when_the_first_is_unplugged(self):
        """This is the case that was a restart loop: DJI named, stick attached."""
        got = pi_hear.pick_alsa_device(PREFER_DJI, ["Device"])
        self.assertEqual(got, "plughw:CARD=Device,DEV=0")

    def test_a_single_entry_still_works(self):
        got = pi_hear.pick_alsa_device("plughw:CARD=Device,DEV=0", ["Device"])
        self.assertEqual(got, "plughw:CARD=Device,DEV=0")

    def test_a_pcm_that_names_no_card_is_passed_through(self):
        """bluealsa PCMs are not cards and cannot be checked; trying is the only
        way to find out, and refusing would break the one case --alsa-device was
        added for."""
        bt = "bluealsa:DEV=20:74:CF:D2:A3:84,PROFILE=sco"
        self.assertEqual(pi_hear.pick_alsa_device(bt, []), bt)

    def test_an_unlisted_mic_is_still_used_rather_than_nothing(self):
        """A mic nobody named beats no mic at all: the fallback is what keeps a
        newly-bought device working without an edit."""
        got = pi_hear.pick_alsa_device(PREFER_DJI, ["Something"])
        self.assertEqual(got, "plughw:CARD=Something,DEV=0")

    def test_no_capture_device_at_all_yields_nothing(self):
        """Better to say so and let systemd retry than to open a made-up PCM."""
        self.assertIsNone(pi_hear.pick_alsa_device(PREFER_DJI, []))

    def test_blank_entries_are_ignored(self):
        got = pi_hear.pick_alsa_device("|plughw:CARD=Device,DEV=0|", ["Device"])
        self.assertEqual(got, "plughw:CARD=Device,DEV=0")


if __name__ == "__main__":
    unittest.main()
