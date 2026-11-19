# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Adam Mercer
"""Run kelder against tests/fake_qemu.py, which stands in for QEMU,
virtiofsd and ssh, and check what it starts, plugs in and runs, and the
environment claude would get. Needs no QEMU and no network: the guest side
runs locally with a fake claude."""
import json
import os
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
# The programs tests/fake_qemu.py stands in for.
STAND_INS = ("qemu-system-x86_64", "qemu-img", "virtiofsd", "genisoimage", "ssh", "ssh-keygen",
             "sudo", "mount", "umount", "rmdir", "fuser", "poweroff")


def link_stand_ins(directory):
    """Put the stand-ins in directory, to go at the front of PATH."""
    for name in STAND_INS:
        (directory / name).symlink_to(ROOT / "tests/fake_qemu.py")


def stop_stand_ins(tmp):
    """End any stand-in QEMU or virtiofsd still running below tmp."""
    for pidfile in tmp.glob("run/kelder/*/qemu/qemu.pid"):
        try:
            os.kill(int(pidfile.read_text()), signal.SIGTERM)
        except (OSError, ValueError):
            pass
    subprocess.run(["pkill", "-f", "--", f"--socket-path={tmp}/"], check=False)


# Reports what it was given. `-p hold FILE` stays up until FILE exists, to
# keep a session open.
FAKE_CLAUDE = """#!/usr/bin/env bash
echo "argv: $*"
echo "cwd=$PWD"
echo "CLAUDE_CONFIG_DIR=${CLAUDE_CONFIG_DIR-unset}"
echo "ANTHROPIC_API_KEY=${ANTHROPIC_API_KEY+set}${ANTHROPIC_API_KEY-unset}"
echo "ANTHROPIC_AUTH_TOKEN=${ANTHROPIC_AUTH_TOKEN+set}${ANTHROPIC_AUTH_TOKEN-unset}"
env | grep '^GIT_CONFIG_' | sort
echo "UV_PROJECT_ENVIRONMENT=${UV_PROJECT_ENVIRONMENT-unset}"
echo "UV_LINK_MODE=${UV_LINK_MODE-unset}"
echo "PATH=$PATH"
env | grep '^MISE_' | sort
if [[ ${2-} == hold ]]; then until [[ -e $3 ]]; do sleep 0.05; done; fi
"""


