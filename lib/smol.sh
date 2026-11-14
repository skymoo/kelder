# shellcheck shell=bash
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Adam Mercer
# Shared helpers for bin/smol-claude and bin/smol-claude-setup. Source this file; don't run it.
# Everything that depends on how the smolvm CLI behaves lives here.

VM="${VM:-smol}"

# Claude's state (login, settings, history, memory) lives on the host so the
# machine can be thrown away and rebuilt. It is mounted into the machine at
# boot, and smol-claude points CLAUDE_CONFIG_DIR at it.
CLAUDE_STATE="${CLAUDE_STATE:-${XDG_DATA_HOME:-$HOME/.local/share}/smol-claude/claude}"
GUEST_CLAUDE_STATE=/var/lib/smol-claude

die() { printf '%s: %s\n' "${0##*/}" "$*" >&2; exit 1; }

command -v smolvm >/dev/null 2>&1 || die "smolvm not found on PATH"

# Run a command in the guest without a TTY.
guest() { smolvm machine exec --name "$VM" -- "$@"; }

# Succeeds if the machine exists, running or not.
# ASSUMPTION: `smolvm machine status` exits non-zero only for an unknown machine.
vm_exists() { smolvm machine status --name "$VM" >/dev/null 2>&1; }

# Start the machine if needed, then wait until it accepts exec.
vm_start() {
  local out
  if ! out=$(smolvm machine start --name "$VM" 2>&1); then
    # Starting a machine that is already running may fail; that's fine as long
    # as it answers. Anything else is a real error, so show it.
    guest true >/dev/null 2>&1 || die "could not start machine '$VM':"$'\n'"$out"
  fi
  for _ in {1..30}; do
    guest true >/dev/null 2>&1 && return 0
    sleep 1
  done
  die "machine '$VM' started but did not answer exec within 30s"
}

# Host directories are attached to the running machine at the same path they
# have on the host (`machine mount`, smolvm 1.32+). Attachments last until the
# machine stops.
vm_attached() { smolvm machine mount ls --name "$VM" | grep -Fxq -- "$1 -> $1"; }
vm_attach()   { smolvm machine mount add --name "$VM" -v "$1:$1" >/dev/null; }
vm_detach()   { smolvm machine mount rm --name "$VM" -t "$1" >/dev/null; }
