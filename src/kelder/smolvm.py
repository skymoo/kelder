# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Adam Mercer
"""The smolvm CLI.

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

from . import settings
from .settings import Capture, die, note, warn

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


def host_checks() -> None:
    if run("machine", "mount", "--help", capture="all")[0] != 0:
        die("smolvm 1.32 or newer is needed (kelder uses 'smolvm machine mount')")


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


def ensure(state: str, guest_state: str) -> None:
    """Create the machine if there is none and leave it running, with the
    host directory state mounted at guest_state.

    IMAGE names the image for a new machine (default: debian).
    """
    vm = settings.VM
    state_mount = f"{state}:{guest_state}"
    if exists():
        print(f"Machine '{vm}' exists; reusing it.")
        if "IMAGE" in os.environ:
            warn("IMAGE is ignored for an existing machine; delete it and re-run to change images")
    elif run("machine", "create", "--name", vm, "--nested", "--net",
             "--image", settings.env("IMAGE", "debian"), "-v", state_mount)[0] != 0:
        die(f"could not create machine '{vm}'")
    start()

    # Machines made before the state mount existed get it added. Mounts can only
    # change while the machine is stopped, so this restarts it once.
    if not guest_ok("mountpoint", "-q", guest_state):
        print(f"Adding the Claude state mount; restarting machine '{vm}' "
              "(this ends any kelder sessions in it)...")
        if run("machine", "stop", "--name", vm)[0] != 0:
            die(f"could not stop machine '{vm}'")
        if run("machine", "update", "--name", vm, "-v", state_mount)[0] != 0:
            die(f"could not add the state mount to machine '{vm}'")
        start()
        if not guest_ok("mountpoint", "-q", guest_state):
            die(f"state directory still not mounted at {guest_state} after restart")

    # Machines made by older versions mount a project at /work on every boot,
    # which Claude would see alongside whatever kelder attaches.
    if guest_ok("mountpoint", "-q", "/work") and not settings.DRY_RUN:
        warn(f"machine '{vm}' still mounts a directory at /work. Remove it with:")
        print(f"  smolvm machine update --name {vm} --remove-volume <host-dir>:/work"
              "   (then restart the machine)", file=sys.stderr)


def running() -> bool:
    return "': running" in run("machine", "status", "--name", settings.VM, capture="all")[1]


def start(directories: int = 0) -> bool:
    """Start the machine if needed, then wait until it accepts exec.

    Returns True if this call booted it, False if it was already running.
    How many directories will be attached makes no difference to smolvm.
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


def delete() -> bool:
    """Delete the machine, running or not."""
    return run("machine", "delete", "--name", settings.VM, "-f")[0] == 0


def delete_command() -> str:
    """The command that deletes the machine, to show the user."""
    return f"smolvm machine delete --name {settings.VM}"


def session(tty: bool, cmd: list[str]) -> int:
    """Run a command in the guest on this terminal; returns its exit status."""
    args = ["machine", "exec", "--name", settings.VM, "-i", *(["-t"] if tty else []), "--", *cmd]
    if settings.DRY_RUN:
        return run(*args)[0]
    return subprocess.call([SMOLVM_BIN, *args])


def guest_files() -> list[tuple[str, str, str]]:
    """Files from guest/ only smolvm machines need: (name, path in the guest, mode)."""
    return [("containers-tsi.conf", "/etc/containers/containers.conf.d/50-smolvm-tsi.conf", "644")]


def smoketest_host() -> int:
    """Check the CLI behaviour kelder relies on; prints a line for each
    check and returns how many failed."""
    fails = 0
    if run("machine", "status", "--name", f"{settings.VM}-nosuch", capture="all")[0] == 0 \
            and not settings.DRY_RUN:
        print("FAIL     host: machine status succeeds for an unknown machine")
        fails += 1
    else:
        print("PASS     host: machine status fails for an unknown machine")
    if run("machine", "start", "--name", settings.VM, capture="all")[0] == 0:
        print("PASS     host: machine start succeeds on a running machine")
    else:
        print("FAIL     host: machine start fails on a running machine")
        fails += 1
    return fails


# Host directories are attached to the running machine at the same path they
# have on the host (`machine mount`, smolvm 1.32+). Attachments last until the
# machine stops.

def attached(d: str) -> bool:
    rc, out = run("machine", "mount", "ls", "--name", settings.VM, capture="out")
    return rc == 0 and f"{d} -> {d}" in out.splitlines()


def make_room(directories: int) -> bool:
    """smolvm attaches any number of directories to a running machine."""
    return True


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
