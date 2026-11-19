# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Adam Mercer
"""The machine sessions run in.

The rest of kelder reaches the machine through these functions; what they
do is up to the backend the machine is on: smolvm, or QEMU for a machine
made with BACKEND=qemu (see qemu.py).
"""
from types import ModuleType

from . import qemu, settings, smolvm
from .settings import Capture, die


def backend() -> ModuleType:
    """The module that runs the machine.

    BACKEND names it. Without that, a machine made by the QEMU backend is a
    QEMU machine and every other name is smolvm's.
    """
    name = settings.env("BACKEND", "")
    if name not in ("", "qemu", "smolvm"):
        die(f"unknown BACKEND '{name}'; it is qemu or smolvm")
    if name == "qemu" or (not name and qemu.exists()):
        return qemu
    return smolvm


def require() -> None:
    """Fail unless the backend's programs are on PATH."""
    backend().require()


def host_checks() -> None:
    """Fail unless the host can run the backend's machines."""
    backend().host_checks()


def exists() -> bool:
    """True if the machine exists, running or not."""
    return backend().exists()


def ensure(state: str, guest_state: str) -> None:
    """Create the machine if there is none and leave it running, with the
    host directory state mounted at guest_state."""
    backend().ensure(state, guest_state)


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


def guest_files() -> list[tuple[str, str, str]]:
    """Files from guest/ only this backend's machines need: (name, path in the guest, mode)."""
    return backend().guest_files()


def smoketest_host() -> int:
    """Check, from the host, the backend behaviour kelder relies on; prints a
    line for each check and returns how many failed."""
    return backend().smoketest_host()
