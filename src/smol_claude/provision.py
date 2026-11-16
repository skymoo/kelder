# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Adam Mercer
"""Create and provision the smolvm guest that smol-claude runs Claude Code in.

Run it as `smol-claude self setup`. Safe to re-run: it updates packages,
config and testvm on an existing machine. The machine has no project
mounted; smol-claude attaches one when it runs. Claude's state lives on the
host in $CLAUDE_STATE, so the machine is disposable.

  VM=name IMAGE=debian smol-claude self setup   machine name / base image
"""
import base64
import os
import subprocess
import sys
import time
from importlib.resources import files

from . import smolvm
from .smolvm import die, guest, guest_ok, note

PACKAGES = [
    # base
    "ca-certificates",
    "curl",
    "git",
    "iproute2",
    "openssh-client",
    "procps",
    "python3",
    # project tooling
    "make",
    "shellcheck",
    # uv is installed separately: see UV below
    # containers
    "crun",
    "podman",
    "slirp4netns",
    "uidmap",
    # VMs (testvm)
    "genisoimage",
    "ovmf",
    "qemu-system-x86",
    "qemu-utils",
]

# Files from this package's guest/ directory, with where they go in the guest.
GUEST_FILES = [
    ("containers-tsi.conf", "/etc/containers/containers.conf.d/50-smolvm-tsi.conf", "644"),
    ("testvm", "/usr/local/bin/testvm", "755"),
    ("smoketest", "/usr/local/bin/smoketest", "755"),
]

# Where the guest records when its packages were last brought up to date
# (seconds since the epoch), and how old that may get before a session that
# boots the machine updates it first.
STAMP = "/var/lib/smol-claude-updated"
UPDATE_DAYS = 7

# Upgrade everything and install the packages given.
APT = """
set -euo pipefail
export DEBIAN_FRONTEND=noninteractive
apt-get -q -o Acquire::Retries=3 update
apt-get -q -y -o Acquire::Retries=3 full-upgrade
apt-get -q -y -o Acquire::Retries=3 install "$@"
"""

# Install uv with its standalone installer (there is no Debian package), or
# update the one already there. It goes in ~/.local/bin, which sessions have
# on PATH, so the installer is told to leave shell profiles alone.
# Download first: `curl | sh` reports success when curl fails.
UV = """
set -euo pipefail
export PATH="$HOME/.local/bin:$PATH" UV_NO_MODIFY_PATH=1
if command -v uv >/dev/null; then
  uv self update
else
  f=$(mktemp); trap "rm -f $f" EXIT
  curl -LsSf https://astral.sh/uv/install.sh -o "$f"; sh "$f"
fi
"""

# Claude Code updates itself in the background; this makes sure of it. A
# machine that doesn't have it yet is left for setup to install.
UPDATE_CLAUDE = """
export PATH="$HOME/.local/bin:$PATH"
command -v claude >/dev/null || exit 0
claude update
"""

WRITE_STAMP = 'date +%s >"$1"'

READ_STAMP = 'cat "$1" 2>/dev/null'

# Exec gives no stdin, so a file travels base64-encoded as an argument.
WRITE_FILE = 'mkdir -p "$(dirname "$2")" && printf %s "$1" | base64 -d >"$2" && chmod "$3" "$2"'

SET_TIMEZONE = """
test -e "/usr/share/zoneinfo/$1" &&
  ln -sf "/usr/share/zoneinfo/$1" /etc/localtime && echo "$1" >/etc/timezone
"""

CLAUDE_INSTALLED = 'test -x "$HOME/.local/bin/claude"'

# Download first: `curl | bash` without pipefail reports success when curl fails.
INSTALL_CLAUDE = """
set -euo pipefail; f=$(mktemp); trap "rm -f $f" EXIT
curl -fsSL https://claude.ai/install.sh -o "$f"; bash "$f"
"""

