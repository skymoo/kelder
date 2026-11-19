# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Adam Mercer
"""Test the installed smolvm or QEMU, or a smolvm release, in a throwaway machine.

Run it as `kelder self smoketest` from a project directory. It creates
the machine, runs the guest's smoketest in it with the current directory
attached, checks the smolvm CLI behaviour kelder relies on, and deletes
the machine if everything passed. A machine that failed is kept for
inspection. With BACKEND=qemu the machine is a QEMU one, which has no CLI
to check.

With `--release VERSION` it tests that smolvm release instead of the
installed one: the release is downloaded to a temporary directory, which
also holds the machine, and removed again if everything passed. That works
inside a kelder guest too, so an upgrade can be tried before the host gets it.

  VM=name kelder self smoketest   picks the machine (default: kelder-test)
"""
import shlex
import shutil
import sys

from . import machine, provision, settings, smolvm
from .cli import check_dir
from .settings import die


def run(args: list[str] | None = None) -> None:
    args = args or []
    if args and (len(args) != 2 or args[0] != "--release"):
        die("usage: kelder self smoketest [--release VERSION]")
    settings.VM = vm = settings.env("VM", "kelder-test")
    if vm == "kelder":
        die("refusing to test in the main machine 'kelder'")
    tmp = ""
    if args:
        if machine.backend() is not smolvm:
            die("--release tests a smolvm release; it cannot be used with a QEMU machine")
        from . import release
        tmp = release.use(args[1])
    machine.require()
    if not settings.DRY_RUN and machine.exists():
        die(f"machine '{vm}' already exists; delete it first ({machine.delete_command()})")
    d = check_dir(".")

    provision.run()
    machine.start()
    if not machine.attach(d):
        die(f"could not attach {d}")

    fails = 0
    print(f"Running smoketest in '{vm}' (output appears when it finishes)...")
    if machine.guest("bash", "-lc", 'smoketest "$1"', "_", d)[0] != 0:
        fails = 1

    print()
    fails += machine.smoketest_host()

    print()
    if fails:
        delete = machine.delete_command()
        if tmp:
            delete = (f"SMOLVM_DATA_DIR={shlex.quote(tmp)}/data {shlex.quote(smolvm.SMOLVM_BIN)}"
                      f" machine delete --name {vm} -f && rm -rf {shlex.quote(tmp)}")
        print(f"Kept machine '{vm}' for inspection; remove it with: {delete}", file=sys.stderr)
        sys.exit(1)
    if not machine.delete():
        die(f"could not delete machine '{vm}'")
    if tmp:
        shutil.rmtree(tmp)
    print(f"All checks passed; deleted machine '{vm}'.")
