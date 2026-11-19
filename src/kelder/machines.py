# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Adam Mercer
"""Looking after the machines: `kelder self list`, `self stop` and `self delete`.

A machine a session is using is never stopped or deleted. Both hold the
machine's session lock while they work, so no session starts meanwhile.
Deleting asks first.
"""
import contextlib
import shutil
import sys
from collections.abc import Generator

from . import config, machine, qemu, session, settings
from .settings import die, note


@contextlib.contextmanager
def named(name: str) -> Generator[None, None, None]:
    """Make name the machine everything else acts on, for the length of the block."""
    if not config.VM_NAME.fullmatch(name):
        die(f"not a machine name: {name}")
    previous, settings.VM = settings.VM, name
    try:
        yield
    finally:
        settings.VM = previous


def size(nbytes: int) -> str:
    return f"{nbytes / (1 << 30):.1f} GiB"


def listing() -> list[tuple[str, str, str, str]]:
    """A row for each machine: name, state, the disk's size on the host, attached directories."""
    rows = []
    for name in qemu.names():
        with named(name):
            attached = [d for d in qemu.mounts() if d != settings.GUEST_CLAUDE_STATE]
            rows.append((name, "running" if qemu.running() else "stopped",
                         size(qemu.disk_usage()), " ".join(attached) or "-"))
    return rows


def show() -> None:
    rows = listing()
    if not rows:
        print("No machines; 'kelder self setup' creates one.")
        return
    rows.insert(0, ("NAME", "STATE", "DISK", "ATTACHED"))
    widths = [max(len(row[i]) for row in rows) for i in range(3)]
    for row in rows:
        print("  ".join([*(cell.ljust(width) for cell, width in zip(row, widths, strict=False)), row[3]]))


@contextlib.contextmanager
def idle(name: str) -> Generator[None, None, None]:
    """The machine name, existing and with no session using it, under its session lock."""
    with named(name), session.locked():
        if not machine.exists():
            die(f"machine '{name}' does not exist; see 'kelder self list'")
        used = session.used()
        if used:
            die(f"machine '{name}' is in use: a session has {' '.join(used)} attached")
        yield


def confirmed(question: str) -> bool:
    """Ask on the terminal; only y or yes is a yes, and no answer is a no."""
    try:
        return input(f"{question} [y/N] ").strip().lower() in ("y", "yes")
    except EOFError:
        print()
        return False


def stop(name: str) -> None:
    with idle(name):
        if not qemu.running():
            print(f"Machine '{name}' is not running.")
            return
        note(f"stopping machine '{name}'...")
        if not machine.stop():
            die(f"could not stop machine '{name}'")


def delete(name: str) -> None:
    with idle(name):
        state = "running" if qemu.running() else "stopped"
        if not confirmed(f"Delete machine '{name}' ({state}, {size(qemu.disk_usage())} on disk)?"):
            print(f"Machine '{name}' was not deleted.")
            sys.exit(1)
        if state == "running":
            note(f"stopping machine '{name}'...")
        if not machine.delete():
            die(f"could not delete machine '{name}'")
        # What sessions left about the directories they had attached.
        shutil.rmtree(session.state_dir(), ignore_errors=True)
        print(f"Deleted machine '{name}'.")


def run(command: str, args: list[str]) -> None:
    """Carry out `self list`, `self stop [NAME]` or `self delete NAME`."""
    if command == "list":
        if args:
            die("usage: kelder self list")
        show()
    elif command == "stop":
        if len(args) > 1:
            die("usage: kelder self stop [NAME]")
        stop(args[0] if args else settings.VM)
    else:
        if len(args) != 1:
            die("usage: kelder self delete NAME")
        delete(args[0])
