"""Tests for pi-lang, which changes the language pi-hear listens in.

The language lives in a file the service reads at startup, so changing it means
writing the file and restarting. Two things make that more delicate than it
sounds: the restart kills whatever asked for it, and the file is read by a
root-owned unit while the asking is done by a voice loop running as a user.
"""
import os
import subprocess
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
PI_LANG = os.path.join(HERE, "..", "bin", "pi-lang")


def run(args, conf=None, extra=None):
    env = dict(os.environ)
    env["PI_LANG_SYSTEMCTL"] = "true"          # never restart anything in a test
    env["PI_LANG_SUDO"] = ""                   # the fixture file is ours already
    if conf is not None:
        path = os.path.join(tempfile.mkdtemp(prefix="pi-lang."), "lang")
        if conf:
            with open(path, "w") as fh:
                fh.write(conf)
        env["PI_LANG_FILE"] = path
    if extra:
        env.update(extra)
    r = subprocess.run(["bash", PI_LANG] + args, capture_output=True,
                       text=True, env=env)
    return r, env.get("PI_LANG_FILE")


class PiLangTest(unittest.TestCase):
    def test_it_writes_the_language(self):
        r, path = run(["ja"], conf="PI_HEAR_LANG=en\n")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("PI_HEAR_LANG=ja", open(path).read())

    def test_it_creates_the_file_when_missing(self):
        r, path = run(["en"], conf="")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("PI_HEAR_LANG=en", open(path).read())

    def test_it_reports_the_current_language(self):
        r, _ = run([], conf="PI_HEAR_LANG=en\n")
        self.assertIn("en", r.stdout)

    def test_only_the_two_supported_languages_are_accepted(self):
        # 'auto' costs 2.6x the latency and misidentifies accented English; it
        # is not something to reach by accident from a misheard sentence.
        # An empty argument is "tell me the current one", not a bad language.
        for bad in ("auto", "fr", "ja; rm -rf /", "../../etc/passwd", "JA"):
            r, path = run([bad], conf="PI_HEAR_LANG=en\n")
            self.assertNotEqual(r.returncode, 0, repr(bad))
            self.assertIn("PI_HEAR_LANG=en", open(path).read(), repr(bad))

    def test_the_file_holds_nothing_but_the_language(self):
        # It is written by the voice loop, so it is the one thing a misheard
        # command can affect. Everything else about pi-hear stays where only
        # root can reach it.
        r, path = run(["ja"], conf="")
        body = [l for l in open(path).read().splitlines()
                if l.strip() and not l.startswith("#")]
        self.assertEqual(body, ["PI_HEAR_LANG=ja"])

    def test_asking_for_the_current_language_does_not_restart(self):
        # Restarting to change nothing costs the deck its ears for a few
        # seconds and looks like a fault.
        r, _ = run(["en"], conf="PI_HEAR_LANG=en\n")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("already", r.stdout.lower())

    def test_a_comment_in_the_file_survives(self):
        r, path = run(["ja"], conf="# hand-written note\nPI_HEAR_LANG=en\n")
        self.assertIn("hand-written note", open(path).read())


if __name__ == "__main__":
    unittest.main()
