# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Adam Mercer
"""Argument parsing and the checks on directories to attach."""
import contextlib
import io
import os
import re
import tempfile
import time
import unittest
from unittest import mock

from smol_claude import cli, smolvm


def dies(test, fn, *args):
    """Call fn expecting it to die; returns the message."""
    err = io.StringIO()
    with contextlib.redirect_stderr(err), test.assertRaises(SystemExit) as caught:
        fn(*args)
    test.assertEqual(caught.exception.code, 1)
    return err.getvalue()


class ParseArgs(unittest.TestCase):
    def test_everything_goes_to_claude_without_self(self):
        self.assertEqual(cli.parse_args([]), ("run", ".", [], []))
        self.assertEqual(cli.parse_args(["-p", "hi"]), ("run", ".", [], ["-p", "hi"]))
        self.assertEqual(cli.parse_args(["-C", "x"]), ("run", ".", [], ["-C", "x"]))

    def test_leading_double_dash_is_dropped(self):
        self.assertEqual(cli.parse_args(["--", "self", "x"]), ("run", ".", [], ["self", "x"]))

    def test_self_options(self):
        self.assertEqual(cli.parse_args(["self", "-C", "d", "-a", "e", "-a", "f", "-p", "hi"]),
                         ("run", "d", ["e", "f"], ["-p", "hi"]))
        self.assertEqual(cli.parse_args(["self", "-a", "e", "-C", "d"]), ("run", "d", ["e"], []))

    def test_self_options_end_at_double_dash(self):
        self.assertEqual(cli.parse_args(["self", "-a", "e", "--", "-C", "x"]),
                         ("run", ".", ["e"], ["-C", "x"]))

    def test_commands(self):
        for command in ("help", "setup", "smoketest", "version"):
            self.assertEqual(cli.parse_args(["self", command])[0], command)

    def test_errors(self):
        self.assertIn("unknown command or option 'self bogus'", dies(self, cli.parse_args, ["self", "bogus"]))
        self.assertIn("unknown command or option 'self '", dies(self, cli.parse_args, ["self"]))
        self.assertIn("-C needs a directory", dies(self, cli.parse_args, ["self", "-C"]))
        self.assertIn("-a needs a directory", dies(self, cli.parse_args, ["self", "-a"]))


class CheckDir(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp = os.path.realpath(tmp.name)
        for d in ("home/proj", "state/sub", "with:colon"):
            os.makedirs(os.path.join(self.tmp, d))
        patch = mock.patch.multiple(smolvm, HOME=self.path("home"), CLAUDE_STATE=self.path("state"))
        patch.start()
        self.addCleanup(patch.stop)

    def path(self, name):
        return os.path.join(self.tmp, name)

    def test_returns_the_real_path(self):
        os.symlink(self.path("home/proj"), self.path("link"))
        self.assertEqual(cli.check_dir(self.path("link")), self.path("home/proj"))

    def test_refusals(self):
        self.assertIn("not a directory", dies(self, cli.check_dir, self.path("missing")))
        self.assertIn("won't attach /;", dies(self, cli.check_dir, "/"))
        self.assertIn("won't attach", dies(self, cli.check_dir, self.path("home")))
        self.assertIn("containing ':'", dies(self, cli.check_dir, self.path("with:colon")))

    def test_refuses_anything_overlapping_the_state_directory(self):
        for d in ("state", "state/sub", ""):
            self.assertIn("overlaps Claude's state directory", dies(self, cli.check_dir, self.path(d)))



class ResumeCommand(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp = os.path.realpath(tmp.name)
        self.proj = os.path.join(self.tmp, "my.proj")
        os.makedirs(self.proj)
        self.folder = os.path.join(self.tmp, "state/projects", re.sub(r"[^A-Za-z0-9]", "-", self.proj))
        os.makedirs(self.folder)
        patch = mock.patch.multiple(smolvm, VM="smol", CLAUDE_STATE=os.path.join(self.tmp, "state"))
        patch.start()
        self.addCleanup(patch.stop)
        old = os.getcwd()
        self.addCleanup(os.chdir, old)
        os.chdir(self.proj)
        self.now = time.time()

    def transcript(self, session, age):
        path = os.path.join(self.folder, session + ".jsonl")
        open(path, "w").close()
        os.utime(path, (self.now - age, self.now - age))

    def test_nothing_written_during_the_session(self):
        self.transcript("old", age=100)
        self.assertIsNone(cli.resume_command([self.proj], self.now - 10))
        self.assertIsNone(cli.resume_command(["/no/such/project"], self.now - 10))

    def test_newest_transcript_from_the_session(self):
        self.transcript("old", age=100)
        self.transcript("earlier", age=5)
        self.transcript("latest", age=1)
        self.assertEqual(cli.resume_command([self.proj], self.now - 10), "smol-claude --resume latest")

    def test_other_machine_and_directories(self):
        self.transcript("abc", age=1)
        os.chdir(self.tmp)
        with mock.patch.object(smolvm, "VM", "smol-test"):
            self.assertEqual(cli.resume_command([self.proj, "/x y"], self.now - 10),
                             f"VM=smol-test smol-claude self -C {self.proj} -a '/x y' -- --resume abc")


if __name__ == "__main__":
    unittest.main()
