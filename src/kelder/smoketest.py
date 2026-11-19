# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Adam Mercer
"""Test the host's QEMU, virtiofsd and ssh in a throwaway machine.

Run it as `kelder self smoketest` from a project directory. It creates
the machine, runs the guest's smoketest in it with the current directory
attached, and deletes the machine if everything passed. A machine that
failed is kept for inspection.

  VM=name kelder self smoketest   picks the machine (default: kelder-test)
"""
import sys

from . import machine, provision, settings
from .cli import check_dir
from .settings import die


def run(args: list[str] | None = None) -> None:
    if args:
        die("usage: kelder self smoketest")
    settings.VM = vm = settings.env("VM", "kelder-test")
    if vm == "kelder":
        die("refusing to test in the main machine 'kelder'")
    machine.require()
    if not settings.DRY_RUN and machine.exists():
        die(f"machine '{vm}' already exists; delete it first ({machine.delete_command()})")
    d = check_dir(".")

    provision.run()
    machine.start()
    if not machine.attach(d):
        die(f"could not attach {d}")

    print(f"Running smoketest in '{vm}'...")
    passed = machine.guest("bash", "-lc", 'smoketest "$1"', "_", d)[0] == 0

    print()
    if not passed:
        print(f"Kept machine '{vm}' for inspection; remove it with: {machine.delete_command()}",
              file=sys.stderr)
        sys.exit(1)
    if not machine.delete():
        die(f"could not delete machine '{vm}'")
    print(f"All checks passed; deleted machine '{vm}'.")
