"""Tests for noticing that the microphone has gone away.

Plugging in a USB audio adapter re-enumerated the bus and arecord lost its
stream: "pcm_read:2272: read error: No such device". The reader thread hit a
short read, broke out of its loop, and ended. Nothing else noticed. pi-hear
stayed up, systemd reported active, Restart=always never fired, and the deck
listened to silence for thirty minutes until someone saw the microphone icon
greyed out on the map.

Reporting healthy while deaf is the worst of the available failures, and the
recovery already exists -- the unit has Restart=always and RestartSec=2. All
that is missing is for the process to admit it.
"""
import os
import subprocess
import sys
import tempfile
import textwrap
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
PI_HEAR = os.path.join(HERE, "..", "pi-hear", "pi_hear.py")


class CaptureDeathTest(unittest.TestCase):
    """Exercises the reader against a stand-in for arecord."""

    def run_reader(self, script, expect_timeout=6):
        """Run the reader loop with a fake arecord and report what happened."""
        harness = textwrap.dedent('''
            import os, subprocess, sys, threading, queue, time
            sys.path.insert(0, %r)
            import pi_hear

            audio_q = queue.Queue()
            stop = threading.Event()
            proc = subprocess.Popen([sys.executable, "-c", %r],
                                    stdout=subprocess.PIPE)
            died = []
            reader = pi_hear.make_alsa_reader(
                proc, audio_q, stop, blocksize=32,
                on_death=lambda why: died.append(why))
            t = threading.Thread(target=reader, daemon=True)
            t.start()
            t.join(timeout=5)
            print("ALIVE" if t.is_alive() else "ENDED")
            print("DIED:" + (died[0] if died else ""))
        ''') % (os.path.join(HERE, "..", "pi-hear"), script)
        r = subprocess.run([sys.executable, "-c", harness],
                           capture_output=True, text=True, timeout=30)
        return r

    def test_a_clean_exit_of_arecord_is_reported(self):
        # arecord exits: the device is gone. This is the observed failure.
        r = self.run_reader("import sys; sys.stdout.buffer.write(b'')")
        self.assertIn("ENDED", r.stdout, r.stderr)
        self.assertNotIn("DIED:\n", r.stdout, "the death was not reported")

    def test_a_short_read_is_reported(self):
        # A partial block then EOF -- what a mid-stream unplug looks like.
        r = self.run_reader(
            "import sys; sys.stdout.buffer.write(b'\\0' * 10); sys.stdout.flush()")
        self.assertIn("ENDED", r.stdout, r.stderr)
        self.assertNotIn("DIED:\n", r.stdout)

    def test_a_healthy_stream_does_not_report_death(self):
        # Endless, so that running out of fake audio cannot be mistaken for the
        # device going away -- which is what the first version of this test did.
        r = self.run_reader(
            "import sys,time\n"
            "while True:\n"
            "    sys.stdout.buffer.write(b'\\0' * 64); sys.stdout.flush()\n"
            "    time.sleep(0.02)\n")
        self.assertIn("ALIVE", r.stdout, r.stderr)
        self.assertIn("DIED:\n", r.stdout, "a healthy stream must not report death")

    def test_a_deliberate_stop_is_not_a_death(self):
        # Shutting down is not the microphone failing; it must not be reported
        # as one, or every clean exit would look like a fault in the journal.
        harness = textwrap.dedent('''
            import subprocess, sys, threading, queue, time
            sys.path.insert(0, %r)
            import pi_hear
            audio_q, stop = queue.Queue(), threading.Event()
            proc = subprocess.Popen(
                [sys.executable, "-c",
                 "import sys,time\\n"
                 "while True:\\n"
                 "    sys.stdout.buffer.write(b'\\\\0'*64); sys.stdout.flush()\\n"
                 "    time.sleep(0.02)\\n"],
                stdout=subprocess.PIPE)
            died = []
            t = threading.Thread(
                target=pi_hear.make_alsa_reader(
                    proc, audio_q, stop, blocksize=32,
                    on_death=lambda why: died.append(why)), daemon=True)
            t.start()
            time.sleep(0.5)
            stop.set()
            proc.kill()
            t.join(timeout=3)
            print("DIED:" + (died[0] if died else ""))
        ''') % (os.path.join(HERE, "..", "pi-hear"),)
        r = subprocess.run([sys.executable, "-c", harness],
                           capture_output=True, text=True, timeout=30)
        self.assertIn("DIED:\n", r.stdout, r.stderr)


if __name__ == "__main__":
    unittest.main()
