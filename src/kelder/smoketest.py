# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Adam Mercer
"""Test the installed smolvm in a throwaway machine.

Run it as `kelder self smoketest` from a project directory. It creates
the machine, runs the guest's smoketest in it with the current directory
attached, checks the smolvm CLI behaviour kelder relies on, and deletes
the machine if everything passed. A machine that failed is kept for
inspection.

  VM=name kelder self smoketest   picks the machine (default: kelder-test)
"""
import sys

from . import provision, smolvm
from .cli import check_dir
from .smolvm import die


def run():
    smolvm.VM = vm = smolvm.env("VM", "kelder-test")
    if vm == "kelder":
        die("refusing to test in the main machine 'kelder'")
    if not smolvm.DRY_RUN and smolvm.exists():
        die(f"machine '{vm}' already exists; delete it first (smolvm machine delete --name {vm})")
    d = check_dir(".")

    provision.run()
    smolvm.start()
    if not smolvm.attach(d):
        die(f"could not attach {d}")

    fails = 0
    print(f"Running smoketest in '{vm}' (output appears when it finishes)...")
    if smolvm.guest("bash", "-lc", 'smoketest "$1"', "_", d)[0] != 0:
        fails = 1

    print()
    if smolvm.run("machine", "status", "--name", f"{vm}-nosuch", capture="all")[0] == 0 \
            and not smolvm.DRY_RUN:
        print("FAIL     host: machine status succeeds for an unknown machine")
        fails = 1
    else:
        print("PASS     host: machine status fails for an unknown machine")
    if smolvm.run("machine", "start", "--name", vm, capture="all")[0] == 0:
        print("PASS     host: machine start succeeds on a running machine")
    else:
        print("FAIL     host: machine start fails on a running machine")
        fails = 1

    print()
    if fails:
        print(f"Kept machine '{vm}' for inspection; remove it with: "
              f"smolvm machine delete --name {vm}", file=sys.stderr)
        sys.exit(1)
    if smolvm.run("machine", "delete", "--name", vm, "-f")[0] != 0:
        die(f"could not delete machine '{vm}'")
    print(f"All checks passed; deleted machine '{vm}'.")
