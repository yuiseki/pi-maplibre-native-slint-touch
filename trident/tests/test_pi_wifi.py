"""Tests for pi-wifi, which manages which Wi-Fi networks the deck knows.

The deck's default network is the cluster AP, and losing it on a headless
machine costs a keyboard and a screen to get back. So the two operations are
deliberately asymmetric: adding a profile must never touch the current link,
and switching must refuse to run while connected unless it is forced.

Those are the properties worth testing, and they are all decided before nmcli
is invoked -- so a fake nmcli that records its arguments tests them without a
radio, without root, and without changing the machine running the test.
"""
import os
import subprocess
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
PI_WIFI = os.path.join(HERE, "..", "bin", "pi-wifi")

# A fake nmcli. Every invocation is appended to $NMCLI_LOG, one line per call,
# and the reply comes from a per-subcommand fixture file if one exists.
FAKE_NMCLI = r"""#!/usr/bin/env bash
printf '%s\n' "$*" >> "$NMCLI_LOG"
if [ -n "${NMCLI_CONN_NAMES+x}" ] && [ "$*" = "-t -f NAME connection show" ]; then
    printf '%s\n' $NMCLI_CONN_NAMES
    exit 0
fi
if [ -n "${NMCLI_DEV_STATUS+x}" ] && [ "$*" = "-t -f DEVICE,STATE,CONNECTION device status" ]; then
    printf '%s\n' "$NMCLI_DEV_STATUS"
    exit 0
fi
exit "${NMCLI_RC:-0}"
"""


FAKE_JOURNALCTL = r"""#!/usr/bin/env bash
printf '%s\n' "${JOURNAL_LINES:-}"
"""


def run(args, conn_names=None, dev_status=None, rc=None, journal=None):
    """Run pi-wifi with a fake nmcli. Returns (returncode, stdout, stderr, calls)."""
    tmp = tempfile.mkdtemp(prefix="pi-wifi.")
    fake = os.path.join(tmp, "nmcli")
    with open(fake, "w") as fh:
        fh.write(FAKE_NMCLI)
    os.chmod(fake, 0o755)
    log = os.path.join(tmp, "calls")
    open(log, "w").close()

    fakej = os.path.join(tmp, "journalctl")
    with open(fakej, "w") as fh:
        fh.write(FAKE_JOURNALCTL)
    os.chmod(fakej, 0o755)

    env = dict(os.environ)
    env["PI_WIFI_NMCLI"] = fake
    env["PI_WIFI_SUDO"] = ""        # never escalate in a test
    env["PI_WIFI_JOURNALCTL"] = fakej
    env["JOURNAL_LINES"] = journal or ""
    env["NMCLI_LOG"] = log
    if conn_names is not None:
        env["NMCLI_CONN_NAMES"] = conn_names
    if dev_status is not None:
        env["NMCLI_DEV_STATUS"] = dev_status
    if rc is not None:
        env["NMCLI_RC"] = str(rc)

    p = subprocess.run(["bash", PI_WIFI] + args, env=env,
                       capture_output=True, text=True, timeout=30)
    with open(log) as fh:
        calls = [ln.rstrip("\n") for ln in fh if ln.strip()]
    return p.returncode, p.stdout, p.stderr, calls


