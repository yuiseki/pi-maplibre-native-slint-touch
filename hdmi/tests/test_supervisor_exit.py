"""Tests for the key combination that drops the map to the console.

CardKB2, the keyboard on pi5-deck, has no Ctrl key at all -- 42 keys, and Ctrl
is not one of them. So Ctrl+C x2 is not merely awkward there, it is impossible,
and the deck had no way back to a shell. Esc x2 is the same gesture on a key the
keyboard actually has (Fn+1 on CardKB2).

Enter is deliberately NOT an exit: it is pressed constantly while using a map,
and dropping to a console by accident is far worse than not having a shortcut.
It stays a screensaver-wake key only.
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

from supervisor import ExitGesture, KEY_C, KEY_ESC, KEY_ENTER, KEY_LEFTCTRL, KEY_RIGHTCTRL


class ExitGestureTest(unittest.TestCase):
    def setUp(self):
        self.g = ExitGesture(window=1.5)

    def press(self, code, t, value=1):
        return self.g.feed(code, value, t)

    def test_ctrl_c_twice_within_the_window_fires(self):
        self.press(KEY_LEFTCTRL, 0.0)
        self.assertFalse(self.press(KEY_C, 0.1))
        self.assertTrue(self.press(KEY_C, 0.9))

    def test_ctrl_c_twice_too_slowly_does_not_fire(self):
        self.press(KEY_LEFTCTRL, 0.0)
        self.assertFalse(self.press(KEY_C, 0.1))
        self.assertFalse(self.press(KEY_C, 2.0))

    def test_c_without_ctrl_never_fires(self):
        self.assertFalse(self.press(KEY_C, 0.1))
        self.assertFalse(self.press(KEY_C, 0.2))

    def test_releasing_ctrl_stops_counting(self):
        self.press(KEY_LEFTCTRL, 0.0)
        self.assertFalse(self.press(KEY_C, 0.1))
        self.press(KEY_LEFTCTRL, 0.2, value=0)
        self.assertFalse(self.press(KEY_C, 0.3))

    def test_right_ctrl_works_too(self):
        self.press(KEY_RIGHTCTRL, 0.0)
        self.assertFalse(self.press(KEY_C, 0.1))
        self.assertTrue(self.press(KEY_C, 0.5))

    def test_esc_twice_within_the_window_fires(self):
        self.assertFalse(self.press(KEY_ESC, 0.1))
        self.assertTrue(self.press(KEY_ESC, 0.9))

    def test_esc_twice_too_slowly_does_not_fire(self):
        self.assertFalse(self.press(KEY_ESC, 0.1))
        self.assertFalse(self.press(KEY_ESC, 2.0))

    def test_a_single_esc_does_nothing(self):
        self.assertFalse(self.press(KEY_ESC, 0.1))

    def test_enter_is_not_an_exit(self):
        # Pressed constantly while using a map; an accidental console drop is a
        # worse outcome than the missing shortcut.
        self.assertFalse(self.press(KEY_ENTER, 0.1))
        self.assertFalse(self.press(KEY_ENTER, 0.2))

    def test_one_of_each_is_not_a_double(self):
        # Each key keeps its own clock, so an Esc and a Ctrl+C are two singles,
        # not a pair. (Two Ctrl+C with an Esc between them do still pair -- the
        # stray key does not cancel a deliberate double.)
        self.press(KEY_LEFTCTRL, 0.0)
        self.assertFalse(self.press(KEY_ESC, 0.1))
        self.assertFalse(self.press(KEY_C, 0.2))

    def test_the_pair_is_consumed_so_a_third_press_starts_over(self):
        self.assertFalse(self.press(KEY_ESC, 0.1))
        self.assertTrue(self.press(KEY_ESC, 0.5))
        self.assertFalse(self.press(KEY_ESC, 0.9))
        self.assertTrue(self.press(KEY_ESC, 1.3))

    def test_autorepeat_does_not_count(self):
        # value 2 is the kernel's auto-repeat: holding Esc down would otherwise
        # drop to a console all by itself.
        self.assertFalse(self.press(KEY_ESC, 0.1))
        self.assertFalse(self.press(KEY_ESC, 0.3, value=2))
        self.assertFalse(self.press(KEY_ESC, 0.5, value=2))

    def test_key_release_does_not_count(self):
        self.assertFalse(self.press(KEY_ESC, 0.1))
        self.assertFalse(self.press(KEY_ESC, 0.2, value=0))

    def test_esc_still_fires_after_ctrl_was_held(self):
        # A Ctrl left down (say, a stuck modifier) must not disable Esc.
        self.press(KEY_LEFTCTRL, 0.0)
        self.assertFalse(self.press(KEY_ESC, 0.1))
        self.assertTrue(self.press(KEY_ESC, 0.5))

    def test_names_the_gesture_for_the_log(self):
        self.assertFalse(self.press(KEY_ESC, 0.1))
        self.assertEqual(self.g.feed(KEY_ESC, 1, 0.5), "Esc x2")
        self.press(KEY_LEFTCTRL, 1.0)
        self.assertFalse(self.press(KEY_C, 1.1))
        self.assertEqual(self.g.feed(KEY_C, 1, 1.5), "Ctrl+C x2")


if __name__ == "__main__":
    unittest.main()
