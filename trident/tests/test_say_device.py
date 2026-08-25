"""Tests for who decides which speaker a confirmation comes out of.

The map flew to Okinawa in silence while `pi-say "..."` typed by hand was
audible. pi-say reads /etc/default/pi-say, which describes this machine's
speaker; pi-hear was passing --device plughw:0,0 and overriding it, and on this
deck card 0 is HDMI with nothing plugged into it.

The speaker is a property of the machine. A caller should have to *ask* to
override that, not do it by accident through a default.
"""
import os
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
PI_HEAR = os.path.join(HERE, "..", "pi-hear", "pi_hear.py")


def build_parser():
    """The real parser from pi_hear, without importing its audio dependencies."""
    import ast
    src = open(PI_HEAR).read()
    tree = ast.parse(src)
    # Find the --say-device argument's default in the source itself: importing
    # pi_hear needs numpy and sounddevice, which the build host lacks.
    for node in ast.walk(tree):
        if (isinstance(node, ast.Call)
                and getattr(node.func, "attr", "") == "add_argument"
                and node.args
                and isinstance(node.args[0], ast.Constant)
                and node.args[0].value == "--say-device"):
            for kw in node.keywords:
                if kw.arg == "default":
                    return kw.value.value
    raise AssertionError("--say-device not found")


class DefaultTest(unittest.TestCase):
    def test_the_default_defers_to_the_machine(self):
        # Not plughw:0,0, and not any other device: empty, so pi-say is left to
        # read /etc/default/pi-say. Anything else here silently overrides the
        # one place the speaker is actually described.
        self.assertEqual(build_parser(), "")

    def test_the_flag_is_only_added_when_set(self):
        """The --device argument must sit behind a truth test on the value.

        Checked structurally rather than by substring: the first version of this
        test asserted the string '"--device", args.say_device' was absent, which
        is also a substring of the *fixed* code, so it failed on a correct file.
        """
        import ast
        tree = ast.parse(open(PI_HEAR).read())
        guarded = False
        for node in ast.walk(tree):
            if not isinstance(node, ast.If):
                continue
            # if args.say_device: ...
            t = node.test
            if not (isinstance(t, ast.Attribute) and t.attr == "say_device"):
                continue
            if "--device" in ast.dump(ast.Module(body=node.body, type_ignores=[])):
                guarded = True
        self.assertTrue(guarded,
                        "--device is not behind `if args.say_device:`")

    def test_the_pi_say_command_starts_bare(self):
        """The command list handed to pi-say must begin with no --device.

        Scoped to the pi-say invocation: pi_hear also has its own --device flag
        for the microphone, and an earlier version of this test failed on that
        one, which has nothing to do with the speaker.
        """
        import ast
        tree = ast.parse(open(PI_HEAR).read())
        found = False
        for node in ast.walk(tree):
            if not isinstance(node, ast.Assign):
                continue
            if not (isinstance(node.value, ast.List) and node.value.elts):
                continue
            first = node.value.elts[0]
            if not (isinstance(first, ast.Constant)
                    and isinstance(first.value, str)
                    and first.value.endswith("pi-say")):
                continue
            found = True
            self.assertEqual(len(node.value.elts), 1,
                             "the pi-say command should start as just the "
                             "binary; the device is appended only if asked for")
        self.assertTrue(found, "no pi-say command list found")


if __name__ == "__main__":
    unittest.main()
