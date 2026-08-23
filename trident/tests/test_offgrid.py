"""Tests for the off-grid command: take the network away, then bring it back.

The point of the feature is a demonstration -- everything the deck answers with
is on the SSD, and the way to show that is to remove the network and watch
nothing change. The point of the tests is the other half: this is the one voice
command whose failure mode is a deck nobody can reach.
"""
import os
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "pi-hear"))

import intent

PI_OFFGRID = os.path.join(HERE, "..", "bin", "pi-offgrid")


class RuleTest(unittest.TestCase):
    """The phrasings, in both languages, as whisper-base tends to write them."""

    def assert_offgrid(self, said):
        r = intent.by_rule(said, lang="ja")
        self.assertIsNotNone(r, repr(said))
        self.assertEqual(r["intent"], "disconnect_net", repr(said))

    def test_english(self):
        for said in (
            "disconnect internet",
            "disconnect the internet",
            "Disconnect from the internet.",
            "cut the internet",
            "kill the network",
            "turn off wifi",
            "turn off the wi-fi",
            "shut off the network",
            "go offline",
            "go off grid",
            "go off-grid",
            "offline mode",
        ):
            self.assert_offgrid(said)

    def test_japanese(self):
        # whisper-base does not reliably write the particle, and it spells the
        # katakana more than one way. Written against only the phrase a person
        # would type, this matched about half of these.
        for said in (
            "インターネットを切断して",
            "インターネットを切断してください",
            "インターネット切断",
            "インタネットを切断して",
            "ネットを切断して",
            "ネットワークを遮断して",
            "ワイファイをオフ",
            "無線を切って",
            "オフライン",
            "オフラインモードにして",
        ):
            self.assert_offgrid(said)

    def test_a_bare_disconnect_is_not_enough(self):
        # "disconnect" without a network word is a word people say to each
        # other, and being wrong here takes the deck off the air in front of an
        # audience. A network word is required.
        for said in ("disconnect", "disconnect me", "cut it out",
                     "切断", "切って"):
            r = intent.by_rule(said, lang="ja")
            if r is not None:
                self.assertNotEqual(r["intent"], "disconnect_net", repr(said))

    def test_it_does_not_eat_a_place_or_a_category(self):
        for said, want in (
            ("show me the map of Internet City", "show_place"),
            ("show cafes on map", "show_poi"),
            ("現在地", "show_here"),
            ("地図をクリアして", "clear_poi"),
        ):
            r = intent.by_rule(said, lang="ja")
            self.assertIsNotNone(r, repr(said))
            self.assertEqual(r["intent"], want, repr(said))


class ModelTest(unittest.TestCase):
    def test_the_model_is_not_offered_this_intent(self):
        # The whole point: a 0.5B model that mishears a sentence as "cut the
        # network" takes the deck off the air, and the screen still looks fine.
        self.assertNotIn("disconnect_net", intent.MODEL_INTENTS)
        self.assertNotIn("disconnect_net", intent.grammar())

    def test_the_model_cannot_smuggle_it_through_anyway(self):
        r = intent.parse('{"intent":"disconnect_net","place":"","category":""}')
        self.assertNotEqual(r["intent"], "disconnect_net")


class PlanTest(unittest.TestCase):
    def test_it_runs_pi_offgrid_for_a_fixed_sixty_seconds(self):
        plan = intent.for_voice("disconnect internet", lang="en")
        self.assertEqual(plan["tool"], "pi-offgrid")
        self.assertEqual(plan["args"], ["60"])

    def test_it_speaks_before_the_network_goes_away(self):
        # A confirmation that arrives after the thing it confirms is not a
        # confirmation.
        plan = intent.for_voice("disconnect internet", lang="en")
        self.assertTrue(plan["speak_first"])

    def test_what_it_says_promises_the_return(self):
        # Somebody who does not know it comes back by itself reaches for the
        # power button, which is the one way to actually strand the deck.
        en = intent.for_voice("disconnect internet", lang="en")["say"]
        self.assertIn("60", en)
        self.assertIn("come back", en.lower())
        ja = intent.for_voice("インターネットを切断して", lang="ja")["say"]
        self.assertIn("60", ja)
        self.assertIn("自動", ja)

    def test_it_answers_in_the_language_being_listened_in(self):
        self.assertIn("Going off grid",
                      intent.for_voice("go offline", lang="en")["say"])
        self.assertIn("切断",
                      intent.for_voice("オフライン", lang="ja")["say"])


