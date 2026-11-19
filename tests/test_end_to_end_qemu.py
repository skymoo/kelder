# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Adam Mercer
"""Run kelder with the QEMU backend against tests/fake_qemu.py, which
stands in for QEMU, virtiofsd and ssh, and check what it starts, plugs in
and runs. Needs no QEMU and no network: the guest side runs locally with a
fake claude."""
import json
import os
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

from test_end_to_end import FAKE_CLAUDE

ROOT = Path(__file__).resolve().parent.parent
STAND_INS = ("qemu-system-x86_64", "qemu-img", "virtiofsd", "genisoimage", "ssh", "ssh-keygen",
             "sudo", "mount", "umount", "rmdir", "fuser", "poweroff")


class QemuEndToEnd(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp = Path(tmp.name).resolve()
        for d in ("bin", "home", "run", "data/kelder/machines/fake", "state", "proj", "other", "extra"):
            (self.tmp / d).mkdir(parents=True)
        (self.tmp / "data/kelder/machines/fake/disk.qcow2").touch()   # the machine exists
        for name in STAND_INS:
            (self.tmp / "bin" / name).symlink_to(ROOT / "tests/fake_qemu.py")
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
            "BACKEND": "qemu",
            "NO_UPDATE": "1",   # the stand-in would run the update on this machine
            "QEMU_TRACE": str(self.trace),
            # The guest side runs locally, so its state path is the host one.
            "CLAUDE_STATE": str(self.tmp / "state"),
            "GUEST_CLAUDE_STATE": str(self.tmp / "state"),
        }
        self.addCleanup(self.stop_stand_ins)

    def stop_stand_ins(self):
        """End a stand-in QEMU or virtiofsd a failed test left running."""
        for entry in self.calls():
            if entry[0] == "qemu-system-x86_64":
                pidfile = Path(entry[entry.index("-pidfile") + 1])
                if pidfile.exists():
                    try:
                        os.kill(int(pidfile.read_text()), signal.SIGTERM)
                    except (OSError, ValueError):
                        pass
        subprocess.run(["pkill", "-f", "--", f"--socket-path={self.tmp}/"], check=False)

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

    def qemu_running(self):
        return any((self.tmp / "run").glob("kelder/*/qemu/qemu.pid"))

    def test_a_session_boots_the_machine_attaches_runs_claude_and_stops_it(self):
        rc, out = self.run_sc("self", "-a", str(self.tmp / "extra"), "--", "-p", "hi")
        self.assertEqual(rc, 0, out)
        self.assertIn(f"argv: --add-dir={self.tmp}/extra -p hi", out)
        self.assertIn(f"cwd={self.tmp}/proj", out)
        self.assertIn(f"CLAUDE_CONFIG_DIR={self.tmp}/state", out)
        self.assertIn("UV_PROJECT_ENVIRONMENT=.venv-fake", out)

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
        self.assertEqual(self.calls()[-1], ["poweroff"])
        self.assertFalse(self.qemu_running())
        self.assertTrue((self.tmp / "proj").is_dir())

    def test_the_session_runs_over_ssh_without_a_tty_here(self):
        self.run_sc("hello")
        session = [call for call in self.calls("ssh") if "exec claude" in call[-1]]
        self.assertEqual(len(session), 1)
        self.assertNotIn("-t", session[0])   # the tests have no terminal
        (qemu,) = self.calls("qemu-system-x86_64")
        ports = [a for a in qemu if a.startswith("pcie-root-port,")]
        self.assertEqual(len(ports), 2)   # the one directory and the state directory

    def test_exit_status_is_claudes(self):
        (self.tmp / "bin/claude").write_text("#!/bin/sh\nexit 7\n")
        self.assertEqual(self.run_sc("hello")[0], 7)
        self.assertFalse(self.qemu_running())

    def test_a_machine_qemu_made_needs_no_BACKEND(self):
        env = dict(self.env)
        del env["BACKEND"]
        p = subprocess.run(self.command("hello"), cwd=self.tmp / "proj", env=env, check=False,
                           stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                           text=True)
        self.assertEqual(p.returncode, 0, p.stdout)
        self.assertIn("argv: hello", p.stdout)
        self.assertEqual(len(self.calls("qemu-system-x86_64")), 1)

    def test_missing_machine(self):
        (self.tmp / "data/kelder/machines/fake/disk.qcow2").unlink()
        rc, out = self.run_sc("hello")
        self.assertEqual(rc, 1)
        self.assertIn("machine 'fake' does not exist; run 'kelder self setup' first", out)
        self.assertEqual(self.calls(), [])

    def test_dry_run_prints_the_commands_and_runs_none(self):
        rc, out = self.run_sc("hello", DRY_RUN="1")
        self.assertEqual(rc, 0, out)
        self.assertEqual(self.calls(), [])
        for command in ("+ qemu-system-x86_64 -name kelder-fake", "/virtiofsd --socket-path=",
                        "+ qmp chardev-add", "+ ssh -i "):
            self.assertIn(command, out)
        self.assertNotIn("argv:", out)

    def test_concurrent_sessions(self):
        release = self.tmp / "release"
        first = subprocess.Popen(self.command("-p", "hold", str(release)), cwd=self.tmp / "proj",
                                 env=self.env, stdin=subprocess.DEVNULL,
                                 stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        self.addCleanup(first.kill)
        deadline = time.monotonic() + 20
        while not any("exec claude" in call[-1] for call in self.calls("ssh")):
            self.assertLess(time.monotonic(), deadline, "the first session never started")
            time.sleep(0.05)

        rc, out = self.run_sc("hello", cwd="other")
        self.assertEqual(rc, 1)
        self.assertIn(f"another kelder session is using {self.tmp}/proj", out)
        self.assertNotIn("argv:", out)

        rc, out = self.run_sc("hello")
        self.assertEqual(rc, 0, out)
        self.assertIn("argv: hello", out)
        self.assertEqual(len(self.calls("qemu-system-x86_64")), 1)   # the same machine
        self.assertNotIn(["device_del", "dev1"], self.calls("qmp"))
        self.assertTrue(self.qemu_running())

        release.touch()
        self.assertEqual(first.wait(timeout=20), 0)
        self.assertIn(["device_del", "dev1"], self.calls("qmp"))
        self.assertFalse(self.qemu_running())


if __name__ == "__main__":
    unittest.main()
