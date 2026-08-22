#!/usr/bin/env python3
"""Tests for the style list parser, driven through a tiny C++ harness.

The list of map styles used to be two parallel arrays typed into the .slint
file, which meant that pointing the device at a different server -- the whole
of making it work off-grid -- was a rebuild. It is now a `label,url` file read
at startup.

The parsing is small but every rule in it exists because getting it wrong is
silent: a style list that comes back empty leaves an appliance with an empty
dropdown and no way to tell why.
"""
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
SRC = HERE.parent / "src" / "style_list.cpp"
HDR = HERE.parent / "src" / "style_list.hpp"
HARNESS = HERE / "style_list_main.cpp"


def build():
    exe = Path(tempfile.mkdtemp()) / "style_list_test"
    r = subprocess.run(
        ["g++", "-std=c++20", "-O0", "-I", str(SRC.parent),
         str(SRC), str(HARNESS), "-o", str(exe)],
        capture_output=True, text=True)
    if r.returncode != 0:
        raise AssertionError("harness build failed:\n" + r.stderr)
    return exe


class StyleList(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.exe = build()

    def parse(self, text):
        """Returns [(label, url), ...] as the C++ parser sees it."""
        with tempfile.NamedTemporaryFile("w", suffix=".csv", delete=False,
                                         encoding="utf-8") as f:
            f.write(text)
            path = f.name
        r = subprocess.run([str(self.exe), path], capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        out = []
        for line in r.stdout.splitlines():
            if line:
                label, _, url = line.partition("\t")
                out.append((label, url))
        return out

    def test_reads_label_and_url(self):
        self.assertEqual(
            self.parse("OSM Bright,http://localhost/static/styles/osm-bright.json\n"),
            [("OSM Bright", "http://localhost/static/styles/osm-bright.json")])

    def test_keeps_order(self):
        got = self.parse("A,http://a\nB,http://b\nC,http://c\n")
        self.assertEqual([l for l, _ in got], ["A", "B", "C"])

    def test_comments_and_blank_lines_are_skipped(self):
        got = self.parse("# a comment\n\nA,http://a\n   \n# another\nB,http://b\n")
        self.assertEqual(len(got), 2)

    def test_surrounding_whitespace_is_trimmed(self):
        got = self.parse("  OSM Bright  ,  http://a  \n")
        self.assertEqual(got, [("OSM Bright", "http://a")])

    def test_only_the_first_comma_separates(self):
        # Labels are free text and may contain commas; the URL never does.
        got = self.parse("Tokyo, Japan,http://a\n")
        self.assertEqual(got, [("Tokyo, Japan", "http://a")])

    def test_a_line_without_a_comma_is_skipped(self):
        got = self.parse("A,http://a\nnonsense\nB,http://b\n")
        self.assertEqual(len(got), 2)

    def test_a_line_with_an_empty_url_is_skipped(self):
        got = self.parse("A,http://a\nB,\n")
        self.assertEqual(len(got), 1)

    def test_a_line_with_an_empty_label_is_skipped(self):
        # Better an entry missing than a blank row in the dropdown.
        got = self.parse("A,http://a\n,http://b\n")
        self.assertEqual(len(got), 1)

    def test_japanese_labels_survive(self):
        got = self.parse("地図,http://a\n")
        self.assertEqual(got, [("地図", "http://a")])

    def test_crlf_is_tolerated(self):
        got = self.parse("A,http://a\r\nB,http://b\r\n")
        self.assertEqual(got, [("A", "http://a"), ("B", "http://b")])

    def test_a_missing_file_yields_nothing(self):
        r = subprocess.run([str(self.exe), "/nonexistent/styles.csv"],
                           capture_output=True, text=True)
        self.assertEqual(r.returncode, 0)
        self.assertEqual(r.stdout.strip(), "")

    def test_an_empty_file_yields_nothing(self):
        self.assertEqual(self.parse(""), [])


if __name__ == "__main__":
    unittest.main()
