# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Adam Mercer
"""`self setup` and `self smoketest` against tests/fake_qemu.py, which
records the calls and runs nothing in a guest."""
import contextlib
import functools
import io
import json
import os
import shlex
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from test_end_to_end import link_stand_ins, stop_stand_ins

from kelder import mise, provision, qemu, settings, smoketest


class Provision(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp = Path(tmp.name).resolve()
        for d in ("home/proj", "bin", "run"):
            (self.tmp / d).mkdir(parents=True)
        link_stand_ins(self.tmp / "bin")
        (self.tmp / "image.qcow2").touch()
        self.trace = self.tmp / "trace"
        for patch in (
            mock.patch.multiple(
                settings, VM="fake", HOME=str(self.tmp / "home"), DRY_RUN=False,
                CLAUDE_STATE=str(self.tmp / "state"), GUEST_CLAUDE_STATE="/var/lib/kelder"),
            mock.patch.dict(os.environ, {
                "PATH": f"{self.tmp}/bin:{os.environ['PATH']}",
                "QEMU_TRACE": str(self.trace), "FAKE_QEMU_NOEXEC": "1",
                "XDG_RUNTIME_DIR": str(self.tmp / "run"), "XDG_DATA_HOME": str(self.tmp / "data"),
                "XDG_CACHE_HOME": str(self.tmp / "cache")}),
            mock.patch.object(provision, "host_checks"),
            mock.patch.object(provision, "host_timezone", return_value="Europe/London"),
            mock.patch.object(qemu, "pull", return_value=self.tmp / "image.qcow2"),   # no download
        ):
            patch.start()
            self.addCleanup(patch.stop)
        os.environ.pop("VM", None)
        self.addCleanup(stop_stand_ins, self.tmp)   # setup leaves the machine running

    def machine_dir(self, name="fake"):
        return self.tmp / "data/kelder/machines" / name

    def calls(self, name=None):
        """The stand-ins' calls so far; with a name, the arguments of that program's calls."""
        entries = [json.loads(line) for line in self.trace.read_text().splitlines()]
        return entries if name is None else [e[1:] for e in entries if e[0] == name]

    def run_quietly(self, fn):
        out = io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(out):
            fn()
        return out.getvalue()

    def guest_commands(self):
        """What was run in the guest over ssh, each as its list of arguments."""
        return [shlex.split(call[-1]) for call in self.calls("ssh")]

    def test_setup_creates_and_provisions_a_new_machine(self):
        out = self.run_quietly(provision.run)
        self.assertIn("Creating machine 'fake'...", out)
        self.assertIn("Setup done.", out)
        self.assertEqual(oct((self.tmp / "state").stat().st_mode & 0o777), "0o700")
        for name in ("disk.qcow2", "id_ed25519", "user-data", "meta-data"):
            self.assertTrue((self.machine_dir() / name).exists(), name)
        self.assertEqual(oct(self.machine_dir().stat().st_mode & 0o777), "0o700")
        (resize,) = self.calls("qemu-img")
        self.assertEqual(resize[0], "resize")
        (machine,) = self.calls("qemu-system-x86_64")
        ports = [a for a in machine if a.startswith("pcie-root-port,")]
        self.assertEqual(len(ports), qemu.SLOTS + 1)   # nothing said how many directories
        shared = [a for call in self.calls("virtiofsd") for a in call if a.startswith("--shared-dir=")]
        self.assertEqual(shared, [f"--shared-dir={self.tmp}/state"])
        commands = self.guest_commands()
        apt = next(c for c in commands if c[2:3] == [provision.APT])
        self.assertEqual(apt[:2], ["bash", "-c"])   # the fake guest is root: no sudo
        self.assertEqual(apt[4:], provision.PACKAGES)
        self.assertIn("mise", provision.PACKAGES)
        self.assertLess(commands.index(["bash", "-c", mise.ENABLE_REPO]), commands.index(apt))
        self.assertNotIn(mise.INSTALL, [c[2] for c in commands if len(c) > 2])   # no project given
        self.assertIn(["bash", "-c", provision.UV], commands)
        self.assertIn(["bash", "-c", provision.UPDATE_CLAUDE], commands)
        self.assertIn(["bash", "-c", provision.WRITE_STAMP, "_", provision.STAMP], commands)
        self.assertLess(commands.index(["bash", "-c", provision.UV]),
                        commands.index(["bash", "-c", provision.WRITE_STAMP, "_", provision.STAMP]))
        written = {c[5]: c[6] for c in commands if c[2:3] == [provision.WRITE_FILE]}
        self.assertEqual(written, {
            "/usr/local/bin/testvm": "755",
            "/usr/local/bin/smoketest": "755",
        })
        self.assertIn(["bash", "-c", provision.SET_TIMEZONE, "_", "Europe/London"], commands)
        self.assertIn(["git", "config", "--global", "--replace-all", "safe.directory", "*"], commands)
        # podman's packages are in before the user's bus is started, and that before podman runs.
        bus = commands.index(["bash", "-c", provision.START_USER_BUS])
        self.assertLess(commands.index(apt), bus)
        self.assertLess(bus, commands.index(["podman", "info"]))

    def test_setup_installs_a_projects_extra_packages(self):
        out = self.run_quietly(lambda: provision.run(["ansible-lint", "ldap-utils"]))
        self.assertIn("Extra packages for this project: ansible-lint ldap-utils", out)
        apt = next(c for c in self.guest_commands() if c[2:3] == [provision.APT])
        self.assertEqual(apt[4:], [*provision.PACKAGES, "ansible-lint", "ldap-utils"])

    def test_setup_installs_the_tools_a_projects_mise_config_lists(self):
        proj = str(self.tmp / "home/proj")
        out = self.run_quietly(lambda: provision.run(project=proj))
        self.assertLess(out.index(f"Installing the tools listed in {proj}'s mise config..."),
                        out.index("Setup done."))
        commands = self.guest_commands()
        install = commands.index(["bash", "-c", mise.INSTALL, "_", proj])
        attach = next(i for i, c in enumerate(commands) if c[-1] == proj and "mount -t virtiofs" in c[-3])
        detach = next(i for i, c in enumerate(commands) if c[-2:] == ["umount", proj])
        self.assertLess(attach, install)
        self.assertLess(install, detach)

    def test_setup_reuses_an_existing_machine(self):
        self.machine_dir().mkdir(parents=True)
        (self.machine_dir() / "disk.qcow2").touch()
        out = self.run_quietly(provision.run)
        self.assertIn("Machine 'fake' exists; reusing it.", out)
        self.assertNotIn("Creating machine", out)
        self.assertEqual(self.calls("qemu-img"), [])
        self.assertFalse((self.machine_dir() / "id_ed25519").exists())

    def test_smoketest_refuses_the_main_machine(self):
        with mock.patch.dict(os.environ, {"VM": "kelder"}), self.assertRaises(SystemExit):
            self.run_quietly(smoketest.run)
        self.assertFalse(self.trace.exists())

    def test_smoketest_takes_no_arguments(self):
        with self.assertRaises(SystemExit):
            self.run_quietly(functools.partial(smoketest.run, ["--bogus"]))
        self.assertFalse(self.trace.exists())

    def test_smoketest_uses_a_throwaway_machine_and_deletes_it(self):
        proj = str(self.tmp / "home/proj")
        with _chdir(proj):
            out = self.run_quietly(smoketest.run)
        self.assertEqual(settings.VM, "kelder-test")
        (machine,) = self.calls("qemu-system-x86_64")
        self.assertEqual(machine[machine.index("-name") + 1], "kelder-kelder-test")
        commands = self.guest_commands()
        attach = next(i for i, c in enumerate(commands) if c[-1] == proj and "mount -t virtiofs" in c[-3])
        self.assertLess(attach, commands.index(["bash", "-lc", 'smoketest "$1"', "_", proj]))
        self.assertIn("All checks passed; deleted machine 'kelder-test'.", out)
        self.assertEqual(commands[-1][-1], "poweroff")
        self.assertFalse(self.machine_dir("kelder-test").exists())
        self.assertFalse(any((self.tmp / "run").glob("kelder/*/qemu/qemu.pid")))


class UpdateIfStale(unittest.TestCase):
    """The weekly update, with the guest replaced by canned answers."""

    def setUp(self):
        self.stamp = ""        # what the guest's stamp file holds
        self.update_rc = 0
        self.uv_rc = 0
        self.ran = []          # the update steps the guest was asked to run
        for patch in (
            mock.patch.multiple(settings, VM="fake", DRY_RUN=False),
            mock.patch.object(provision, "guest", side_effect=self.guest),
            mock.patch.dict(os.environ),
        ):
            patch.start()
            self.addCleanup(patch.stop)
        os.environ.pop("NO_UPDATE", None)

    def guest(self, *cmd, capture=None):
        if cmd[:2] == ("id", "-u"):
            return 0, "0\n"
        if cmd[2] == provision.READ_STAMP:
            return (0, self.stamp + "\n") if self.stamp else (1, "")
        step = {mise.ENABLE_REPO: "repo", provision.APT: "apt", provision.UV: "uv",
                provision.UPDATE_CLAUDE: "claude", provision.WRITE_STAMP: "stamp"}[cmd[2]]
        self.ran.append(step)
        if step == "apt":
            self.apt_packages = list(cmd[4:])
            return self.update_rc, "line one\nE: something broke\n"
        if step == "uv":
            return self.uv_rc, "error: uv could not update\n"
        return 0, ""

    def run_it(self):
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            provision.update_if_stale()
        return err.getvalue()

    def days_ago(self, days):
        self.stamp = str(int(time.time() - days * 86400))

    def test_recent_update_is_left_alone(self):
        self.days_ago(6.9)
        self.assertEqual(self.run_it(), "")
        self.assertEqual(self.ran, [])

    def test_stale_machine_is_updated(self):
        self.days_ago(9.5)
        self.assertIn("updating machine 'fake' (last updated 9 days ago)...", self.run_it())
        self.assertEqual(self.ran, ["repo", "apt", "uv", "claude", "stamp"])
        self.assertEqual(self.apt_packages, provision.PACKAGES)   # never a project's extras

    def test_machine_with_no_record_is_updated(self):
        self.assertIn("(no record of an update)", self.run_it())
        self.assertEqual(self.ran, ["repo", "apt", "uv", "claude", "stamp"])

    def test_failed_update_is_reported_and_does_not_stop_the_session(self):
        self.update_rc = 100
        err = self.run_it()
        self.assertIn("update failed (exit 100); starting the session anyway", err)
        self.assertIn("E: something broke", err)
        self.assertEqual(self.ran, ["repo", "apt"])

    def test_failed_uv_update_leaves_the_time_unrecorded(self):
        self.uv_rc = 2
        err = self.run_it()
        self.assertIn("update failed (exit 2)", err)
        self.assertIn("uv could not update", err)
        self.assertEqual(self.ran, ["repo", "apt", "uv"])

    def test_no_update_and_dry_run_skip_it(self):
        with mock.patch.dict(os.environ, {"NO_UPDATE": "1"}):
            self.assertEqual(self.run_it(), "")
        with mock.patch.object(settings, "DRY_RUN", True):
            self.assertEqual(self.run_it(), "")
        self.assertEqual(self.ran, [])


@contextlib.contextmanager
def _chdir(path):
    old = os.getcwd()
    os.chdir(path)
    try:
        yield
    finally:
        os.chdir(old)


if __name__ == "__main__":
    unittest.main()
