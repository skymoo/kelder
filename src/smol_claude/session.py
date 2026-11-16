# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Adam Mercer
"""Which directories are attached to the machine, and by which sessions.

Each attached directory has <key>.path (its path) and <key>.lock, which every
session using it holds a shared lock on. A lock nobody holds means the
sessions are gone and the directory can be detached. .lock serializes all of
this between concurrent smol-claude runs.
"""
import fcntl
import hashlib
import os
from pathlib import Path

from . import smolvm
from .smolvm import die, note

BUSY = "only one set of directories can be attached at a time (use VM=<name> for another machine)"


def state_dir():
    base = smolvm.env("XDG_RUNTIME_DIR", os.path.join(smolvm.HOME, ".cache"))
    return Path(base) / "smol-claude" / smolvm.VM


def _open(path):
    return os.open(path, os.O_WRONLY | os.O_CREAT, 0o666)


class Session:
    """One smol-claude run: dirs is every directory it attaches, primary first.

    vm is what attaches and detaches (the smolvm module; a stand-in in tests).
    on_boot, if given, is called when this session is the one that boots the
    machine, before anything is attached and while other sessions wait.
    """

    def __init__(self, dirs, state=None, vm=smolvm, on_boot=None):
        self.dirs = dirs
        self.vm = vm
        self.on_boot = on_boot
        self.state = Path(state) if state else state_dir()
        self.state.mkdir(parents=True, exist_ok=True)
        self.glock = _open(self.state / ".lock")
        self.mylocks = []

    def _key(self, d):
        return self.state / hashlib.sha256(d.encode()).hexdigest()[:16]

    def sweep(self, only_free=False, fatal=True):
        """Detach directories no session is using.

        With only_free, stop there; otherwise also fail unless the directories
        still in use are exactly this session's set (mounts are machine-wide,
        so any other set would let one session see another's projects).
        A failed detach is fatal, or just reported when fatal is false.
        """
        used = []
        for path_file in sorted(self.state.glob("*.path")):
            lock_file = path_file.with_suffix(".lock")
            other = path_file.read_text()
            fd = _open(lock_file)
            try:
                try:
                    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                except OSError:
                    if not only_free:
                        used.append(other)
                        if other not in self.dirs:
                            die(f"another smol-claude session is using {other}; {BUSY}")
                    continue
                if self.vm.attached(other) and not self.vm.detach(other):
                    if fatal:
                        die(f"could not detach {other}")
                    note(f"could not detach {other}")
                    continue
                path_file.unlink()
                lock_file.unlink()
            finally:
                os.close(fd)
        if used and any(d not in used for d in self.dirs):
            die(f"another smol-claude session is using {' '.join(used)}; {BUSY}")

    def attach(self):
        """Start the machine, attach this session's directories and lock them."""
        fcntl.flock(self.glock, fcntl.LOCK_EX)
        try:
            # Start the machine under the lock, so an exiting session can't stop
            # it between here and the attach.
            if self.vm.start() and self.on_boot:
                self.on_boot()
            self.sweep()
            for d in self.dirs:
                if not self.vm.attached(d) and not self.vm.attach(d):
                    die(f"could not attach {d}")
            for d in self.dirs:
                key = self._key(d)
                key.with_suffix(".path").write_text(d)
                fd = _open(key.with_suffix(".lock"))
                fcntl.flock(fd, fcntl.LOCK_SH)
                self.mylocks.append(fd)
        finally:
            fcntl.flock(self.glock, fcntl.LOCK_UN)

    def cleanup(self):
        """Release this session's directories; stop the machine if it was the last."""
        for fd in self.mylocks:
            os.close(fd)
        self.mylocks = []
        fcntl.flock(self.glock, fcntl.LOCK_EX)
        try:
            self.sweep(only_free=True, fatal=False)
            if not any(self.state.glob("*.path")):
                note(f"stopping machine '{smolvm.VM}'...")
                if not self.vm.stop():
                    note(f"could not stop machine '{smolvm.VM}'")
        finally:
            fcntl.flock(self.glock, fcntl.LOCK_UN)