class AddingAProfileIsAlwaysSafe(unittest.TestCase):

    def test_adding_never_activates_anything(self):
        # The whole point: a new network can be typed in from the field while
        # the deck stays on the link that is currently working.
        rc, out, err, calls = run(["--ssid", "cafe-wifi", "--pass", "hunter2"])
        self.assertEqual(rc, 0, err)
        joined = " | ".join(calls)
        self.assertNotIn("connection up", joined)
        self.assertNotIn("device disconnect", joined)

    def test_a_new_profile_does_not_autoconnect(self):
        # Otherwise the next boot could prefer the cafe over the cluster AP.
        rc, out, err, calls = run(["--ssid", "cafe-wifi", "--pass", "hunter2"])
        add = [c for c in calls if "connection add" in c]
        self.assertEqual(len(add), 1, calls)
        self.assertIn("connection.autoconnect no", add[0])

    def test_a_new_profile_carries_the_passphrase(self):
        rc, out, err, calls = run(["--ssid", "cafe-wifi", "--pass", "hunter2"])
        add = [c for c in calls if "connection add" in c][0]
        self.assertIn("wifi-sec.key-mgmt wpa-psk", add)
        self.assertIn("wifi-sec.psk hunter2", add)

    def test_an_open_network_asks_for_no_key(self):
        rc, out, err, calls = run(["--ssid", "OpenAP"])
        add = [c for c in calls if "connection add" in c][0]
        self.assertNotIn("wifi-sec", add)
        self.assertIn("open profile", out)

    def test_an_existing_profile_is_modified_not_duplicated(self):
        rc, out, err, calls = run(["--ssid", "cafe-wifi", "--pass", "new-one"],
                                  conn_names="cafe-wifi")
        self.assertEqual(rc, 0, err)
        self.assertTrue(any("connection modify" in c for c in calls), calls)
        self.assertFalse(any("connection add" in c for c in calls), calls)
        self.assertIn("updated", out)

    def test_ssid_alone_means_add(self):
        # --ssid with no action word is an add, not a status query.
        rc, out, err, calls = run(["--ssid", "cafe-wifi", "--pass", "x"])
        self.assertTrue(any("connection add" in c for c in calls), calls)

    def test_the_interface_is_honoured(self):
        rc, out, err, calls = run(
            ["--ssid", "cafe-wifi", "--pass", "x", "--iface", "wlan1"])
        add = [c for c in calls if "connection add" in c][0]
        self.assertIn("ifname wlan1", add)


class SwitchingProtectsTheCurrentLink(unittest.TestCase):

    CONNECTED = "wlan0:connected:pi5-w-1"
    IDLE = "wlan0:disconnected:"

    def test_switching_is_refused_while_connected(self):
        rc, out, err, calls = run(["--switch", "cafe-wifi"],
                                  dev_status=self.CONNECTED)
        self.assertEqual(rc, 1)
        self.assertIn("--force", err)
        self.assertFalse(any("connection up" in c for c in calls), calls)

    def test_the_refusal_names_what_it_is_protecting(self):
        # "already connected" alone does not say what would be lost.
        rc, out, err, calls = run(["--switch", "cafe-wifi"],
                                  dev_status=self.CONNECTED)
        self.assertIn("pi5-w-1", err)

    def test_force_switches_anyway(self):
        rc, out, err, calls = run(["--switch", "cafe-wifi", "--force"],
                                  dev_status=self.CONNECTED,
                                  conn_names="cafe-wifi")
        self.assertEqual(rc, 0, err)
        self.assertTrue(any("connection up cafe-wifi" in c for c in calls), calls)

    def test_switching_when_idle_needs_no_force(self):
        rc, out, err, calls = run(["--switch", "cafe-wifi"],
                                  dev_status=self.IDLE,
                                  conn_names="cafe-wifi")
        self.assertEqual(rc, 0, err)
        self.assertTrue(any("connection up cafe-wifi" in c for c in calls), calls)

    def test_connect_is_the_same_as_switch(self):
        rc, out, err, calls = run(["--connect", "cafe-wifi"],
                                  dev_status=self.IDLE,
                                  conn_names="cafe-wifi")
        self.assertEqual(rc, 0, err)
        self.assertTrue(any("connection up cafe-wifi" in c for c in calls), calls)

    def test_switch_without_a_name_is_a_usage_error(self):
        rc, out, err, calls = run(["--switch"])
        self.assertEqual(rc, 2)
        self.assertEqual(calls, [])


