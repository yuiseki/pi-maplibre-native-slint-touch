#!/usr/bin/env python3
"""Gating the whisper call on Julius hearing the wake word.

Measured on pi5-deck, 2026-09-13, on 92 real recordings the deck saved on
08-23. All 22 that the two methods disagreed about were played back and
confirmed by ear to be the wake word:

    wake utterances present         42
    Julius grammar found            36   (86%)
    matching whisper's text found   26   (62%)
    Julius false alarms              0

The 16 that only Julius found are ones whisper wrote as "OK トライドエンド",
"OK トライダーエント", "OK、とりあえず" -- the sound was the wake word and the
text was not. Matching on text throws that away before the rule ever runs.

Julius cannot replace whisper here: a grammar only emits words that are in it,
so the command after the wake word ("show Hiroshima") still needs whisper.
Julius decides whether to spend that call.
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "pi-hear"))

import julius_gate as jg  # noqa: E402


class ReadingJuliusOutput(unittest.TestCase):
    """What the decoder prints, verbatim from the device."""

    FOUND = "sentence1: <s> G WAKE </s>"
    FOUND_MID = "sentence1: <s> G G G WAKE G G </s>"
    GARBAGE = "sentence1: <s> G G G G G G G G G G G G G G </s>"

    def test_wake_alone_is_heard(self):
        self.assertTrue(jg.wake_in_output(self.FOUND))

    def test_wake_surrounded_by_garbage_is_heard(self):
        self.assertTrue(jg.wake_in_output(self.FOUND_MID))

    def test_pure_garbage_is_not(self):
        self.assertFalse(jg.wake_in_output(self.GARBAGE))

    def test_no_sentence_at_all_is_not(self):
        # 60s of continuous radio exceeded Julius's sentence length and it
        # printed no sentence1 line. Absent output is not a wake word.
        self.assertFalse(jg.wake_in_output("trace_backptr: sentence length exceeded"))

    def test_empty_is_not(self):
        self.assertFalse(jg.wake_in_output(""))

    def test_the_word_in_a_log_line_is_not_a_detection(self):
        # Only the recognised sentence counts. "WAKE" appears in the grammar
        # dump and in warnings too, and matching those would fire on startup.
        self.assertFalse(jg.wake_in_output("Stat: dict: 3 WAKE [WAKE] o: k e:"))


class BuildingTheCommand(unittest.TestCase):
    """Absolute paths only: the AM is found via -h/-hlist, not via cwd.

    hmm_ptm.jconf in the grammar kit names its model by relative path, so
    running with -C only works from inside the kit. pi-hear runs from
    wherever systemd put it.
    """

    def test_every_model_path_is_passed_absolute(self):
        cmd = jg.build_command("/j/julius", "/m/ptm.binhmm", "/m/logicalTri",
                               "/g/wake.dfa", "/g/wake.dict", "/dev/shm/u.wav")
        self.assertIn("/m/ptm.binhmm", cmd)
        self.assertIn("/m/logicalTri", cmd)
        self.assertNotIn("-C", cmd)

    def test_the_wav_arrives_as_a_filelist(self):
        # Julius does not take a waveform as a positional argument; it exits
        # with "Try `-help'" and prints nothing. Read as "no wake word", that
        # is a deck which never answers -- which is what happened live on
        # 2026-09-13 while the same audio matched fine from a filelist.
        cmd = jg.build_command("/j/julius", "/m/ptm.binhmm", "/m/logicalTri",
                               "/g/wake.dfa", "/g/wake.dict", "/dev/shm/u.list")
        self.assertIn("-filelist", cmd)
        self.assertEqual(cmd[cmd.index("-filelist") + 1], "/dev/shm/u.list")
        self.assertIn("rawfile", cmd)
        self.assertNotIn("/dev/shm/u.wav", cmd)


class WhoGetsTranscribed(unittest.TestCase):
    """The gate stands in front of whisper, and only while disarmed.

    Once the deck is armed, the next utterance is the command -- "show
    Hiroshima" -- which is deliberately not in the grammar. Asking Julius
    about it would answer "no wake word" and drop the very thing the user
    just woke the deck to say.
    """

    def test_without_a_gate_everything_is_transcribed(self):
        self.assertTrue(jg.should_transcribe(has_gate=False, armed=False,
                                             heard_wake=False))

    def test_an_armed_deck_transcribes_without_asking(self):
        self.assertTrue(jg.should_transcribe(has_gate=True, armed=True,
                                             heard_wake=False))

    def test_a_disarmed_deck_transcribes_only_what_woke_it(self):
        self.assertTrue(jg.should_transcribe(has_gate=True, armed=False,
                                             heard_wake=True))
        self.assertFalse(jg.should_transcribe(has_gate=True, armed=False,
                                              heard_wake=False))

    def test_the_gate_is_consulted_only_when_it_could_change_the_answer(self):
        self.assertTrue(jg.needs_gate(has_gate=True, armed=False))
        self.assertFalse(jg.needs_gate(has_gate=True, armed=True))
        self.assertFalse(jg.needs_gate(has_gate=False, armed=False))


class ArmingFromTheGate(unittest.TestCase):
    """Arm on the sound, not on the transcript that follows it.

    Measured on the deck, 2026-09-13: whisper takes 5.5s on a 1.8s utterance,
    because whisper.cpp pads every input to its 30s window. The wake word was
    already identified 5.2s earlier, by Julius, in 0.3s. Waiting for the text
    before dimming the map spends all of that on a decision that was made.
    """

    def test_a_heard_wake_arms_a_disarmed_deck(self):
        self.assertTrue(jg.should_arm_on_gate(heard_wake=True, armed=False))

    def test_silence_does_not(self):
        self.assertFalse(jg.should_arm_on_gate(heard_wake=False, armed=False))

    def test_an_armed_deck_is_left_alone(self):
        # Re-arming here would extend the window every time the user speaks
        # inside it, which is not what the window is for.
        self.assertFalse(jg.should_arm_on_gate(heard_wake=True, armed=True))


class WhatTheScreenSaysAfterArming(unittest.TestCase):
    """Do not take "armed" back to say "thinking" about the same breath.

    emit() normally publishes the transcription so that "it heard this and
    did nothing" is distinguishable from "it heard nothing". For the wake
    utterance that is no longer news -- the map is already dimmed and the
    user is mid-sentence saying the place. Overwriting it would flip the
    screen back to Thinking while they speak.
    """

    def test_the_wake_utterance_does_not_announce_its_transcript(self):
        self.assertFalse(jg.announce_heard(armed_by_gate=True))

    def test_everything_else_still_does(self):
        self.assertTrue(jg.announce_heard(armed_by_gate=False))


class WhenJuliusFailsToRun(unittest.TestCase):
    """A decoder that exited without deciding has not said "no"."""

    def test_a_nonzero_exit_is_not_a_silent_no(self):
        with self.assertRaises(RuntimeError):
            jg.decide(returncode=255, stdout="", stderr="Try `-help'")

    def test_a_clean_run_decides(self):
        self.assertTrue(jg.decide(0, "sentence1: <s> G WAKE </s>", ""))
        self.assertFalse(jg.decide(0, "sentence1: <s> G G </s>", ""))


class WhenJuliusIsMissing(unittest.TestCase):
    """A gate that fails open would silently undo the whole change.

    If the binary or the grammar is not there, say so at construction. The
    alternative -- treating "cannot run" as "no wake word" -- makes a deck
    that never answers, and looks exactly like a quiet room.
    """

    def test_missing_binary_is_refused_at_construction(self):
        with self.assertRaises(FileNotFoundError):
            jg.JuliusGate(binary="/nonexistent/julius", hmm="/nonexistent/a",
                          hlist="/nonexistent/b", dfa="/nonexistent/c",
                          dictionary="/nonexistent/d")


if __name__ == "__main__":
    unittest.main()
