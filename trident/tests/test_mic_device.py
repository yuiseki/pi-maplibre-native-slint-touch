"""Tests for choosing which microphone pi-hear listens to.

The deck has four USB ports and the mic competes with everything else for
them, so which mic is attached changes from day to day: the little PnP stick
normally, a DJI Mic Mini receiver when the speaker wants to walk away from the
desk. Pointing the unit at a mic that is not plugged in is not a quiet
degradation -- arecord exits, pi-hear exits with it, and systemd restarts the
pair forever.

So the device has to be settable per host without editing the unit. These
tests pin the mechanism that makes that possible, which is entirely a matter
of where lines sit relative to each other in the unit file and is therefore
easy to break by tidying.
"""
import os
import re
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
UNIT = os.path.join(HERE, "..", "systemd", "pi-hear.service")


def unit_lines():
    """The unit with its backslash continuations folded into single lines."""
    with open(UNIT, encoding="utf-8") as fh:
        text = fh.read()
    return [ln.strip() for ln in text.replace("\\\n", " ").splitlines()]


class DeviceIsOverridable(unittest.TestCase):
    def setUp(self):
        self.lines = unit_lines()
        execs = [ln for ln in self.lines if ln.startswith("ExecStart=")]
        self.assertEqual(len(execs), 1, "expected exactly one ExecStart")
        self.exec_line = execs[0]

    def test_device_is_a_variable_not_a_card_name(self):
        """--alsa-device must be a variable, so a drop-in never has to restate
        ExecStart. Restating it means the real command lives in two files and
        the next edit lands in only one of them."""
        m = re.search(r"--alsa-device\s+(\S+)", self.exec_line)
        self.assertIsNotNone(m, "ExecStart has no --alsa-device")
        value = m.group(1)
        self.assertTrue(
            value.startswith("${") and value.endswith("}"),
            "--alsa-device is hardcoded as %r; it has to be ${VAR}" % value,
        )

    def test_the_variable_has_a_default(self):
        """A host that sets nothing still has to get a working mic."""
        m = re.search(r"--alsa-device\s+\$\{(\w+)\}", self.exec_line)
        self.assertIsNotNone(m, "--alsa-device is not a ${VAR} reference")
        name = m.group(1)
        defaults = [ln for ln in self.lines
                    if re.match(r"Environment=%s=" % name, ln)]
        self.assertEqual(len(defaults), 1,
                         "expected one Environment= default for %s" % name)
        self.assertIn("CARD=", defaults[0],
                      "the default must name the card, not its index: an index "
                      "moves when USB enumerates in a different order")

    def test_environmentfile_can_win(self):
        """systemd applies these in file order, so the override files have to
        come after the default. Move the Environment= line down and every host
        silently reverts to the built-in mic."""
        name = re.search(r"--alsa-device\s+\$\{(\w+)\}", self.exec_line).group(1)
        default_at = next(i for i, ln in enumerate(self.lines)
                          if ln.startswith("Environment=%s=" % name))
        files_at = [i for i, ln in enumerate(self.lines)
                    if ln.startswith("EnvironmentFile=")]
        self.assertTrue(files_at, "unit has no EnvironmentFile= to override from")
        self.assertLess(default_at, min(files_at),
                        "Environment= default sits after EnvironmentFile=, so "
                        "the files can no longer override it")


if __name__ == "__main__":
    unittest.main()
