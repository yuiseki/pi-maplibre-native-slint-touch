"""Tests for pi-kbd's presence check and Wi-Fi quieting arithmetic.

The one thing this script must never do is report success when the keyboard is
not actually usable. BlueZ's own "Connected" flag cannot be trusted for that:
it is set from LE Connection Complete, which the controller reports the moment
CONNECT_IND goes out, before the peripheral has answered anything. Measured on
pi5-deck: 6/6 "connected" in a run where the link layer established zero times.
So the source of truth is the HID input node -- if that exists, keys can arrive.
"""
import atexit
import os
import shutil
import subprocess
import tempfile
import textwrap
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
PI_KBD = os.path.join(HERE, "..", "bin", "pi-kbd")

# Fixtures are files because the thing under test is a shell script, but they do
# not belong next to the source -- an earlier version scattered two dozen stubs
# into the working tree.
TMP = tempfile.mkdtemp(prefix="pi-kbd-tests.")
atexit.register(shutil.rmtree, TMP, True)

# A real /proc/bus/input/devices excerpt from pi5-deck, with the keyboard bonded
# to both controllers at once (this genuinely happens mid-migration).
BOTH = textwrap.dedent("""\
    I: Bus=0005 Vendor=16c0 Product=05df Version=0100
    N: Name="CardKB2-CE8A"
    P: Phys=8c:86:dd:82:6a:30
    S: Sysfs=/devices/virtual/misc/uhid/0005:16C0:05DF.0001/input/input6
    U: Uniq=88:56:a6:c2:ce:8a
    H: Handlers=sysrq kbd event6 leds
    B: PROP=0

    I: Bus=0005 Vendor=16c0 Product=05df Version=0100
    N: Name="CardKB2-CE8A"
    P: Phys=88:a2:9e:a2:e3:32
    S: Sysfs=/devices/virtual/misc/uhid/0005:16C0:05DF.0002/input/input7
    U: Uniq=88:56:a6:c2:ce:8a
    H: Handlers=sysrq kbd event7 leds
    B: PROP=0
    """)

DONGLE_ONLY = textwrap.dedent("""\
    I: Bus=0005 Vendor=16c0 Product=05df Version=0100
    N: Name="CardKB2-CE8A"
    P: Phys=8c:86:dd:82:6a:30
    S: Sysfs=/devices/virtual/misc/uhid/0005:16C0:05DF.0001/input/input6
    U: Uniq=88:56:a6:c2:ce:8a
    H: Handlers=sysrq kbd event6 leds
    B: PROP=0
    """)

NOTHING = textwrap.dedent("""\
    I: Bus=0019 Vendor=0000 Product=0006 Version=0000
    N: Name="vc4-hdmi-0"
    P: Phys=
    S: Sysfs=/devices/platform/soc@107c000000/107c701400.hdmi/sound/card0/input3
    U: Uniq=
    H: Handlers=event2
    B: PROP=0
    """)

# Same MAC, but the node belongs to a different keyboard's uniq -- must not match.
OTHER_KEYBOARD = textwrap.dedent("""\
    I: Bus=0005 Vendor=05ac Product=0250 Version=0100
    N: Name="Some Other Keyboard"
    P: Phys=88:a2:9e:a2:e3:32
    S: Sysfs=/devices/virtual/misc/uhid/0005:05AC:0250.0003/input/input9
    U: Uniq=11:22:33:44:55:66
    H: Handlers=sysrq kbd event9 leds
    B: PROP=0
    """)


_btctl_seq = [0]


def fake_btctl(script):
    """A stand-in for a command whose output changes per call.

    Each call gets its own files: sharing one path let the default stub clobber
    the one a test had just written, and every liveness test silently passed.
    """
    _btctl_seq[0] += 1
    path = os.path.join(TMP, "btctl.%d" % _btctl_seq[0])
    state = os.path.join(TMP, "btctl.%d.state" % _btctl_seq[0])
    with open(state, "w") as fh:
        fh.write("0\n")
    with open(path, "w") as fh:
        fh.write("#!/bin/bash\n")
        fh.write("n=$(cat %s)\n" % state)
        fh.write("echo $((n+1)) > %s\n" % state)
        fh.write("case $n in\n")
        for i, line in enumerate(script):
            fh.write("  %d) echo '%s';;\n" % (i, line))
        fh.write("  *) echo '%s';;\n" % (script[-1] if script else ""))
        fh.write("esac\n")
    os.chmod(path, 0o755)
    return path


# hcitool -i hciN con output, which is the kernel's live connection list.
CONNECTED = "< LE 88:56:A6:C2:CE:8A handle 64 state 1 lm CENTRAL"
NOT_CONNECTED = "Connections:"


