#!/usr/bin/env bash
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Adam Mercer
# Create and provision the smolvm guest that `smol-claude` runs Claude Code in.
# Run it as `smol-claude self setup`.
# Safe to re-run: it updates packages, config and testvm on an existing machine.
# The machine has no project mounted; `smol-claude` attaches one when it runs.
# Claude's state lives on the host in $CLAUDE_STATE, so the machine is
# disposable.
#
#   VM=name IMAGE=debian smol-claude self setup   machine name / base image
set -euo pipefail
# This file sits in lib/, next to guest/, in a checkout and in
# share/smol-claude when installed.
root="$(dirname "$(realpath "${BASH_SOURCE[0]}")")/.."
# shellcheck source=SCRIPTDIR/smol.sh
source "$root/lib/smol.sh"

IMAGE_SET="${IMAGE+x}"
IMAGE="${IMAGE:-debian}"

# --- host checks -------------------------------------------------------------
[[ -e /dev/kvm ]] || die "/dev/kvm not found on the host"
nested=$(cat /sys/module/kvm_intel/parameters/nested /sys/module/kvm_amd/parameters/nested 2>/dev/null || true)
[[ $nested == *[Y1]* ]] || echo "warning: nested virtualization looks disabled on the host; VMs inside the guest won't get KVM" >&2
smolvm machine mount --help >/dev/null 2>&1 || die "smolvm 1.32 or newer is needed (smol-claude uses 'smolvm machine mount')"

mkdir -p "$CLAUDE_STATE"
chmod 700 "$CLAUDE_STATE"   # holds Claude's login credentials
state_mount="$(realpath "$CLAUDE_STATE"):$GUEST_CLAUDE_STATE"

# --- create / start ----------------------------------------------------------
if vm_exists; then
  echo "Machine '$VM' exists; reusing it."
  [[ -n $IMAGE_SET ]] && echo "warning: IMAGE is ignored for an existing machine; delete it and re-run to change images" >&2
else
  smolvm machine create --name "$VM" --nested --net --image "$IMAGE" -v "$state_mount"
fi
vm_start

# Machines made before the state mount existed get it added. Mounts can only
# change while the machine is stopped, so this restarts it once.
if ! guest mountpoint -q "$GUEST_CLAUDE_STATE"; then
  echo "Adding the Claude state mount; restarting machine '$VM' (this ends any smol-claude sessions in it)..."
  smolvm machine stop --name "$VM"
  smolvm machine update --name "$VM" -v "$state_mount"
  vm_start
  guest mountpoint -q "$GUEST_CLAUDE_STATE" || die "state directory still not mounted at $GUEST_CLAUDE_STATE after restart"
fi

# Machines made by older versions of this script mount a project at /work on
# every boot, which Claude would see alongside whatever smol-claude attaches.
if guest mountpoint -q /work; then
  echo "warning: machine '$VM' still mounts a directory at /work. Remove it with:" >&2
  echo "  smolvm machine update --name $VM --remove-volume <host-dir>:/work   (then restart the machine)" >&2
fi

asroot=()
[[ $(guest id -u) == 0 ]] || asroot=(sudo)

# --- packages ----------------------------------------------------------------
packages=(
  # base
  ca-certificates
  curl
  git
  iproute2
  openssh-client
  procps
  python3
  # containers
  crun
  podman
  slirp4netns
  uidmap
  # VMs (testvm)
  genisoimage
  ovmf
  qemu-system-x86
  qemu-utils
)
echo "Installing packages..."
guest "${asroot[@]}" bash -c '
  set -euo pipefail
  export DEBIAN_FRONTEND=noninteractive
  apt-get -q -o Acquire::Retries=3 update
  apt-get -q -y -o Acquire::Retries=3 install "$@"' _ "${packages[@]}"

# --- guest config ------------------------------------------------------------
# Copy a file from this repo into the guest. Exec gives no stdin, so the file
# travels base64-encoded as an argument.
install_file() {  # SRC DEST MODE
  # shellcheck disable=SC2016  # $1..$3 expand in the guest
  guest "${asroot[@]}" bash -c 'mkdir -p "$(dirname "$2")" && printf %s "$1" | base64 -d >"$2" && chmod "$3" "$2"' \
    _ "$(base64 -w0 "$1")" "$2" "$3"
}
install_file "$root/guest/containers-tsi.conf" /etc/containers/containers.conf.d/50-smolvm-tsi.conf 644
install_file "$root/guest/testvm" /usr/local/bin/testvm 755
install_file "$root/guest/smoketest" /usr/local/bin/smoketest 755

# --- Claude Code -------------------------------------------------------------
# shellcheck disable=SC2016  # $HOME is the guest's
claude_installed() { guest bash -c 'test -x "$HOME/.local/bin/claude"'; }

if ! claude_installed; then
  echo "Installing Claude Code..."
  # Download first: `curl | bash` without pipefail reports success when curl fails.
  # shellcheck disable=SC2016  # the script expands in the guest
  guest bash -c 'set -euo pipefail; f=$(mktemp); trap "rm -f $f" EXIT
    curl -fsSL https://claude.ai/install.sh -o "$f"; bash "$f"'
  claude_installed || die "Claude Code installer ran but claude is missing"
fi

# Move state from before the state mount (Claude's default ~/.claude and
# ~/.claude.json in the machine) into the state directory. Only when the state
# directory is still empty, so it never overwrites anything; the old copy stays.
# shellcheck disable=SC2016  # the script expands in the guest
guest bash -c '
  state=$1
  [[ -z $(ls -A "$state") && -e $HOME/.claude.json ]] || exit 0
  echo "Copying existing Claude state from $HOME/.claude into the state directory..."
  if [[ -d $HOME/.claude ]]; then cp -a "$HOME/.claude/." "$state/"; fi
  cp -a "$HOME/.claude.json" "$state/.claude.json"
  chmod 700 "$state"   # cp -a copied the source directory'"'"'s mode onto it' _ "$GUEST_CLAUDE_STATE"

# --- smoke tests -------------------------------------------------------------
echo "Checking KVM and podman in the guest..."
guest test -r /dev/kvm -a -w /dev/kvm || die "/dev/kvm missing or not accessible in the guest"
# A paused VM (-S) stays up until timeout kills it (124); a KVM error exits sooner.
rc=0; guest timeout 3 qemu-system-x86_64 -accel kvm -display none -nodefaults -S >/dev/null 2>&1 || rc=$?
[[ $rc == 124 ]] || die "QEMU could not start a KVM guest (exit $rc); check nested virtualization"
guest podman info >/dev/null || die "podman is installed but not working"

echo "Setup done. Run smol-claude from a project directory, or: smol-claude -C DIR"