class ReadOnlyActions(unittest.TestCase):

    def test_no_arguments_shows_status(self):
        rc, out, err, calls = run([])
        self.assertEqual(rc, 0, err)
        self.assertTrue(any("device show wlan0" in c for c in calls), calls)

    def test_status_changes_nothing(self):
        rc, out, err, calls = run(["--status"])
        joined = " | ".join(calls)
        for verb in ("connection add", "connection modify", "connection up"):
            self.assertNotIn(verb, joined)

    def test_list_shows_whether_a_profile_connects_on_its_own(self):
        # Priority alone does not answer the question people actually ask of
        # this list. A profile at priority 0 with autoconnect=no is never
        # considered at all, and reading only the priority column suggests the
        # opposite -- that it is merely ranked equal.
        rc, out, err, calls = run(["--list"])
        fields = [c for c in calls if "connection show" in c and "-f " in c]
        self.assertTrue(fields, calls)
        self.assertIn("NAME,TYPE,AUTOCONNECT,AUTOCONNECT-PRIORITY", fields[0])

    def test_list_changes_nothing(self):
        rc, out, err, calls = run(["--list"])
        joined = " | ".join(calls)
        for verb in ("connection add", "connection modify", "connection up"):
            self.assertNotIn(verb, joined)

    def test_help_touches_nmcli_at_all(self):
        rc, out, err, calls = run(["--help"])
        self.assertEqual(rc, 0)
        self.assertEqual(calls, [])
        self.assertIn("pi-net", out)   # the split between the two is documented

    def test_an_unknown_argument_is_refused_before_acting(self):
        rc, out, err, calls = run(["--wat"])
        self.assertEqual(rc, 2)
        self.assertEqual(calls, [])


class TheDiagnosisAfterAFailedSwitch(unittest.TestCase):
    """What pi-wifi says when `nmcli connection up` fails.

    It said "Is X a saved profile? (pi-wifi --list)" unconditionally. On
    2026-08-28 that was said about a profile sitting in the --list output two
    lines above, which sent the search in the wrong direction entirely. The
    real event was in the supplicant log, and it named a different access
    point: the one the deck was leaving had rejected reassociation, and the
    SSID being switched TO was temp-disabled as collateral.
    """

    # The actual lines from that failure, with the phone hotspot's BSSID.
    REAL = ("wlan0: CTRL-EVENT-ASSOC-REJECT bssid=e2:9a:a1:02:dc:98 status_code=16\n"
            "wlan0: CTRL-EVENT-SSID-TEMP-DISABLED id=0 ssid=\"yuiseki_redmi\" "
            "auth_failures=1 duration=10 reason=CONN_FAILED")

    def test_an_unknown_profile_is_still_told_to_look_at_the_list(self):
        rc, out, err, calls = run(["--switch", "nope", "--force"], rc=4)
        self.assertNotEqual(rc, 0)
        self.assertIn("--list", err)

    def test_a_known_profile_is_not_told_it_might_not_exist(self):
        rc, out, err, calls = run(["--switch", "yuiseki_redmi", "--force"],
                                  conn_names="yuiseki_redmi", rc=4)
        self.assertNotEqual(rc, 0)
        self.assertNotIn("saved profile", err)

    def test_a_known_profile_gets_the_radio_level_reason(self):
        rc, out, err, calls = run(["--switch", "yuiseki_redmi", "--force"],
                                  conn_names="yuiseki_redmi", rc=4,
                                  journal=self.REAL)
        self.assertIn("ASSOC-REJECT", err)

    def test_no_secrets_is_not_reported_as_a_wrong_password(self):
        # NetworkManager asks for new secrets when association times out, so
        # "Secrets were required" arrives for failures that have nothing to do
        # with the passphrase. Saying so is the whole point of this message.
        rc, out, err, calls = run(["--switch", "yuiseki_redmi", "--force"],
                                  conn_names="yuiseki_redmi", rc=4,
                                  journal=self.REAL)
        self.assertNotIn("wrong password", err.lower())
        self.assertIn("association", err.lower())

    def test_a_silent_journal_does_not_invent_a_reason(self):
        rc, out, err, calls = run(["--switch", "yuiseki_redmi", "--force"],
                                  conn_names="yuiseki_redmi", rc=4, journal="")
        self.assertNotIn("ASSOC-REJECT", err)
        self.assertNotEqual(rc, 0)

    def test_a_successful_switch_says_nothing_about_failures(self):
        rc, out, err, calls = run(["--switch", "yuiseki_redmi", "--force"],
                                  conn_names="yuiseki_redmi",
                                  journal=self.REAL)
        self.assertEqual(rc, 0, err)
        self.assertNotIn("ASSOC-REJECT", err)


