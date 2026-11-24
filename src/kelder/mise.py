# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Adam Mercer
"""Tools Debian doesn't package, or packages too old, come from mise
(https://mise.jdx.dev): a project lists them in its own mise.toml, which
works the same on the host and in CI.

The guest gets mise from its Debian repository, so the weekly package
upgrade keeps it current. `kelder self setup`, run from a project with a
mise config, attaches the project and runs `mise install`; only setup
installs, as with packages. The tools go in the project's own directory
(see data_dir), not the machine's disk, so projects sharing a machine
share no tools. Sessions have the project's shims on PATH and say which
of its tools are not installed.
"""
import os

from . import machine, settings
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

# Follows cli.GUEST_ENV in a session, which sets dir and vm; the data
# directory is data_dir's. Rust's toolchains and Cargo's files are
# rustup's, which keeps them in the home directory unless told where
# else. mise keeps what it downloads in its cache; /tmp is in memory, so
# none of it stays on the machine's disk. A shim would otherwise download a
# missing tool mid-session. A config with [env] or templates stops every
# shim until it is trusted; the session's directories are ones the user
# chose to run Claude in.
GUEST_ENV = """
export MISE_DATA_DIR=$dir/.mise-$vm
export MISE_RUSTUP_HOME=$MISE_DATA_DIR/rustup MISE_CARGO_HOME=$MISE_DATA_DIR/cargo
export MISE_CACHE_DIR=/tmp/mise-cache
export PATH="$MISE_DATA_DIR/shims:$PATH"
export MISE_NOT_FOUND_AUTO_INSTALL=false MISE_AUTO_INSTALL=false
export MISE_TRUSTED_CONFIG_PATHS=$(IFS=:; d=("$dir" "${extra[@]}"); echo "${d[*]}")
"""

# Gets the project and its data directory. The .gitignore keeps the
# directory out of the project's git status, as uv's does for a .venv.
INSTALL = """
mkdir -p "$2" && echo '*' >"$2/.gitignore" && cd "$1" &&
  MISE_DATA_DIR=$2 MISE_RUSTUP_HOME=$2/rustup MISE_CARGO_HOME=$2/cargo \\
  MISE_CACHE_DIR=/tmp/mise-cache mise install
"""

# Gets the project and its data directory. One line for each tool the
# project's config asks for that is not installed there: name, version,
# "(missing)", config file, version asked for.
MISSING = """
command -v mise >/dev/null || exit 127
cd "$1" && MISE_TRUSTED_CONFIG_PATHS=$1 MISE_DATA_DIR=$2 MISE_CACHE_DIR=/tmp/mise-cache \\
  mise ls --missing --current --no-header
"""


def configured(directory: str) -> bool:
    """True if the directory has a mise config."""
    return any(os.path.exists(os.path.join(directory, f)) for f in CONFIG_FILES)


def data_dir(project: str) -> str:
    """Where the project's tools are installed (mise's MISE_DATA_DIR): in
    the project, which is on the host's disk, and named after the machine
    like uv's environment."""
    return os.path.join(project, f".mise-{settings.VM}")


def install(project: str) -> bool:
    """Attach the project and install the tools its config lists; False if that fails."""
    session = Session([project])
    session.attach()
    try:
        return machine.guest("bash", "-c", INSTALL, "_", project, data_dir(project))[0] == 0
    finally:
        session.cleanup()


def missing(project: str) -> list[str] | None:
    """The tools (name@version) the attached project asks for that are not
    installed for it; None if the guest has no mise."""
    rc, out = machine.guest("bash", "-c", MISSING, "_", project, data_dir(project), capture="out")
    if rc == 127:
        return None
    return ["@".join(fields[:2]) for fields in map(str.split, out.splitlines()) if len(fields) > 1]