def run(args, devices=None, env=None):
    e = dict(os.environ)
    e["PI_KBD_CONF"] = "/nonexistent"
    # The workstation these tests run on has no onboard Bluetooth, so autodetection
    # would find nothing; name the controller explicitly and test detection apart.
    e["PI_KBD_ADAPTER"] = "88:A2:9E:A2:E3:32"
    e["PI_KBD_SETTLE"] = "3"
    e["PI_KBD_DEV"] = "hci0"
    e["PI_KBD_SUDO"] = ""      # the stubs are plain scripts, not privileged tools
    if not (env and "PI_KBD_HCITOOL" in env):
        e["PI_KBD_HCITOOL"] = fake_btctl([CONNECTED])
    e.setdefault("PI_KBD_BTCTL", "true")
    e.setdefault("PI_KBD_HOLD", "0")
    if devices is not None:
        path = os.path.join(TMP, "devices")
        with open(path, "w") as fh:
            fh.write(devices)
        e["PI_KBD_INPUT_DEVICES"] = path
    if env:
        e.update(env)
    return subprocess.run(["bash", PI_KBD] + args, capture_output=True, text=True, env=e)


class PresenceTest(unittest.TestCase):
    """`pi-kbd present` is the success criterion the rest of the script leans on."""

    def test_present_on_the_named_controller(self):
        r = run(["present"], BOTH)
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_absent_when_only_the_dongle_has_it(self):
        # The whole point of the exercise is running without the USB dongle, so a
        # node that hangs off hci1 must not count as success for hci0.
        r = run(["present"], DONGLE_ONLY)
        self.assertNotEqual(r.returncode, 0)

    def test_absent_when_no_keyboard_at_all(self):
        r = run(["present"], NOTHING)
        self.assertNotEqual(r.returncode, 0)

    def test_a_different_keyboard_on_the_same_controller_is_not_ours(self):
        r = run(["present"], OTHER_KEYBOARD)
        self.assertNotEqual(r.returncode, 0)

    def test_matching_is_case_insensitive(self):
        # /proc reports addresses lowercase; bluetoothctl and humans write them
        # uppercase, and the config file is written by a human.
        import re
        upper = re.sub(r"(?i)\b([0-9a-f]{2}(?::[0-9a-f]{2}){5})\b",
                       lambda m: m.group(1).upper(), BOTH)
        self.assertIn("88:56:A6:C2:CE:8A", upper)
        r = run(["present"], upper)
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_status_names_the_input_node_once(self):  # noqa: D401
        # The lookup used to print the match twice: awk's exit runs the END rule,
        # which re-emitted the same block. A doubled node is a doubled truth.
        r = run(["status"], BOTH)
        self.assertEqual(r.stdout.count("event7"), 1, r.stdout)

    def test_dongle_only_is_reported_as_such(self):
        r = run(["status"], DONGLE_ONLY)
        self.assertIn("8c:86:dd:82:6a:30", r.stdout.lower())


class LinkLivenessTest(unittest.TestCase):
    """The failure this whole exercise is about looks like a connection at first.

    A 0x3e collapse happens ~340 ms after the controller reports Connection
    Complete, and BlueZ retries at once, so a single reading of Connected can
    say yes while nothing works. Only a reading that holds means anything.
    """

    def test_steady_connection_counts_as_present(self):
        r = run(["present"], BOTH, env={"PI_KBD_HCITOOL": fake_btctl([CONNECTED])})
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_a_flapping_connection_is_not_present(self):
        # yes, yes, no -- exactly what a retry storm looks like from up here.
        r = run(["present"], BOTH,
                env={"PI_KBD_HCITOOL": fake_btctl([CONNECTED, CONNECTED, NOT_CONNECTED])})
        self.assertNotEqual(r.returncode, 0)

    def test_a_lingering_node_with_a_dead_link_is_not_present(self):
        # BlueZ keeps the uhid node across a disconnect, so the node alone lies.
        r = run(["present"], BOTH, env={"PI_KBD_HCITOOL": fake_btctl([NOT_CONNECTED])})
        self.assertNotEqual(r.returncode, 0)

    def test_status_says_so_when_the_node_outlives_the_link(self):
        r = run(["status"], BOTH, env={"PI_KBD_HCITOOL": fake_btctl([NOT_CONNECTED])})
        self.assertIn("link is down", r.stdout)
        self.assertNotEqual(r.returncode, 0)

    def test_connect_does_not_skip_work_when_the_link_is_down(self):
        r = run(["connect", "--dry-run"], BOTH,
                env={"PI_KBD_HCITOOL": fake_btctl([NOT_CONNECTED])})
        self.assertIn("txpower fixed", r.stdout)


class ConfigTest(unittest.TestCase):
    def test_mac_and_controller_come_from_the_environment(self):
        r = run(["present"], BOTH,
                env={"PI_KBD_MAC": "88:56:A6:C2:CE:8A",
                     "PI_KBD_ADAPTER": "88:A2:9E:A2:E3:32"})
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_an_unknown_controller_finds_nothing(self):
        r = run(["present"], BOTH, env={"PI_KBD_ADAPTER": "00:00:00:00:00:00"})
        self.assertNotEqual(r.returncode, 0)


