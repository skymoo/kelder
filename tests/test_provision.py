# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Adam Mercer
"""`self setup` and `self smoketest` against tests/fake_smolvm.py, which
records the calls and runs nothing in a guest."""
import contextlib
import functools
import io
import json
import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from kelder import mise, provision, release, smoketest, smolvm

ROOT = Path(__file__).resolve().parent.parent


class Provision(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp = Path(tmp.name).resolve()
        (self.tmp / "home/proj").mkdir(parents=True)
        self.trace = self.tmp / "trace"
        for patch in (
            mock.patch.multiple(
                smolvm, VM="fake", HOME=str(self.tmp / "home"), DRY_RUN=False,
                SMOLVM_BIN=str(ROOT / "tests/fake_smolvm.py"),
                CLAUDE_STATE=str(self.tmp / "state"), GUEST_CLAUDE_STATE="/var/lib/kelder"),
            mock.patch.dict(os.environ, {"SMOLVM_TRACE": str(self.trace),
                                         "FAKE_SMOLVM_NOEXEC": "1", "FAKE_SMOLVM_FRESH": "1",
                                         "XDG_RUNTIME_DIR": str(self.tmp)}),
            mock.patch.object(provision, "host_checks"),
            mock.patch.object(provision, "host_timezone", return_value="Europe/London"),
        ):
            patch.start()
            self.addCleanup(patch.stop)
        os.environ.pop("IMAGE", None)
        os.environ.pop("VM", None)

    def calls(self):
        return [json.loads(line) for line in self.trace.read_text().splitlines()]

    def run_quietly(self, fn):
        out = io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(out):
            fn()
        return out.getvalue()

    def guest_commands(self):
        return [c[c.index("--") + 1:] for c in self.calls() if c[:2] == ["machine", "exec"]]

    def test_setup_creates_and_provisions_a_new_machine(self):
        out = self.run_quietly(provision.run)
        self.assertIn("Setup done.", out)
        self.assertEqual(oct((self.tmp / "state").stat().st_mode & 0o777), "0o700")
        self.assertIn(["machine", "create", "--name", "fake", "--nested", "--net", "--image", "debian",
                       "-v", f"{self.tmp}/state:/var/lib/kelder"], self.calls())
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
            "/etc/containers/containers.conf.d/50-smolvm-tsi.conf": "644",
            "/usr/local/bin/testvm": "755",
            "/usr/local/bin/smoketest": "755",
        })
        self.assertIn(["bash", "-c", provision.SET_TIMEZONE, "_", "Europe/London"], commands)
        self.assertIn(["git", "config", "--global", "--replace-all", "safe.directory", "*"], commands)

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
        calls = self.calls()
        # --stream: its output is shown as it arrives, not when it ends
        install = ["machine", "exec", "--name", "fake", "--stream", "--",
                   "bash", "-c", mise.INSTALL, "_", proj]
        attach = calls.index(["machine", "mount", "add", "--name", "fake", "-v", f"{proj}:{proj}"])
        detach = calls.index(["machine", "mount", "rm", "--name", "fake", "-t", proj])
        self.assertLess(attach, calls.index(install))
        self.assertLess(calls.index(install), detach)

    def test_setup_reuses_an_existing_machine(self):
        with mock.patch.dict(os.environ, {"IMAGE": "ubuntu"}):
            os.environ.pop("FAKE_SMOLVM_FRESH")
            out = self.run_quietly(provision.run)
        self.assertIn("Machine 'fake' exists; reusing it.", out)
        self.assertIn("IMAGE is ignored for an existing machine", out)
        self.assertFalse(any(c[:2] == ["machine", "create"] for c in self.calls()))

    def test_smoketest_refuses_the_main_machine(self):
        with mock.patch.dict(os.environ, {"VM": "kelder"}), self.assertRaises(SystemExit):
            self.run_quietly(smoketest.run)
        self.assertFalse(self.trace.exists())

    def test_smoketest_refuses_arguments_it_does_not_know(self):
        for args in (["--bogus"], ["--release"], ["--release", "1.2.3", "extra"]):
            with self.assertRaises(SystemExit):
                self.run_quietly(functools.partial(smoketest.run, args))
        self.assertFalse(self.trace.exists())

    def test_smoketest_of_a_release_removes_its_directory(self):
        made = self.tmp / "release"

        def use(version):
            made.mkdir()
            return str(made)

        with _chdir(self.tmp / "home/proj"), mock.patch.object(release, "use", use):
            out = self.run_quietly(functools.partial(smoketest.run, ["--release", "1.2.3"]))
        self.assertIn("All checks passed", out)
        self.assertFalse(made.exists())

    def test_smoketest_uses_a_throwaway_machine_and_deletes_it(self):
        with _chdir(self.tmp / "home/proj"):
            out = self.run_quietly(smoketest.run)
        self.assertEqual(smolvm.VM, "kelder-test")
        self.assertIn(["machine", "mount", "add", "--name", "kelder-test", "-v",
                       f"{self.tmp}/home/proj:{self.tmp}/home/proj"], self.calls())
        self.assertIn(["bash", "-lc", 'smoketest "$1"', "_", f"{self.tmp}/home/proj"],
                      self.guest_commands())
        self.assertIn("PASS     host: machine status fails for an unknown machine", out)
        self.assertIn("PASS     host: machine start succeeds on a running machine", out)
        self.assertEqual(self.calls()[-1], ["machine", "delete", "--name", "kelder-test", "-f"])


class UpdateIfStale(unittest.TestCase):
    """The weekly update, with the guest replaced by canned answers."""

    def setUp(self):
        self.stamp = ""        # what the guest's stamp file holds
        self.update_rc = 0
        self.uv_rc = 0
        self.ran = []          # the update steps the guest was asked to run
        for patch in (
            mock.patch.multiple(smolvm, VM="fake", DRY_RUN=False),
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
        with mock.patch.object(smolvm, "DRY_RUN", True):
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