# Move state from before the state mount (Claude's default ~/.claude and
# ~/.claude.json in the machine) into the state directory. Only when the state
# directory is still empty, so it never overwrites anything; the old copy stays.
MIGRATE_STATE = """
state=$1
[[ -z $(ls -A "$state") && -e $HOME/.claude.json ]] || exit 0
echo "Copying existing Claude state from $HOME/.claude into the state directory..."
if [[ -d $HOME/.claude ]]; then cp -a "$HOME/.claude/." "$state/"; fi
cp -a "$HOME/.claude.json" "$state/.claude.json"
chmod 700 "$state"   # cp -a copied the source directory's mode onto it
"""


def warn(msg):
    print(f"warning: {msg}", file=sys.stderr)


def asroot():
    """The prefix that makes a guest command run as root."""
    return [] if guest("id", "-u", capture="out")[1].strip() == "0" else ["sudo"]


def update_guest(quiet=False):
    """Bring the guest up to date and record when.

    Upgrades its packages, installs any missing from PACKAGES, installs or
    updates uv, and updates Claude Code if it is installed. Returns (exit status, output) of the first step to fail,
    or (0, "") with the time recorded; the output is only collected when quiet.
    """
    capture = "all" if quiet else None
    root = asroot()
    for cmd in (
        [*root, "bash", "-c", APT, "_", *PACKAGES],
        ["bash", "-c", UV],   # as the session's user, like Claude Code
        ["bash", "-c", UPDATE_CLAUDE],
        [*root, "bash", "-c", WRITE_STAMP, "_", STAMP],
    ):
        rc, out = guest(*cmd, capture=capture)
        if rc != 0:
            return rc, out
    return 0, ""