class EndToEnd(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp = Path(tmp.name).resolve()
        for d in ("bin", "home", "run", "data/kelder/machines", "state", "proj", "other", "extra"):
            (self.tmp / d).mkdir(parents=True)
        self.make_machine("fake")
        link_stand_ins(self.tmp / "bin")
        for name, text in (("claude", FAKE_CLAUDE), ("mountpoint", "#!/bin/sh\nexit 0\n")):
            (self.tmp / "bin" / name).write_text(text)
            (self.tmp / "bin" / name).chmod(0o755)
        self.trace = self.tmp / "trace"
        self.trace.touch()
        self.env = {
            "PATH": f"{self.tmp}/bin:{os.environ['PATH']}",
            "PYTHONPATH": str(ROOT / "src"),
            "HOME": str(self.tmp / "home"),
            "XDG_RUNTIME_DIR": str(self.tmp / "run"),
            "XDG_DATA_HOME": str(self.tmp / "data"),
            "VM": "fake",
            "NO_UPDATE": "1",   # the stand-in would run the update on this machine
            "QEMU_TRACE": str(self.trace),
            # The guest side runs locally, so its state path is the host one.
            "CLAUDE_STATE": str(self.tmp / "state"),
            "GUEST_CLAUDE_STATE": str(self.tmp / "state"),
            # Must never reach claude.
            "ANTHROPIC_API_KEY": "fake-api-key",
            "ANTHROPIC_AUTH_TOKEN": "fake-bearer",
        }
        self.addCleanup(stop_stand_ins, self.tmp)

    def make_machine(self, name):
        """What makes kelder take the machine to exist: its disk."""
        (self.tmp / "data/kelder/machines" / name).mkdir()
        (self.tmp / "data/kelder/machines" / name / "disk.qcow2").touch()

    def command(self, *args):
        return [sys.executable, "-m", "kelder", *args]

    def run_sc(self, *args, cwd="proj", **env):
        p = subprocess.run(self.command(*args), cwd=self.tmp / cwd, env={**self.env, **env},
                           check=False, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                           stderr=subprocess.STDOUT, text=True)
        return p.returncode, p.stdout

    def calls(self, name=None):
        """The stand-ins' calls so far; with a name, the arguments of that program's calls."""
        entries = [json.loads(line) for line in self.trace.read_text().splitlines()]
        return entries if name is None else [e[1:] for e in entries if e[0] == name]

    def guest_commands(self):
        """What was run in the guest over ssh, one command to a line."""
        return "\n".join(call[-1] for call in self.calls("ssh"))

    def session_started(self):
        return "exec claude" in self.guest_commands()

    def machine_running(self):
        return any((self.tmp / "run").glob("kelder/*/qemu/qemu.pid"))

    def test_claude_environment_and_arguments(self):
        rc, out = self.run_sc("self", "-a", str(self.tmp / "extra"), "--", "-p", "hi")
        self.assertEqual(rc, 0, out)
        self.assertIn(f"CLAUDE_CONFIG_DIR={self.tmp}/state", out)
        self.assertIn("ANTHROPIC_API_KEY=unset", out)
        self.assertIn("ANTHROPIC_AUTH_TOKEN=unset", out)
        self.assertIn("UV_PROJECT_ENVIRONMENT=.venv-fake", out)
        self.assertIn("UV_LINK_MODE=copy", out)
        self.assertIn(f"PATH={self.tmp}/home/.local/share/mise/shims:{self.tmp}/home/.local/bin:", out)
        self.assertIn("MISE_AUTO_INSTALL=false", out)
        self.assertIn("MISE_NOT_FOUND_AUTO_INSTALL=false", out)
        self.assertIn(f"MISE_TRUSTED_CONFIG_PATHS={self.tmp}/proj:{self.tmp}/extra", out)
        self.assertIn(f"argv: --add-dir={self.tmp}/extra -p hi", out)
        self.assertIn(f"cwd={self.tmp}/proj", out)

    def test_git_uses_a_gitignore_in_the_state_directory(self):
        (self.tmp / "state" / "gitignore").write_text(".claude/settings.local.json\n")
        rc, out = self.run_sc("hello")
        self.assertEqual(rc, 0, out)
        self.assertIn("GIT_CONFIG_COUNT=1", out.splitlines())
        self.assertIn("GIT_CONFIG_KEY_0=core.excludesFile", out.splitlines())
        self.assertIn(f"GIT_CONFIG_VALUE_0={self.tmp}/state/gitignore", out.splitlines())

    def test_a_projects_memory_folder_is_given_to_claude(self):
        (self.tmp / "proj/.kelder.toml").write_text('memory = "work"\n')
        rc, out = self.run_sc("hello")
        self.assertEqual(rc, 0, out)
        settings = json.dumps({"autoMemoryDirectory": f"{self.tmp}/state/memory/work"})
        self.assertIn(f"argv: --settings={settings} hello", out)

    def test_without_a_memory_key_claude_gets_no_settings(self):
        rc, out = self.run_sc("hello")
        self.assertEqual(rc, 0, out)
        self.assertIn("argv: hello", out.splitlines())

    def test_settings_of_your_own_are_refused_with_a_memory_folder(self):
        (self.tmp / "proj/.kelder.toml").write_text('memory = "work"\n')
        for args in (("--settings", "{}"), ("--settings={}", "hello")):
            rc, out = self.run_sc(*args)
            self.assertEqual(rc, 1, out)
            self.assertIn("--settings cannot be used with memory in "
                          f"{self.tmp}/proj/.kelder.toml", out)
        self.assertEqual(self.calls(), [])

    def test_a_session_boots_the_machine_attaches_runs_claude_and_stops_it(self):
        rc, out = self.run_sc("self", "-a", str(self.tmp / "extra"), "--", "-p", "hi")
        self.assertEqual(rc, 0, out)
        (qemu,) = self.calls("qemu-system-x86_64")
        ports = [a for a in qemu if a.startswith("pcie-root-port,")]
        self.assertEqual(len(ports), 3)   # the two directories and the state directory
        shared = [a for call in self.calls("virtiofsd") for a in call if a.startswith("--shared-dir=")]
        self.assertEqual(shared, [f"--shared-dir={self.tmp}/{d}" for d in ("state", "proj", "extra")])
        qmp = self.calls("qmp")
        self.assertEqual(qmp[:6], [
            ["chardev-add", "ch0"], ["device_add", "dev0"],   # the state directory
            ["chardev-add", "ch1"], ["device_add", "dev1"],
            ["chardev-add", "ch2"], ["device_add", "dev2"],
        ])
        # After claude has exited, in either order.
        self.assertCountEqual([qmp[6:8], qmp[8:]], [
            [["device_del", "dev1"], ["chardev-remove", "ch1"]],
            [["device_del", "dev2"], ["chardev-remove", "ch2"]],
        ])
        self.assertIn(["-t", "virtiofs", "fs1", f"{self.tmp}/proj"], self.calls("mount"))
        self.assertCountEqual(self.calls("umount"), [[f"{self.tmp}/proj"], [f"{self.tmp}/extra"]])
        # The mount point is removed from the guest once its directory is detached.
        self.assertIn(["-p", "--ignore-fail-on-non-empty", f"{self.tmp}/proj"], self.calls("rmdir"))
        self.assertEqual(self.calls()[-1], ["poweroff"])
        self.assertFalse(self.machine_running())
        self.assertTrue((self.tmp / "proj").is_dir())

    def test_a_session_for_one_directory_gets_one_port_and_no_tty_here(self):
        self.run_sc("hello")
        (qemu,) = self.calls("qemu-system-x86_64")
        ports = [a for a in qemu if a.startswith("pcie-root-port,")]
        self.assertEqual(len(ports), 2)   # the one directory and the state directory
        session = [call for call in self.calls("ssh") if "exec claude" in call[-1]]
        self.assertEqual(len(session), 1)
        self.assertNotIn("-t", session[0])   # the tests have no terminal

    def test_shell_gets_the_session_without_claude(self):
        (self.tmp / "bin/claude").unlink()
        (self.tmp / "extra/.kelder.toml").write_text('memory = "shared"\n')
        (self.tmp / "state" / "gitignore").write_text(".venv-*\n")
        script = 'echo "cwd=$PWD"; echo "PATH=$PATH"; echo "UV=$UV_PROJECT_ENVIRONMENT"\n' \
                 'echo "CONFIG=$CLAUDE_CONFIG_DIR"; echo "KEY=${ANTHROPIC_API_KEY-unset}"\n' \
                 'env | grep "^GIT_CONFIG_VALUE_0="\n'
        p = subprocess.run(self.command("self", "shell", "-C", str(self.tmp / "extra")),
                           cwd=self.tmp / "proj", env=self.env, check=False, input=script,
                           stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
        self.assertEqual(p.returncode, 0, p.stdout)
        self.assertIn(f"cwd={self.tmp}/extra", p.stdout)
        self.assertIn(f"PATH={self.tmp}/home/.local/share/mise/shims:{self.tmp}/home/.local/bin:",
                      p.stdout)
        self.assertIn("UV=.venv-fake", p.stdout)
        self.assertIn(f"CONFIG={self.tmp}/state", p.stdout)
        self.assertIn("KEY=unset", p.stdout)
        self.assertIn(f"GIT_CONFIG_VALUE_0={self.tmp}/state/gitignore", p.stdout)
        self.assertNotIn("resume with:", p.stdout)
        self.assertNotIn("autoMemoryDirectory", self.guest_commands())
        self.assertIn(["-t", "virtiofs", "fs1", f"{self.tmp}/extra"], self.calls("mount"))
        self.assertFalse(self.machine_running())

    def test_exit_status_is_claudes(self):
        (self.tmp / "bin/claude").write_text("#!/bin/sh\nexit 7\n")
        self.assertEqual(self.run_sc("hello")[0], 7)
        self.assertFalse(self.machine_running())

    def test_dry_run_prints_the_commands_and_runs_none(self):
        rc, out = self.run_sc("hello", DRY_RUN="1")
        self.assertEqual(rc, 0, out)
        self.assertEqual(self.calls(), [])
        for command in ("+ qemu-system-x86_64 -name kelder-fake", "/virtiofsd --socket-path=",
                        "+ qmp chardev-add", "+ ssh -i "):
            self.assertIn(command, out)
        self.assertNotIn("argv:", out)

    def test_missing_machine(self):
        (self.tmp / "data/kelder/machines/fake/disk.qcow2").unlink()
        rc, out = self.run_sc("hello")
        self.assertEqual(rc, 1)
        self.assertIn("machine 'fake' does not exist; run 'kelder self setup' first", out)
        self.assertEqual(self.calls(), [])

    def test_project_file_names_the_machine_unless_vm_is_set(self):
        self.make_machine("mine")
        (self.tmp / "proj/.kelder.toml").write_text('vm = "mine"\n')
        rc, out = self.run_sc("hello", VM="")
        self.assertEqual(rc, 0, out)
        (qemu,) = self.calls("qemu-system-x86_64")
        self.assertEqual(qemu[qemu.index("-name") + 1], "kelder-mine")
        self.trace.write_text("")
        rc, out = self.run_sc("hello")
        self.assertEqual(rc, 0, out)
        (qemu,) = self.calls("qemu-system-x86_64")
        self.assertEqual(qemu[qemu.index("-name") + 1], "kelder-fake")

    def test_missing_project_packages_are_reported_not_installed(self):
        (self.tmp / "proj/.kelder.toml").write_text('packages = ["have", "lack"]\n')
        (self.tmp / "bin/dpkg-query").write_text(
            '#!/bin/sh\nfor p; do :; done\n[ "$p" = have ] && printf installed\n')
        (self.tmp / "bin/dpkg-query").chmod(0o755)
        rc, out = self.run_sc("hello")
        self.assertEqual(rc, 0, out)
        self.assertIn(f"machine 'fake' lacks packages listed in {self.tmp}/proj/.kelder.toml: lack; "
                      "run 'kelder self setup' to install them", out)
        self.assertNotIn("apt-get", self.guest_commands())

    def test_missing_mise_tools_are_reported_not_installed(self):
        (self.tmp / "proj/mise.toml").write_text('[tools]\njq = "1.7.1"\nzig = "0.15"\n')
        (self.tmp / "bin/mise").write_text(
            '#!/bin/sh\necho "$PWD $MISE_TRUSTED_CONFIG_PATHS $*" >"$HOME/mise-call"\n'
            f'echo "zig  0.15 (missing)  {self.tmp}/proj/mise.toml  0.15"\n')
        (self.tmp / "bin/mise").chmod(0o755)
        rc, out = self.run_sc("hello")
        self.assertEqual(rc, 0, out)
        self.assertIn(f"machine 'fake' lacks tools listed in {self.tmp}/proj's mise config: zig@0.15; "
                      "run 'kelder self setup' to install them", out)
        self.assertEqual((self.tmp / "home/mise-call").read_text(),
                         f"{self.tmp}/proj {self.tmp}/proj ls --missing --current --no-header\n")

    def test_a_project_without_a_mise_config_is_not_checked(self):
        rc, out = self.run_sc("hello")
        self.assertEqual(rc, 0, out)
        self.assertNotIn("mise ls", self.guest_commands())

    def test_invalid_project_file_stops_the_session(self):
        (self.tmp / "proj/.kelder.toml").write_text('vm = "-x"\n')
        rc, out = self.run_sc("hello")
        self.assertEqual(rc, 1)
        self.assertIn("vm must be a machine name", out)
        self.assertEqual(self.calls(), [])

    def hold_a_session(self, release):
        """Start a session that stays up until the file release exists."""
        first = subprocess.Popen(self.command("-p", "hold", str(release)), cwd=self.tmp / "proj",
                                 env=self.env, stdin=subprocess.DEVNULL,
                                 stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        self.addCleanup(first.kill)
        deadline = time.monotonic() + 20
        while not self.session_started():
            self.assertLess(time.monotonic(), deadline, "the session never started")
            time.sleep(0.05)
        return first

    def test_concurrent_sessions(self):
        release = self.tmp / "release"
        first = self.hold_a_session(release)

        rc, out = self.run_sc("hello", cwd="other")
        self.assertEqual(rc, 1)
        self.assertIn(f"another kelder session is using {self.tmp}/proj", out)
        self.assertNotIn("argv:", out)

        rc, out = self.run_sc("hello")
        self.assertEqual(rc, 0, out)
        self.assertIn("argv: hello", out)
        self.assertEqual(len(self.calls("qemu-system-x86_64")), 1)   # the same machine
        self.assertNotIn(["device_del", "dev1"], self.calls("qmp"))
        self.assertTrue(self.machine_running())

        release.touch()
        self.assertEqual(first.wait(timeout=20), 0)
        self.assertIn(["device_del", "dev1"], self.calls("qmp"))
        self.assertFalse(self.machine_running())

    def test_sigterm_still_cleans_up(self):
        first = self.hold_a_session(self.tmp / "never")
        first.terminate()
        self.assertEqual(first.wait(timeout=20), 143)
        self.assertEqual(self.calls()[-1], ["poweroff"])
        self.assertFalse(self.machine_running())
        (self.tmp / "never").touch()   # lets the orphaned fake claude finish


if __name__ == "__main__":
    unittest.main()
