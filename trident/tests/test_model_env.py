"""Tests for choosing the recogniser model without editing the unit.

The deck's everyday language is not fixed. `pi-lang` already switches what
whisper decodes, and switching it changes which model is the right one: the
multilingual base reads Japanese well but refuses Japanese-accented English
outright, returning whisper's "(speaking in foreign language)" placeholder
rather than a wrong guess. Measured on the deck, 2026-08-28, on one recording
of "Show Hiroshima":

    ggml-base.bin        -l en    (speaking in foreign language)   2.6s
    ggml-base.bin        -l auto  しょうギロスマ                    3.6s
    ggml-base.en.bin     -l en    So, Hiroshima!                   2.2s
    ggml-small-q8_0.bin  -l en    Show Hiroshima!                  5.1s

So the model belongs in configuration next to the language, not baked into
ExecStart, and --record-dir already works that way. The English-only base is
faster and reads the place, but it fails the wake word ("Okay, to right end."),
which is why the choice is a measurement rather than a size.

--debug reads the environment for the same reason: turning on the per-utterance
peak line is how a "nothing was heard" report gets diagnosed, and it should not
need a duplicated ExecStart in a drop-in to do it.
"""
import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "pi-hear"))

import pi_hear


class ParserIsReachable(unittest.TestCase):
    """The parser is built outside main() so its defaults can be asserted."""

    def test_build_parser_returns_a_parser(self):
        ap = pi_hear.build_parser()
        self.assertTrue(hasattr(ap, "parse_args"))

    def test_parser_has_no_required_arguments(self):
        # pi-hear runs from a unit with only optional flags; a required
        # argument would turn a restart into a usage error.
        ap = pi_hear.build_parser()
        args = ap.parse_args([])
        self.assertIsNotNone(args)


class ModelFromEnvironment(unittest.TestCase):

    def setUp(self):
        self._saved = {k: os.environ.get(k)
                       for k in ("PI_HEAR_MODEL", "PI_HEAR_DEBUG")}
        for k in self._saved:
            os.environ.pop(k, None)

    def tearDown(self):
        for k, v in self._saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

    def defaults(self):
        return pi_hear.build_parser().parse_args([])

    def test_model_defaults_to_a_ggml_file(self):
        self.assertTrue(self.defaults().whisper_model.endswith(".bin"))

    def test_model_comes_from_pi_hear_model(self):
        os.environ["PI_HEAR_MODEL"] = "/models/ggml-small-q8_0.bin"
        self.assertEqual(self.defaults().whisper_model,
                         "/models/ggml-small-q8_0.bin")

    def test_an_empty_setting_is_not_a_model(self):
        # An EnvironmentFile line left as `PI_HEAR_MODEL=` must not hand
        # whisper the empty string: the engine's own check would then report a
        # missing model file, which reads like the model was deleted.
        os.environ["PI_HEAR_MODEL"] = ""
        self.assertTrue(self.defaults().whisper_model.endswith(".bin"))

    def test_a_command_line_model_still_wins(self):
        os.environ["PI_HEAR_MODEL"] = "/models/from-env.bin"
        args = pi_hear.build_parser().parse_args(
            ["--whisper-model", "/models/from-argv.bin"])
        self.assertEqual(args.whisper_model, "/models/from-argv.bin")


class DebugFromEnvironment(unittest.TestCase):

    def setUp(self):
        self._saved = os.environ.get("PI_HEAR_DEBUG")
        os.environ.pop("PI_HEAR_DEBUG", None)

    def tearDown(self):
        if self._saved is None:
            os.environ.pop("PI_HEAR_DEBUG", None)
        else:
            os.environ["PI_HEAR_DEBUG"] = self._saved

    def defaults(self):
        return pi_hear.build_parser().parse_args([])

    def test_debug_is_off_by_default(self):
        self.assertFalse(self.defaults().debug)

    def test_debug_turns_on_from_the_environment(self):
        os.environ["PI_HEAR_DEBUG"] = "1"
        self.assertTrue(self.defaults().debug)

    def test_an_empty_setting_leaves_debug_off(self):
        # systemd writes `PI_HEAR_DEBUG=` for a commented-out line, and an
        # empty string is a request for nothing, not for everything.
        os.environ["PI_HEAR_DEBUG"] = ""
        self.assertFalse(self.defaults().debug)

    def test_zero_leaves_debug_off(self):
        os.environ["PI_HEAR_DEBUG"] = "0"
        self.assertFalse(self.defaults().debug)

    def test_the_flag_still_turns_it_on(self):
        self.assertTrue(
            pi_hear.build_parser().parse_args(["--debug"]).debug)


if __name__ == "__main__":
    unittest.main()