class DryRunTest(unittest.TestCase):
    """--dry-run must show the Wi-Fi quieting, and must always restore it."""

    def test_connect_lowers_then_restores_txpower(self):
        r = run(["connect", "--dry-run"], NOTHING)
        out = r.stdout
        self.assertIn("txpower fixed", out)
        # "auto" appears twice: once inside the armed dead-man, once as the real
        # restore at the end. The restore is the last one.
        self.assertLess(out.index("txpower fixed"), out.rindex("txpower auto"),
                        "must lower before restoring")
        # The dead-man is only stood down after the radio is actually back.
        self.assertLess(out.rindex("txpower auto"), out.index("systemctl stop"),
                        "restore the radio before disarming the dead-man")

    def test_dry_run_arms_the_dead_man_before_lowering(self):
        # If we die between lowering and restoring, the deck is left on a 1 dBm
        # Wi-Fi link. Arming first is the same rule pi-net follows for the network.
        r = run(["connect", "--dry-run"], NOTHING)
        out = r.stdout
        self.assertIn("systemd-run", out)
        self.assertLess(out.index("systemd-run"), out.index("txpower fixed"),
                        "must arm the restore before lowering")

    def test_dry_run_touches_nothing_when_already_present(self):
        r = run(["connect", "--dry-run"], BOTH)
        self.assertNotIn("txpower fixed", r.stdout)
        self.assertEqual(r.returncode, 0)

    def test_txpower_is_configurable(self):
        r = run(["connect", "--dry-run"], NOTHING, env={"PI_KBD_TXPOWER_MBM": "500"})
        self.assertIn("txpower fixed 500", r.stdout)


class UsageTest(unittest.TestCase):
    def test_unknown_command_fails_loudly(self):
        r = run(["frobnicate"], NOTHING)
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("usage", (r.stdout + r.stderr).lower())

    def test_help_exits_clean(self):
        r = run(["--help"], NOTHING)
        self.assertEqual(r.returncode, 0)


class AdapterDetectionTest(unittest.TestCase):
    """The onboard controller is the one that is not on USB.

    Real hciconfig output from pi5-deck: the Realtek dongle enumerates first, so
    "the first controller" is the wrong rule -- the bus is what distinguishes them.
    """

    HCICONFIG = textwrap.dedent("""\
        hci1:\tType: Primary  Bus: USB
        \tBD Address: 8C:86:DD:82:6A:30  ACL MTU: 1021:6  SCO MTU: 255:12
        \tUP RUNNING
        \tName: 'pi5-deck #2'

        hci0:\tType: Primary  Bus: UART
        \tBD Address: 88:A2:9E:A2:E3:32  ACL MTU: 1021:8  SCO MTU: 64:1
        \tUP RUNNING PSCAN
        \tName: 'pi5-deck'
        """)

    USB_ONLY = textwrap.dedent("""\
        hci1:\tType: Primary  Bus: USB
        \tBD Address: 8C:86:DD:82:6A:30  ACL MTU: 1021:6  SCO MTU: 255:12
        \tUP RUNNING
        """)

    def _listing(self, text):
        path = os.path.join(TMP, "hciconfig")
        with open(path, "w") as fh:
            fh.write(text)
        return path

    def test_reports_the_uart_device_name(self):
        r = run(["dev"], BOTH,
                env={"PI_KBD_ADAPTER": "", "PI_KBD_DEV": "",
                     "PI_KBD_HCICONFIG_OUT": self._listing(self.HCICONFIG)})
        self.assertEqual(r.stdout.strip(), "hci0", r.stdout)

    def test_picks_the_uart_controller_not_the_dongle(self):
        r = run(["status"], BOTH,
                env={"PI_KBD_ADAPTER": "",
                     "PI_KBD_HCICONFIG_OUT": self._listing(self.HCICONFIG)})
        self.assertIn("onboard  : 88:a2:9e:a2:e3:32", r.stdout.lower())
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_reports_no_controller_when_only_usb_is_present(self):
        r = run(["status"], BOTH,
                env={"PI_KBD_ADAPTER": "",
                     "PI_KBD_HCICONFIG_OUT": self._listing(self.USB_ONLY)})
        self.assertIn("none found", r.stdout)
        self.assertNotEqual(r.returncode, 0)

    def test_connect_refuses_without_an_onboard_controller(self):
        r = run(["connect", "--dry-run"], NOTHING,
                env={"PI_KBD_ADAPTER": "",
                     "PI_KBD_HCICONFIG_OUT": self._listing(self.USB_ONLY)})
        self.assertNotEqual(r.returncode, 0)
        self.assertNotIn("txpower fixed", r.stdout)


if __name__ == "__main__":
    unittest.main()
