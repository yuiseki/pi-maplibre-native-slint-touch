"""Tests for when pi-mesh may write this host's position into the node.

pi-mesh is the single owner of the serial port, so it is also the only thing
that can tell the node where it is. The question it has to answer is not "do we
have a position" but "where did this position come from", and getting that
wrong is not a small error:

  - A fix from a USB receiver is a measurement. Sharing it is the point.
  - A position pi-mesh itself wrote out of the node's *own* fix must never be
    sent back. The node would record it as LOC_MANUAL, and a hand-set position
    does not age out -- so a fix that died hours ago would keep being reported
    as current, to the whole mesh.
  - A position from pi-wigle is a third thing: not from satellites, but not
    from the node either. Indoors it is the only answer anyone has, and the
    laundering argument does not apply to it.

The five-field file cannot say which of those it is, so the writer names
itself in a sixth field. Readers that only ever wanted five keep working: they
index the fields they know.
"""
import importlib.machinery
import importlib.util
import os
import tempfile
import time
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
TOOL = os.path.join(HERE, "..", "bin", "pi-mesh")


def load():
    loader = importlib.machinery.SourceFileLoader("pi_mesh", TOOL)
    spec = importlib.util.spec_from_loader("pi_mesh", loader)
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


m = load()


class MayShare(unittest.TestCase):
    def test_a_usb_receiver_is_the_original_reason(self):
        self.assertTrue(m.may_share(True, "/dev/ttyACM0", None))
        self.assertTrue(m.may_share(True, "/dev/ttyACM0", "gps"))

    def test_off_is_off_whatever_the_source(self):
        self.assertFalse(m.may_share(False, "/dev/ttyACM0", "wigle"))
        self.assertFalse(m.may_share(False, None, "wigle"))

    def test_the_nodes_own_fix_is_never_sent_back(self):
        # This is the case the whole rule exists for: with no receiver, pi-mesh
        # writes the file itself out of the node's fix. Returning it would turn
        # a measurement into a hand-set position that never expires.
        self.assertFalse(m.may_share(True, None, None))
        self.assertFalse(m.may_share(True, None, "mesh"))

    def test_wigle_may_share_without_a_receiver(self):
        # Indoors there is no receiver and no node fix. WiFi trilateration is
        # not the node talking to itself, so there is nothing to launder.
        self.assertTrue(m.may_share(True, None, "wigle"))

    def test_an_unknown_source_is_treated_as_the_node(self):
        # Refusing is the safe default: a writer that does not name itself
        # might be this process, and the cost of guessing wrong is a stale
        # position broadcast to the mesh as current.
        self.assertFalse(m.may_share(True, None, "something-new"))


class ReadSource(unittest.TestCase):
    def write(self, text):
        d = tempfile.mkdtemp(prefix="pi-mesh-src.")
        p = os.path.join(d, "pi-gps")
        with open(p, "w") as fh:
            fh.write(text)
        return p

    def test_the_sixth_field_names_the_writer(self):
        p = self.write("34.387604 132.454813 1 0 0 wigle\n")
        self.assertEqual(m.fix_source(p), "wigle")

    def test_five_fields_name_nobody(self):
        # Every writer that predates this, which is all of them.
        p = self.write("34.385000 132.455000 1 7 9\n")
        self.assertIsNone(m.fix_source(p))

    def test_a_missing_file_names_nobody(self):
        self.assertIsNone(m.fix_source("/nonexistent/pi-gps"))

    def test_junk_is_not_a_source(self):
        self.assertIsNone(m.fix_source(self.write("\n")))


class ReceiverFixStillWorks(unittest.TestCase):
    """The sixth field must not disturb the five that were already there."""

    def write(self, text):
        d = tempfile.mkdtemp(prefix="pi-mesh-fix.")
        p = os.path.join(d, "pi-gps")
        with open(p, "w") as fh:
            fh.write(text)
        return p

    def test_a_six_field_line_still_parses_as_a_fix(self):
        p = self.write("34.387604 132.454813 1 0 0 wigle\n")
        self.assertEqual(m.receiver_fix(p, 120), (34.387604, 132.454813))

    def test_five_fields_parse_as_before(self):
        p = self.write("34.385000 132.455000 1 7 9\n")
        self.assertEqual(m.receiver_fix(p, 120), (34.385, 132.455))

    def test_fix_zero_is_not_a_position(self):
        p = self.write("0.000000 0.000000 0 0 1\n")
        self.assertIsNone(m.receiver_fix(p, 120))

    def test_stale_is_silence(self):
        p = self.write("34.385000 132.455000 1 7 9\n")
        self.assertIsNone(m.receiver_fix(p, 1, now=time.time() + 3600))


if __name__ == "__main__":
    unittest.main()
