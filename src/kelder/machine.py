# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Adam Mercer
"""The machine sessions run in.

The rest of kelder reaches the machine through these functions; what they
do is up to the backend, and QEMU is the only one (see qemu.py).
"""
from types import ModuleType

from . import qemu
from .settings import Capture


def backend() -> ModuleType:
    """The module that runs the machine."""
    return qemu


def require() -> None:
    """Fail unless the backend's programs are on PATH."""
    backend().require()


def host_checks() -> None:
    """Fail unless the host can run the backend's machines."""
    backend().host_checks()


def exists() -> bool:
    """True if the machine exists, running or not."""
    return backend().exists()


def ensure(state: str, guest_state: str) -> bool:
    """Create the machine if there is none and leave it running, with the
    host directory state mounted at guest_state. Returns True if this call
    booted it, False if it was already running."""
    return backend().ensure(state, guest_state)


def running() -> bool:
    return backend().running()


def start(directories: int = 0) -> bool:
    """Start the machine if needed; True if this call booted it.

    directories is how many the caller is about to attach.
    """
    return backend().start(directories)


def stop() -> bool:
    return backend().stop()


def delete() -> bool:
    """Delete the machine, running or not."""
    return backend().delete()


def delete_command() -> str:
    """The command that deletes the machine, to show the user."""
    return backend().delete_command()


def attached(d: str) -> bool:
    return backend().attached(d)


def make_room(directories: int) -> bool:
    """Make sure that many more directories can be attached, restarting the
    machine if the backend has to. Only for when no session has a
    directory attached."""
    return backend().make_room(directories)


def attach(d: str) -> bool:
    return backend().attach(d)


def detach(d: str) -> bool:
    return backend().detach(d)


def guest(*cmd: str, capture: Capture = None) -> tuple[int, str]:
    """Run a command in the guest without a TTY; returns (exit status, output)."""
    return backend().guest(*cmd, capture=capture)


def guest_ok(*cmd: str) -> bool:
    """True if the command succeeds in the guest; its output is discarded."""
    return backend().guest_ok(*cmd)


def session(tty: bool, cmd: list[str]) -> int:
    """Run a command in the guest on this terminal; returns its exit status."""
    return backend().session(tty, cmd)
