# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Adam Mercer
"""Settings and the smolvm CLI.

Everything that depends on how the smolvm CLI behaves lives here. Lock files
are never inherited by the processes started here: Python opens files
close-on-exec.
"""
import os
import shlex
import shutil
import subprocess
import sys
import time

PROG = "kelder"
HOME = os.path.expanduser("~")


def env(name, default):
    """An environment variable, with empty treated as unset."""
    return os.environ.get(name) or default


# The smolvm machine. `self smoketest` changes it to its throwaway machine.
VM = env("VM", "kelder")
# Claude's state (login, settings, history, memory) lives on the host so the
# machine can be thrown away and rebuilt. It is mounted into the machine at
# boot, and sessions point CLAUDE_CONFIG_DIR at it.
CLAUDE_STATE = env(
    "CLAUDE_STATE",
    os.path.join(env("XDG_DATA_HOME", os.path.join(HOME, ".local/share")), "kelder/claude"),
)
# Overridable so the tests can run the guest side locally; nothing else should set it.
GUEST_CLAUDE_STATE = env("GUEST_CLAUDE_STATE", "/var/lib/kelder")
# The smolvm binary; the tests point it at tests/fake_smolvm.py.
SMOLVM_BIN = env("SMOLVM_BIN", "smolvm")
# DRY_RUN=1 prints every smolvm call and runs none.
DRY_RUN = os.environ.get("DRY_RUN") == "1"


def note(msg):
    print(f"{PROG}: {msg}", file=sys.stderr)


def die(msg):
    note(msg)
    sys.exit(1)


def require():
    """Fail unless the smolvm binary is on PATH."""
    if not DRY_RUN and shutil.which(SMOLVM_BIN) is None:
        die(f"{SMOLVM_BIN} not found on PATH")


def run(*args, capture=None):
    """Run smolvm; returns (exit status, captured output).

    capture="all" collects stdout and stderr together, "out" collects stdout
    and leaves stderr on the terminal, "drop" discards stdout.
    """
    if DRY_RUN:
        print("+ smolvm " + shlex.join(args), file=sys.stderr)
        return 0, ""
    stdout = {None: None, "drop": subprocess.DEVNULL}.get(capture, subprocess.PIPE)
    stderr = subprocess.STDOUT if capture == "all" else None
    p = subprocess.run([SMOLVM_BIN, *args], check=False, stdout=stdout, stderr=stderr, text=True)
    return p.returncode, p.stdout or ""


def guest(*cmd, capture=None):
    """Run a command in the guest without a TTY; returns (exit status, output)."""
    return run("machine", "exec", "--name", VM, "--", *cmd, capture=capture)


def guest_ok(*cmd):
    """True if the command succeeds in the guest; its output is discarded."""
    return guest(*(cmd or ("true",)), capture="all")[0] == 0


def exists():
    """True if the machine exists, running or not."""
    # `machine status` exits 0 for a running or stopped machine and fails for
    # an unknown one (checked against the smolvm v1.34.4 source).
    return run("machine", "status", "--name", VM, capture="all")[0] == 0


def running():
    return "': running" in run("machine", "status", "--name", VM, capture="all")[1]


def start():
    """Start the machine if needed, then wait until it accepts exec.

    Returns True if this call booted it, False if it was already running.
    """
    booted = not running()
    if booted:
        note(f"starting machine '{VM}'...")
    rc, out = run("machine", "start", "--name", VM, capture="all")
    # Starting a running machine succeeds; a paused one fails. Accept a failure
    # as long as the machine answers, otherwise show smolvm's error.
    if rc != 0 and not guest_ok():
        die(f"could not start machine '{VM}':\n{out}")
    for _ in range(30):
        if guest_ok():
            return booted
        time.sleep(1)
    die(f"machine '{VM}' started but did not answer exec within 30s")


def stop():
    return run("machine", "stop", "--name", VM, capture="drop")[0] == 0


# Host directories are attached to the running machine at the same path they
# have on the host (`machine mount`, smolvm 1.32+). Attachments last until the
# machine stops.

def attached(d):
    rc, out = run("machine", "mount", "ls", "--name", VM, capture="out")
    return rc == 0 and f"{d} -> {d}" in out.splitlines()


def attach(d):
    return run("machine", "mount", "add", "--name", VM, "-v", f"{d}:{d}", capture="drop")[0] == 0


def detach(d):
    return run("machine", "mount", "rm", "--name", VM, "-t", d, capture="drop")[0] == 0