def run_tool(args, extra=None):
    """Run pi-offgrid with every outside command replaced by a recorder."""
    tmp = tempfile.mkdtemp(prefix="pi-offgrid.")
    log = os.path.join(tmp, "log")
    # A stub that appends its argv and succeeds.
    stub = os.path.join(tmp, "stub")
    with open(stub, "w") as fh:
        fh.write('#!/bin/bash\necho "$(basename $0): $*" >> %s\n' % log)
    os.chmod(stub, 0o755)
    for name in ("pi-net", "pi-say", "systemd-run", "nmcli"):
        os.symlink(stub, os.path.join(tmp, name))

    env = dict(os.environ)
    env.update({
        "PI_OFFGRID_NET": os.path.join(tmp, "pi-net"),
        "PI_OFFGRID_SAY": os.path.join(tmp, "pi-say"),
        "PI_OFFGRID_SYSTEMDRUN": os.path.join(tmp, "systemd-run"),
        "PI_OFFGRID_NMCLI": os.path.join(tmp, "nmcli"),
        "PI_OFFGRID_LANG_FILE": os.path.join(tmp, "lang"),
        "PI_OFFGRID_SUDO": "",
    })
    if extra:
        env.update(extra)
    r = subprocess.run(["bash", PI_OFFGRID] + args, capture_output=True,
                       text=True, env=env, timeout=30)
    recorded = ""
    if os.path.exists(log):
        with open(log) as fh:
            recorded = fh.read()
    return r, recorded, tmp


class ToolTest(unittest.TestCase):
    def test_it_defaults_to_sixty_seconds(self):
        r, log, _ = run_tool([])
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("pi-net: disconnect 60", log)

    def test_it_passes_the_time_through_to_pi_net(self):
        r, log, _ = run_tool(["120"])
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("pi-net: disconnect 120", log)

    def test_it_refuses_a_time_outside_the_bounds(self):
        # Reachable by voice. Ten minutes is a demonstration; an hour is an
        # outage, and nothing said to a microphone should be able to ask for
        # one. Nor should a misheard number.
        for bad in ("0", "5", "601", "86400", "-60", "abc", "60; reboot"):
            r, log, _ = run_tool([bad])
            self.assertNotEqual(r.returncode, 0, repr(bad))
            self.assertNotIn("pi-net: disconnect", log, repr(bad))

    def test_an_empty_argument_is_the_default_not_an_error(self):
        # A caller passing "" is a caller bug, and the safe reading of it is
        # the default rather than a refusal: sixty seconds is the thing that
        # was asked for anyway.
        r, log, _ = run_tool([""])
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("pi-net: disconnect 60", log)

    def test_there_is_no_way_to_stay_off(self):
        # pi-net has --stay. This deliberately does not pass it through: a
        # voice command that can strand a headless deck eventually will.
        r, log, _ = run_tool(["--stay"])
        self.assertNotEqual(r.returncode, 0)
        self.assertNotIn("disconnect", log)

    def test_it_schedules_the_spoken_return(self):
        r, log, _ = run_tool(["60"])
        self.assertIn("systemd-run:", log)
        self.assertIn("--on-active=80", log)      # 60 + the settling time
        self.assertIn("--announce", log)

    def test_it_does_not_disconnect_when_pi_net_refuses(self):
        # pi-net refuses unless it could schedule the way back first. That
        # refusal is the safety property; going ahead regardless would throw it
        # away.
        tmp = tempfile.mkdtemp(prefix="pi-offgrid.")
        failing = os.path.join(tmp, "pi-net")
        with open(failing, "w") as fh:
            fh.write('#!/bin/bash\nexit 1\n')
        os.chmod(failing, 0o755)
        r, log, _ = run_tool(["60"], extra={"PI_OFFGRID_NET": failing})
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("staying on the air", r.stderr)
        self.assertNotIn("systemd-run", log)

    def test_the_announcement_tells_the_truth_about_being_back(self):
        # nmcli's stub reports nothing, so no wifi device is connected: the
        # deck must not claim it is back.
        r, log, _ = run_tool(["--announce"])
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("pi-say:", log)
        self.assertIn("戻れませんでした", log)

    def test_the_announcement_says_so_when_it_is_back(self):
        tmp = tempfile.mkdtemp(prefix="pi-offgrid.")
        nm = os.path.join(tmp, "nmcli")
        with open(nm, "w") as fh:
            fh.write('#!/bin/bash\necho "wlan0:wifi:connected"\n')
        os.chmod(nm, 0o755)
        r, log, _ = run_tool(["--announce"], extra={"PI_OFFGRID_NMCLI": nm})
        self.assertIn("オンラインに戻りました", log)

    def test_the_announcement_follows_the_listening_language(self):
        tmp = tempfile.mkdtemp(prefix="pi-offgrid.")
        nm = os.path.join(tmp, "nmcli")
        with open(nm, "w") as fh:
            fh.write('#!/bin/bash\necho "wlan0:wifi:connected"\n')
        os.chmod(nm, 0o755)
        lang = os.path.join(tmp, "lang")
        with open(lang, "w") as fh:
            fh.write("PI_HEAR_LANG=en\n")
        r, log, _ = run_tool(["--announce"],
                             extra={"PI_OFFGRID_NMCLI": nm,
                                    "PI_OFFGRID_LANG_FILE": lang})
        self.assertIn("Back online", log)


if __name__ == "__main__":
    unittest.main()
