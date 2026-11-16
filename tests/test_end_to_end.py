# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Adam Mercer
"""Run kelder against tests/fake_smolvm.py and check the smolvm calls it
makes and the environment claude would get. Needs no smolvm and no network:
the guest side runs locally with a fake claude."""
import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

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
if [[ ${2-} == hold ]]; then until [[ -e $3 ]]; do sleep 0.05; done; fi
"""


class EndToEnd(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp = Path(tmp.name).resolve()
        for d in ("bin", "home", "run", "state", "proj", "other", "extra"):
            (self.tmp / d).mkdir()
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
            "VM": "fake",
            "NO_UPDATE": "1",   # the fake would run the update on this machine
            "SMOLVM_BIN": str(ROOT / "tests/fake_smolvm.py"),
            "SMOLVM_TRACE": str(self.trace),
            # The guest side runs locally, so its state path is the host one.
            "CLAUDE_STATE": str(self.tmp / "state"),
            "GUEST_CLAUDE_STATE": str(self.tmp / "state"),
            # Must never reach claude.
            "ANTHROPIC_API_KEY": "fake-api-key",
            "ANTHROPIC_AUTH_TOKEN": "fake-bearer",
        }

    def command(self, *args):
        return [sys.executable, "-m", "kelder", *args]

    def run_sc(self, *args, cwd="proj", **env):
        p = subprocess.run(self.command(*args), cwd=self.tmp / cwd, env={**self.env, **env},
                           check=False, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                           stderr=subprocess.STDOUT, text=True)
        return p.returncode, p.stdout

    def calls(self):
        """The smolvm calls so far, one per line, arguments joined by spaces."""
        return "\n".join(" ".join(json.loads(line)) for line in self.trace.read_text().splitlines())

    def test_claude_environment_and_arguments(self):
        rc, out = self.run_sc("self", "-a", str(self.tmp / "extra"), "--", "-p", "hi")
        self.assertEqual(rc, 0, out)
        self.assertIn(f"CLAUDE_CONFIG_DIR={self.tmp}/state", out)
        self.assertIn("ANTHROPIC_API_KEY=unset", out)
        self.assertIn("ANTHROPIC_AUTH_TOKEN=unset", out)
        self.assertIn("UV_PROJECT_ENVIRONMENT=.venv-smol", out)
        self.assertIn("UV_LINK_MODE=copy", out)
        self.assertIn(f"argv: --add-dir={self.tmp}/extra -p hi", out)
        self.assertIn(f"cwd={self.tmp}/proj", out)

    def test_git_treats_the_attached_directories_as_safe(self):
        rc, out = self.run_sc("self", "-a", str(self.tmp / "extra"), "--", "hello")
        self.assertEqual(rc, 0, out)
        expected = [f"{self.tmp}/proj", f"{self.tmp}/proj/*", f"{self.tmp}/extra", f"{self.tmp}/extra/*"]
        self.assertIn(f"GIT_CONFIG_COUNT={len(expected)}", out.splitlines())
        for i, value in enumerate(expected):
            self.assertIn(f"GIT_CONFIG_KEY_{i}=safe.directory", out.splitlines())
            self.assertIn(f"GIT_CONFIG_VALUE_{i}={value}", out.splitlines())

    def test_directories_are_attached_then_detached_and_the_machine_stopped(self):
        self.run_sc("hello")
        calls = self.calls()
        self.assertIn(f"machine mount add --name fake -v {self.tmp}/proj:{self.tmp}/proj", calls)
        self.assertIn(f"machine mount rm --name fake -t {self.tmp}/proj", calls)
        self.assertTrue(calls.rstrip().endswith("machine stop --name fake"), calls)

    def test_exit_status_is_claudes(self):
        (self.tmp / "bin/claude").write_text("#!/bin/sh\nexit 7\n")
        self.assertEqual(self.run_sc("hello")[0], 7)

    def test_dry_run_prints_the_calls_and_runs_none(self):
        rc, out = self.run_sc("hello", DRY_RUN="1")
        self.assertEqual(rc, 0, out)
        self.assertEqual(self.calls(), "")
        for call in ("status --name fake", "start --name fake", "exec --name fake -i -- bash -c",
                     "stop --name fake"):
            self.assertIn(f"+ smolvm machine {call}", out)
        self.assertNotIn("argv:", out)

    def test_missing_machine(self):
        rc, out = self.run_sc("hello", FAKE_SMOLVM_FRESH="1")
        self.assertEqual(rc, 1)
        self.assertIn("machine 'fake' does not exist; run 'kelder self setup' first", out)

    def test_concurrent_sessions(self):
        release = self.tmp / "release"
        first = subprocess.Popen(self.command("-p", "hold", str(release)), cwd=self.tmp / "proj",
                                 env=self.env, stdin=subprocess.DEVNULL,
                                 stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        self.addCleanup(first.kill)
        deadline = time.monotonic() + 20
        while "-p hold" not in self.calls():
            self.assertLess(time.monotonic(), deadline, "the first session never started")
            time.sleep(0.05)

        rc, out = self.run_sc("hello", cwd="other")
        self.assertEqual(rc, 1)
        self.assertIn(f"another kelder session is using {self.tmp}/proj", out)
        self.assertNotIn("argv:", out)

        rc, out = self.run_sc("hello")
        self.assertEqual(rc, 0, out)
        self.assertIn("argv: hello", out)
        self.assertNotIn("mount rm", self.calls())
        self.assertNotIn("machine stop", self.calls())

        release.touch()
        self.assertEqual(first.wait(timeout=20), 0)
        self.assertIn(f"machine mount rm --name fake -t {self.tmp}/proj", self.calls())
        self.assertIn("machine stop --name fake", self.calls())

    def test_sigterm_still_cleans_up(self):
        first = subprocess.Popen(self.command("-p", "hold", str(self.tmp / "never")),
                                 cwd=self.tmp / "proj", env=self.env, stdin=subprocess.DEVNULL,
                                 stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        self.addCleanup(first.kill)
        deadline = time.monotonic() + 20
        while "-p hold" not in self.calls():
            self.assertLess(time.monotonic(), deadline, "the session never started")
            time.sleep(0.05)
        first.terminate()
        self.assertEqual(first.wait(timeout=20), 143)
        self.assertIn("machine stop --name fake", self.calls())
        (self.tmp / "never").touch()   # lets the orphaned fake claude finish


if __name__ == "__main__":
    unittest.main()
