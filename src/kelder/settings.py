# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Adam Mercer
"""Settings every module shares, and how kelder reports to the user."""
import os
import sys
from typing import Literal, NoReturn

PROG = "kelder"
HOME = os.path.expanduser("~")

# What a command's output is done with; see smolvm.run.
Capture = Literal["all", "out", "drop"] | None


def env(name: str, default: str) -> str:
    """An environment variable, with empty treated as unset."""
    return os.environ.get(name) or default


# The machine. `self smoketest` changes it to its throwaway machine.
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
# DRY_RUN=1 prints every smolvm call and runs none.
DRY_RUN = os.environ.get("DRY_RUN") == "1"


def note(msg: str) -> None:
    print(f"{PROG}: {msg}", file=sys.stderr)


def die(msg: str) -> NoReturn:
    note(msg)
    sys.exit(1)
