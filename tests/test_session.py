# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Adam Mercer
"""The attachment bookkeeping, with a stand-in for the machine."""
import contextlib
import io
import os
import tempfile
import unittest

from kelder.session import Session


class FakeVm:
    def __init__(self):
        self.mounted = set()
        self.calls = []
        self.detach_fails = False
        self.boots = False   # whether start() finds the machine stopped
        self.room = []       # what make_room() was asked for
        self.has_room = True

    def start(self, directories=0):
        self.calls.append("start")
        self.started_for = directories
        return self.boots

    def stop(self):
        self.calls.append("stop")
        return True

    def attached(self, d):
        return d in self.mounted

    def make_room(self, directories):
        self.room.append(directories)
        return self.has_room

    def attach(self, d):
        self.calls.append(f"attach {d}")
        self.mounted.add(d)
        return True

    def detach(self, d):
        self.calls.append(f"detach {d}")
        if self.detach_fails:
            return False
        self.mounted.discard(d)
        return True


class SessionTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.state = tmp.name
        self.vm = FakeVm()
        self.err = io.StringIO()
        redirect = contextlib.redirect_stderr(self.err)
        redirect.__enter__()
        self.addCleanup(redirect.__exit__, None, None, None)

    def session(self, *dirs):
        return Session(list(dirs), state=self.state, vm=self.vm)

    def paths(self, session):
        return sorted(p.read_text() for p in session.state.glob("*.path"))

    def dies(self, fn):
        with self.assertRaises(SystemExit):
            fn()
        return self.err.getvalue()

    def test_attach_then_cleanup(self):
        s = self.session("/a", "/b")
        s.attach()
        self.assertEqual(self.vm.calls, ["start", "attach /a", "attach /b"])
        self.assertEqual(self.paths(s), ["/a", "/b"])
        s.cleanup()
        self.assertEqual(self.vm.calls[3:], ["detach /a", "detach /b", "stop"])
        self.assertEqual(self.paths(s), [])
        self.assertEqual(self.vm.mounted, set())

    def test_on_boot_runs_only_for_the_session_that_boots_the_machine(self):
        hook = lambda: self.vm.calls.append("on_boot")
        self.vm.boots = True
        first = Session(["/a"], state=self.state, vm=self.vm, on_boot=hook)
        first.attach()
        self.assertEqual(self.vm.calls, ["start", "on_boot", "attach /a"])
        self.vm.boots = False
        Session(["/a"], state=self.state, vm=self.vm, on_boot=hook).attach()
        self.assertEqual(self.vm.calls.count("on_boot"), 1)

    def test_same_directories_share_the_machine(self):
        first, second = self.session("/a"), self.session("/a")
        first.attach()
        second.attach()
        self.assertEqual(self.vm.calls.count("attach /a"), 1)
        second.cleanup()
        self.assertNotIn("detach /a", self.vm.calls)
        self.assertNotIn("stop", self.vm.calls)
        first.cleanup()
        self.assertEqual(self.vm.calls[-2:], ["detach /a", "stop"])

    def test_a_different_directory_is_refused(self):
        self.session("/a").attach()
        self.assertIn("another kelder session is using /a", self.dies(self.session("/b").attach))
        self.assertEqual(self.vm.mounted, {"/a"})

    def test_a_larger_or_smaller_set_is_refused(self):
        self.session("/a", "/b").attach()
        self.assertIn("is using /b;", self.dies(self.session("/a").attach))
        self.assertIn("is using /b;", self.dies(self.session("/a", "/c").attach))
        self.assertIn("is using /a /b;", self.dies(self.session("/a", "/b", "/c").attach))
        self.assertEqual(self.vm.mounted, {"/a", "/b"})

    def test_a_crashed_session_is_swept_by_the_next(self):
        crashed = self.session("/a")
        crashed.attach()
        for fd in crashed.mylocks:   # the process died: its locks are gone
            os.close(fd)
        self.session("/b").attach()
        self.assertEqual(self.vm.mounted, {"/b"})
        self.assertIn("detach /a", self.vm.calls)

    def test_a_failed_detach_at_exit_keeps_the_record_and_the_machine(self):
        s = self.session("/a")
        s.attach()
        self.vm.detach_fails = True
        s.cleanup()
        self.assertIn("could not detach /a", self.err.getvalue())
        self.assertEqual(self.paths(s), ["/a"])
        self.assertNotIn("stop", self.vm.calls)

    def test_room_is_asked_for_the_directories_not_yet_attached(self):
        first = self.session("/a", "/b")
        first.attach()
        self.assertEqual(self.vm.started_for, 2)
        self.assertEqual(self.vm.room, [2])
        self.session("/a", "/b").attach()   # the same set: nothing is missing
        self.assertEqual(self.vm.room, [2])

    def test_no_room_is_fatal_before_anything_is_attached(self):
        self.vm.has_room = False
        with self.assertRaises(SystemExit):
            self.session("/a", "/b").attach()
        self.assertIn("could not make room", self.err.getvalue())
        self.assertNotIn("attach /a", self.vm.calls)

    def test_a_failed_detach_at_start_is_fatal(self):
        crashed = self.session("/a")
        crashed.attach()
        for fd in crashed.mylocks:
            os.close(fd)
        self.vm.detach_fails = True
        self.assertIn("could not detach /a", self.dies(self.session("/b").attach))


if __name__ == "__main__":
    unittest.main()
