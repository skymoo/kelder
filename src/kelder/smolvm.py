# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Adam Mercer
"""The smolvm CLI.

Everything that depends on how the smolvm CLI behaves lives here. Lock files
are never inherited by the processes started here: Python opens files
close-on-exec.
"""
import shlex
import shutil
import subprocess
import sys
import time

from . import settings
from .settings import Capture, die, note

# The smolvm binary; the tests point it at tests/fake_smolvm.py.
SMOLVM_BIN = settings.env("SMOLVM_BIN", "smolvm")


def require() -> None:
    """Fail unless the smolvm binary is on PATH."""
    if not settings.DRY_RUN and shutil.which(SMOLVM_BIN) is None:
        die(f"{SMOLVM_BIN} not found on PATH")


def run(*args: str, capture: Capture = None) -> tuple[int, str]:
    """Run smolvm; returns (exit status, captured output).

    capture="all" collects stdout and stderr together, "out" collects stdout
    and leaves stderr on the terminal, "drop" discards stdout.
    """
    if settings.DRY_RUN:
        print("+ smolvm " + shlex.join(args), file=sys.stderr)
        return 0, ""
    stdout = {None: None, "drop": subprocess.DEVNULL}.get(capture, subprocess.PIPE)
    stderr = subprocess.STDOUT if capture == "all" else None
    p = subprocess.run([SMOLVM_BIN, *args], check=False, stdout=stdout, stderr=stderr, text=True)
    return p.returncode, p.stdout or ""


def guest(*cmd: str, capture: Capture = None) -> tuple[int, str]:
    """Run a command in the guest without a TTY; returns (exit status, output)."""
    # Without --stream, smolvm prints a command's output only once it has
    # finished (src/cli/machine.rs at v1.35.3), so a long step looks hung.
    # Output that is collected here anyway is left as it was.
    stream = ["--stream"] if capture is None else []
    return run("machine", "exec", "--name", settings.VM, *stream, "--", *cmd, capture=capture)


def guest_ok(*cmd: str) -> bool:
    """True if the command succeeds in the guest; its output is discarded."""
    return guest(*(cmd or ("true",)), capture="all")[0] == 0


def exists() -> bool:
    """True if the machine exists, running or not."""
    # `machine status` exits 0 for a running or stopped machine and fails for
    # an unknown one (checked against the smolvm v1.34.4 source).
    return run("machine", "status", "--name", settings.VM, capture="all")[0] == 0


def running() -> bool:
    return "': running" in run("machine", "status", "--name", settings.VM, capture="all")[1]


def start() -> bool:
    """Start the machine if needed, then wait until it accepts exec.

    Returns True if this call booted it, False if it was already running.
    """
    booted = not running()
    if booted:
        note(f"starting machine '{settings.VM}'...")
    rc, out = run("machine", "start", "--name", settings.VM, capture="all")
    # Starting a running machine succeeds; a paused one fails. Accept a failure
    # as long as the machine answers, otherwise show smolvm's error.
    if rc != 0 and not guest_ok():
        die(f"could not start machine '{settings.VM}':\n{out}")
    for _ in range(30):
        if guest_ok():
            return booted
        time.sleep(1)
    die(f"machine '{settings.VM}' started but did not answer exec within 30s")


def stop() -> bool:
    return run("machine", "stop", "--name", settings.VM, capture="drop")[0] == 0


# Host directories are attached to the running machine at the same path they
# have on the host (`machine mount`, smolvm 1.32+). Attachments last until the
# machine stops.

def attached(d: str) -> bool:
    rc, out = run("machine", "mount", "ls", "--name", settings.VM, capture="out")
    return rc == 0 and f"{d} -> {d}" in out.splitlines()


def attach(d: str) -> bool:
    return run("machine", "mount", "add", "--name", settings.VM, "-v", f"{d}:{d}", capture="drop")[0] == 0


def detach(d: str) -> bool:
    if run("machine", "mount", "rm", "--name", settings.VM, "-t", d, capture="drop")[0] != 0:
        return False
    # smolvm leaves the mount point on the machine's disk, where its name would
    # tell later sessions about a project they were not given. Remove it and
    # the parents it leaves empty; rmdir never removes a directory with content.
    guest("rmdir", "-p", "--ignore-fail-on-non-empty", d, capture="all")
    return True
