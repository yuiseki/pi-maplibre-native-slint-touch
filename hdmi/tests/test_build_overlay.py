"""Tests for build.sh's overlay step.

The build works by copying this directory's sources over a checkout of
maplibre-native-slint and building the target there. Copying unconditionally
means every file's mtime changes on every run, so ninja rebuilds everything --
and, worse, sees CMakeLists.txt as changed and re-runs CMake's configure.

Re-configuring is not free at this pinned ref: Slint is fetched from the moving
release/1 branch, so the update step tries to rebase the local Slint checkout
onto whatever upstream is today, dies in merge conflicts, and leaves the tree
needing a regeneration that then fails on every subsequent build too. That has
already destroyed one working build environment.

So the overlay must be idempotent: running it twice must leave the second run
with nothing to do.
"""
import os
import shutil
import subprocess
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
BUILD_SH = os.path.join(HERE, "..", "scripts", "build.sh")

BANNER = "# --- Zero-copy OpenGL example (maplibre-slint-gl) ---"


class OverlayTest(unittest.TestCase):
    """Exercises build.sh with OVERLAY_ONLY=1, which stops before configuring."""

    def setUp(self):
        self.work = tempfile.mkdtemp(prefix="mln-work.")
        for sub in ("cpp/src", "cpp/platform", "cpp/assets", "build"):
            os.makedirs(os.path.join(self.work, sub), exist_ok=True)
        os.makedirs(os.path.join(self.work, ".git"), exist_ok=True)
        with open(os.path.join(self.work, "cpp", "CMakeLists.txt"), "w") as fh:
            fh.write("add_library(other STATIC other.cpp)\n" + BANNER + "\nold target\n")
        self.addCleanup(shutil.rmtree, self.work, True)

    def run_overlay(self):
        env = dict(os.environ, WORK=self.work, OVERLAY_ONLY="1", SYNC="0")
        return subprocess.run(["bash", BUILD_SH], capture_output=True,
                              text=True, env=env)

    def snapshot(self):
        out = {}
        for root, _dirs, files in os.walk(self.work):
            if "/.git" in root:
                continue
            for name in files:
                p = os.path.join(root, name)
                st = os.stat(p)
                out[p] = (st.st_mtime_ns, st.st_size)
        return out

    def test_the_first_run_copies_the_sources(self):
        r = self.run_overlay()
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertTrue(os.path.exists(
            os.path.join(self.work, "cpp", "main_gl.cpp")))
        self.assertTrue(os.path.exists(
            os.path.join(self.work, "cpp", "src", "style_list.cpp")))

    def test_the_target_definition_is_overlaid(self):
        self.run_overlay()
        body = open(os.path.join(self.work, "cpp", "CMakeLists.txt")).read()
        self.assertIn(BANNER, body)
        self.assertIn("maplibre-slint-gl", body)
        self.assertNotIn("old target", body)
        # everything above the banner is upstream's and must survive
        self.assertIn("add_library(other STATIC other.cpp)", body)

    def test_a_second_run_changes_nothing(self):
        # The point of the whole exercise: no mtime moves, so ninja neither
        # rebuilds nor -- the dangerous part -- re-runs configure.
        self.run_overlay()
        before = self.snapshot()
        r = self.run_overlay()
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(self.snapshot(), before)

    def test_a_changed_source_is_copied(self):
        self.run_overlay()
        target = os.path.join(self.work, "cpp", "main_gl.cpp")
        with open(target, "a") as fh:
            fh.write("\n// local edit\n")
        before = os.stat(target).st_mtime_ns
        self.run_overlay()
        self.assertNotEqual(os.stat(target).st_mtime_ns, before)
        self.assertNotIn("// local edit", open(target).read())

    def test_the_banner_is_required(self):
        # Without it we would not know where upstream's file ends, and
        # appending blindly would define the target twice.
        with open(os.path.join(self.work, "cpp", "CMakeLists.txt"), "w") as fh:
            fh.write("add_library(other STATIC other.cpp)\n")
        r = self.run_overlay()
        self.assertIn("banner", (r.stdout + r.stderr).lower())

    def test_it_reports_what_it_changed(self):
        first = self.run_overlay()
        second = self.run_overlay()
        self.assertNotEqual(first.stdout, second.stdout)
        self.assertIn("unchanged", second.stdout.lower())


if __name__ == "__main__":
    unittest.main()
