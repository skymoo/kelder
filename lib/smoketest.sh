#!/usr/bin/env bash
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Adam Mercer
# Test the installed smolvm in a throwaway machine: create it, run
# guest/smoketest in it with this checkout attached, check the smolvm CLI
# behaviour lib/smol.sh relies on, and delete the machine if everything passed.
# A machine that failed is kept for inspection.
#
#   make smoketest            VM=name picks the machine (default: smol-test)
set -euo pipefail
root="$(dirname "$(realpath "${BASH_SOURCE[0]}")")/.."
VM="${VM:-smol-test}"
# shellcheck source=SCRIPTDIR/smol.sh
source "$root/lib/smol.sh"

[[ $VM != smol ]] || die "refusing to test in the main machine 'smol'"
vm_exists && die "machine '$VM' already exists; delete it first (smolvm machine delete --name $VM)"

VM="$VM" bash "$root/lib/setup.sh"
vm_start
dir=$(realpath "$root")
vm_attach "$dir"

fails=0
echo "Running smoketest in '$VM' (output appears when it finishes)..."
# shellcheck disable=SC2016  # $1 expands in the guest
guest bash -lc 'smoketest "$1"' _ "$dir" || fails=1

echo
if smolvm machine status --name "$VM-nosuch" >/dev/null 2>&1; then
  echo "FAIL     host: machine status succeeds for an unknown machine"; fails=1
else
  echo "PASS     host: machine status fails for an unknown machine"
fi
if smolvm machine start --name "$VM" >/dev/null 2>&1; then
  echo "PASS     host: machine start succeeds on a running machine"
else
  echo "FAIL     host: machine start fails on a running machine"; fails=1
fi

echo
if ((fails)); then
  echo "Kept machine '$VM' for inspection; remove it with: smolvm machine delete --name $VM" >&2
  exit 1
fi
smolvm machine delete --name "$VM" -f
echo "All checks passed; deleted machine '$VM'."
