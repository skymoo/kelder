# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Adam Mercer
"""Tools Debian doesn't package, or packages too old, come from mise
(https://mise.jdx.dev): a project lists them in its own mise.toml, which
works the same on the host and in CI.

The guest gets mise from its Debian repository, so the weekly package
upgrade keeps it current. `kelder self setup`, run from a project with a
mise config, attaches the project and runs `mise install`; only setup
installs, as with packages. Sessions have mise's shims on PATH, which
pick each tool's version from the directory they are run in, and say
which of the project's tools the machine lacks.
"""
import os

from . import machine
from .session import Session

# What makes a directory a mise project (mise's "Configuration" page).
CONFIG_FILES = (
    "mise.toml", "mise.local.toml", ".mise.toml", ".mise.local.toml",
    "mise", ".mise", ".config/mise.toml", ".config/mise", ".tool-versions",
)

# Add mise's Debian repository, as root, unless it is there; the package
# itself is one of provision.PACKAGES.
ENABLE_REPO = """
set -euo pipefail
[[ -e /etc/apt/sources.list.d/extrepo_mise.sources ]] && exit 0
export DEBIAN_FRONTEND=noninteractive
apt-get -q -o Acquire::Retries=3 update
apt-get -q -y -o Acquire::Retries=3 install extrepo
extrepo enable mise
"""

# Follows cli.GUEST_ENV in a session. A shim would otherwise download a
# missing tool mid-session. A config with [env] or templates stops every
# shim until it is trusted; the session's directories are ones the user
# chose to run Claude in.
GUEST_ENV = """
export PATH="$HOME/.local/share/mise/shims:$PATH"
export MISE_NOT_FOUND_AUTO_INSTALL=false MISE_AUTO_INSTALL=false
export MISE_TRUSTED_CONFIG_PATHS=$(IFS=:; d=("$dir" "${extra[@]}"); echo "${d[*]}")
"""

INSTALL = 'cd "$1" && mise install'

# One line for each tool the directory's config asks for and the guest
# lacks: name, version, "(missing)", config file, version asked for.
MISSING = """
command -v mise >/dev/null || exit 127
cd "$1" && MISE_TRUSTED_CONFIG_PATHS=$1 mise ls --missing --current --no-header
"""


def configured(directory: str) -> bool:
    """True if the directory has a mise config."""
    return any(os.path.exists(os.path.join(directory, f)) for f in CONFIG_FILES)


def install(project: str) -> bool:
    """Attach the project and install the tools its config lists; False if that fails."""
    session = Session([project])
    session.attach()
    try:
        return machine.guest("bash", "-c", INSTALL, "_", project)[0] == 0
    finally:
        session.cleanup()


def missing(project: str) -> list[str] | None:
    """The tools (name@version) the attached project asks for and the guest
    lacks; None if the guest has no mise."""
    rc, out = machine.guest("bash", "-c", MISSING, "_", project, capture="out")
    if rc == 127:
        return None
    return ["@".join(fields[:2]) for fields in map(str.split, out.splitlines()) if len(fields) > 1]