class AutoconnectIsAPolicyOfItsOwn(unittest.TestCase):
    """--auto / --no-auto / --priority, and why they do not touch security.

    Profiles are added autoconnect=no so a network typed in from the field
    cannot outrank the cluster AP at the next boot. That is the right default
    and the wrong permanent state for a phone hotspot, which is the whole
    point of carrying the deck out of the house: away from the AP the radio
    would otherwise sit unconnected until somebody types --switch by hand.

    Priorities go NEGATIVE rather than raising the AP's. The AP profile is
    owned by netplan (/etc/netplan/90-NM-*.yaml), not by
    /etc/NetworkManager/system-connections, so leaving it untouched is worth a
    minus sign. Higher wins in NetworkManager, and the AP sits at 0.

    The hazard this also closes: `--ssid X` with no --pass used to run
    `connection modify X wifi-sec.key-mgmt none` on an existing profile, which
    silently strips WPA from a working network. Adding a flag people will want
    to combine with --ssid makes that far easier to trip over, so saying
    nothing about security now means nothing is done to it.
    """

    def test_auto_turns_autoconnect_on(self):
        rc, out, err, calls = run(["--ssid", "hotspot", "--auto"],
                                  conn_names="hotspot")
        self.assertEqual(rc, 0, err)
        mod = [c for c in calls if "connection modify" in c]
        self.assertTrue(mod, calls)
        self.assertIn("connection.autoconnect yes", " ".join(mod))

    def test_auto_ranks_below_the_home_ap(self):
        # The AP is at 0 and higher wins, so this has to be negative.
        rc, out, err, calls = run(["--ssid", "hotspot", "--auto"],
                                  conn_names="hotspot")
        mod = " ".join(c for c in calls if "connection modify" in c)
        self.assertRegex(mod, r"connection\.autoconnect-priority -\d+")

    def test_auto_does_not_touch_security(self):
        # No --pass was given, so nothing was said about the key.
        rc, out, err, calls = run(["--ssid", "hotspot", "--auto"],
                                  conn_names="hotspot")
        self.assertNotIn("wifi-sec", " ".join(calls))

    def test_no_auto_turns_it_off_again(self):
        rc, out, err, calls = run(["--ssid", "hotspot", "--no-auto"],
                                  conn_names="hotspot")
        self.assertEqual(rc, 0, err)
        mod = " ".join(c for c in calls if "connection modify" in c)
        self.assertIn("connection.autoconnect no", mod)

    def test_an_explicit_priority_wins(self):
        rc, out, err, calls = run(
            ["--ssid", "hotspot", "--auto", "--priority", "-30"],
            conn_names="hotspot")
        mod = " ".join(c for c in calls if "connection modify" in c)
        self.assertIn("connection.autoconnect-priority -30", mod)

    def test_a_new_profile_can_be_added_already_automatic(self):
        rc, out, err, calls = run(["--ssid", "hotspot", "--pass", "k", "--auto"])
        add = [c for c in calls if "connection add" in c]
        self.assertEqual(len(add), 1, calls)
        self.assertIn("connection.autoconnect yes", add[0])
        self.assertIn("wifi-sec.psk k", add[0])

    def test_a_new_profile_is_still_manual_by_default(self):
        rc, out, err, calls = run(["--ssid", "hotspot", "--pass", "k"])
        add = [c for c in calls if "connection add" in c][0]
        self.assertIn("connection.autoconnect no", add)

    def test_saying_nothing_about_an_existing_profile_changes_nothing(self):
        # This used to strip WPA from a working profile.
        rc, out, err, calls = run(["--ssid", "hotspot"], conn_names="hotspot")
        self.assertNotIn("wifi-sec", " ".join(calls))
        self.assertNotEqual(rc, 0)
        self.assertIn("--pass", err)

    def test_open_is_how_you_ask_for_no_key(self):
        rc, out, err, calls = run(["--ssid", "hotspot", "--open"],
                                  conn_names="hotspot")
        self.assertEqual(rc, 0, err)
        mod = " ".join(c for c in calls if "connection modify" in c)
        self.assertIn("wifi-sec.key-mgmt none", mod)

    def test_a_new_open_profile_still_needs_open(self):
        rc, out, err, calls = run(["--ssid", "hotspot", "--open"])
        add = [c for c in calls if "connection add" in c][0]
        self.assertNotIn("wifi-sec.psk", add)


if __name__ == "__main__":
    unittest.main()