def update_if_stale():
    """Update the guest if its last update is over UPDATE_DAYS old or unknown.

    Called when a session boots the machine. NO_UPDATE=1 skips it; a failed
    update is reported and the session goes ahead.
    """
    if os.environ.get("NO_UPDATE") == "1" or smolvm.DRY_RUN:
        return
    stamp = guest("bash", "-c", READ_STAMP, "_", STAMP, capture="out")[1].strip()
    if stamp.isdigit():
        days = int((time.time() - int(stamp)) // 86400)
        if days < UPDATE_DAYS:
            return
        when = f"last updated {days} days ago"
    else:
        when = "no record of an update"
    note(f"updating machine '{smolvm.VM}' ({when})...")
    rc, out = update_guest(quiet=True)
    if rc != 0:
        tail = "\n".join(out.splitlines()[-15:])
        note(f"update failed (exit {rc}); starting the session anyway. Its last output:\n{tail}")


def host_checks():
    if not os.path.exists("/dev/kvm"):
        die("/dev/kvm not found on the host")
    nested = ""
    for module in ("kvm_intel", "kvm_amd"):
        try:
            with open(f"/sys/module/{module}/parameters/nested") as f:
                nested += f.read()
        except OSError:
            pass
    if "Y" not in nested and "1" not in nested:
        warn("nested virtualization looks disabled on the host; VMs inside the guest won't get KVM")
    if smolvm.run("machine", "mount", "--help", capture="all")[0] != 0:
        die("smolvm 1.32 or newer is needed (smol-claude uses 'smolvm machine mount')")


def host_timezone():
    """The host's timezone name, or "" if it can't be worked out."""
    # timedatectl first; /etc/localtime's target as a fallback for hosts
    # without systemd.
    try:
        p = subprocess.run(["timedatectl", "show", "-p", "Timezone", "--value"], check=False,
                           stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True)
        if p.returncode == 0 and p.stdout.strip():
            return p.stdout.strip()
    except OSError:
        pass
    try:
        target = os.readlink("/etc/localtime")
    except OSError:
        return ""
    return target.split("zoneinfo/", 1)[1] if "zoneinfo/" in target else ""


def run():
    vm = smolvm.VM
    state = smolvm.GUEST_CLAUDE_STATE
    image_set = "IMAGE" in os.environ
    image = smolvm.env("IMAGE", "debian")

    host_checks()
    os.makedirs(smolvm.CLAUDE_STATE, exist_ok=True)
    os.chmod(smolvm.CLAUDE_STATE, 0o700)   # holds Claude's login credentials
    state_mount = f"{os.path.realpath(smolvm.CLAUDE_STATE)}:{state}"

    # --- create / start ------------------------------------------------------
    if smolvm.exists():
        print(f"Machine '{vm}' exists; reusing it.")
        if image_set:
            warn("IMAGE is ignored for an existing machine; delete it and re-run to change images")
    elif smolvm.run("machine", "create", "--name", vm, "--nested", "--net",
                    "--image", image, "-v", state_mount)[0] != 0:
        die(f"could not create machine '{vm}'")
    smolvm.start()

    # Machines made before the state mount existed get it added. Mounts can only
    # change while the machine is stopped, so this restarts it once.
    if not guest_ok("mountpoint", "-q", state):
        print(f"Adding the Claude state mount; restarting machine '{vm}' "
              "(this ends any smol-claude sessions in it)...")
        if smolvm.run("machine", "stop", "--name", vm)[0] != 0:
            die(f"could not stop machine '{vm}'")
        if smolvm.run("machine", "update", "--name", vm, "-v", state_mount)[0] != 0:
            die(f"could not add the state mount to machine '{vm}'")
        smolvm.start()
        if not guest_ok("mountpoint", "-q", state):
            die(f"state directory still not mounted at {state} after restart")

    # Machines made by older versions mount a project at /work on every boot,
    # which Claude would see alongside whatever smol-claude attaches.
    if guest_ok("mountpoint", "-q", "/work") and not smolvm.DRY_RUN:
        warn(f"machine '{vm}' still mounts a directory at /work. Remove it with:")
        print(f"  smolvm machine update --name {vm} --remove-volume <host-dir>:/work"
              "   (then restart the machine)", file=sys.stderr)

    # --- packages ------------------------------------------------------------
    print("Updating the guest: packages, uv and Claude Code...")
    if update_guest()[0] != 0:
        die("could not update the guest")
    root = asroot()

    # --- guest config --------------------------------------------------------
    for name, dest, mode in GUEST_FILES:
        data = files("smol_claude").joinpath("guest", name).read_bytes()
        if guest(*root, "bash", "-c", WRITE_FILE, "_",
                 base64.b64encode(data).decode(), dest, mode)[0] != 0:
            die(f"could not install {dest} in the guest")

    tz = host_timezone()
    if tz:
        print(f"Setting the guest timezone to {tz}...")
        if guest(*root, "bash", "-c", SET_TIMEZONE, "_", tz)[0] != 0:
            warn(f"could not set the guest timezone to {tz}; leaving it unchanged")
    else:
        warn("could not work out the host timezone; the guest stays on its default")

    # --- Claude Code ---------------------------------------------------------
    if not guest_ok("bash", "-c", CLAUDE_INSTALLED):
        print("Installing Claude Code...")
        guest("bash", "-c", INSTALL_CLAUDE)
        if not guest_ok("bash", "-c", CLAUDE_INSTALLED):
            die("Claude Code installer ran but claude is missing")

    if guest("bash", "-c", MIGRATE_STATE, "_", state)[0] != 0:
        die("could not copy the existing Claude state into the state directory")

    # --- smoke tests ---------------------------------------------------------
    print("Checking KVM and podman in the guest...")
    if not guest_ok("test", "-r", "/dev/kvm", "-a", "-w", "/dev/kvm"):
        die("/dev/kvm missing or not accessible in the guest")
    # A paused VM (-S) stays up until timeout kills it (124); a KVM error exits sooner.
    rc = guest("timeout", "3", "qemu-system-x86_64", "-accel", "kvm", "-display", "none",
               "-nodefaults", "-S", capture="all")[0]
    if rc != 124 and not smolvm.DRY_RUN:
        die(f"QEMU could not start a KVM guest (exit {rc}); check nested virtualization")
    if guest("podman", "info", capture="drop")[0] != 0:
        die("podman is installed but not working")

    print("Setup done. Run smol-claude from a project directory, or: smol-claude self -C DIR")
