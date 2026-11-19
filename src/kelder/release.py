# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Adam Mercer
"""Fetch a smolvm release so it can be tested without installing it.

`kelder self smoketest --release VERSION` uses this to test a release
before the host is upgraded. It also works inside a kelder guest, which
has no smolvm of its own but has nested KVM.
"""
import hashlib
import os
import platform
import re
import shutil
import subprocess
import tempfile
import urllib.request

from . import smolvm
from .settings import die

URL = "https://github.com/smol-machines/smolvm/releases/download/v{version}/{name}"
# smolvm's name for each machine type in its Linux tarballs.
ARCH = {"x86_64": "x86_64", "aarch64": "arm64"}
# Where the release and its machines are kept. smolvm puts a Unix socket for
# each machine below its data directory and refuses a path over 107 bytes, so
# this has to be short; /tmp may be in memory, too small for a machine's disk.
TMP = "/var/tmp"


def tarball(version: str) -> str:
    """The name of the release's Linux tarball for this machine."""
    arch = ARCH.get(platform.machine())
    if arch is None:
        die(f"smolvm has no Linux release for {platform.machine()}")
    return f"smolvm-{version}-linux-{arch}.tar.gz"


def download(url: str, path: str) -> None:
    with urllib.request.urlopen(url) as response, open(path, "wb") as f:
        shutil.copyfileobj(response, f)


def fetch(version: str, dest: str) -> str:
    """Download, verify and unpack a release in dest; returns its smolvm wrapper."""
    name = tarball(version)
    archive = os.path.join(dest, name)
    sums = os.path.join(dest, "checksums.sha256")
    for target in (archive, sums):
        url = URL.format(version=version, name=os.path.basename(target))
        try:
            download(url, target)
        except OSError as e:
            die(f"could not download {url}: {e}")

    with open(sums) as f:
        expected = {n: digest for digest, n in (line.split() for line in f if line.strip())}
    with open(archive, "rb") as f:
        digest = hashlib.file_digest(f, "sha256").hexdigest()
    if expected.get(name) != digest:
        die(f"checksum mismatch for {name}: got {digest}, expected {expected.get(name)}")

    if subprocess.run(["tar", "-xzf", archive, "-C", dest, "--no-same-owner"],
                      check=False).returncode != 0:
        die(f"could not unpack {archive}")
    os.remove(archive)
    wrapper = os.path.join(dest, name.removesuffix(".tar.gz"), "smolvm")
    if not os.access(wrapper, os.X_OK):
        die(f"{name} has no smolvm wrapper at {wrapper}")
    return wrapper


def use(version: str) -> str:
    """Make every smolvm call use a release; returns the directory to remove afterwards."""
    version = version.removeprefix("v")
    if not re.fullmatch(r"\d+\.\d+\.\d+", version):
        die(f"not a smolvm version: {version}")
    tmp = tempfile.mkdtemp(prefix="kelder-", dir=TMP)
    print(f"Fetching smolvm {version} into {tmp}...")
    try:
        smolvm.SMOLVM_BIN = fetch(version, tmp)
    except SystemExit:
        shutil.rmtree(tmp)
        raise
    # A data directory of its own keeps the release away from the installed
    # smolvm's machines and database.
    os.environ["SMOLVM_DATA_DIR"] = os.path.join(tmp, "data")
    # Run as root (in a kelder guest), smolvm gives each machine's process a
    # uid of its own, which cannot write to the directories kelder mounts.
    if os.geteuid() == 0:
        os.environ["SMOLVM_VM_UID_DROP"] = "off"
    return tmp
