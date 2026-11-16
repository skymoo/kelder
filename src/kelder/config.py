# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Adam Mercer
"""A project's .kelder.toml: the machine it runs in, the extra packages
`kelder self setup` installs there, and the memory folder its sessions use.

  vm = "name"
  packages = [
      "package",
  ]
  memory = "name"

Every key is optional. Only the primary directory's file is read: one in a
directory above or below it is ignored.
"""
import os
import re
import tomllib
from dataclasses import dataclass, field

from .smolvm import die

NAME = ".kelder.toml"

# The machine name ends up in a file path and on smolvm's command line, and
# package names on apt-get's, so neither may look like an option or a path.
VM_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]*")
# Debian's rule for package names (Policy 5.6.1).
PACKAGE_NAME = re.compile(r"[a-z0-9][a-z0-9+.-]+")
# The memory name is a folder under the state directory's memory/, so it
# may not contain a "/" or start with a ".".
MEMORY_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]*")


@dataclass
class Config:
    """What a project asks for; path is None when it has no file."""

    path: str | None = None
    vm: str | None = None
    packages: list[str] = field(default_factory=list)
    memory: str | None = None


def load(primary: str) -> Config:
    """Read the primary directory's file, if it has one; dies if it is invalid."""
    path = os.path.join(os.path.realpath(primary), NAME)
    if not os.path.isfile(path):
        return Config()
    try:
        with open(path, "rb") as f:
            data = tomllib.load(f)
    except (OSError, tomllib.TOMLDecodeError) as e:
        die(f"could not read {path}: {e}")
    unknown = sorted(set(data) - {"vm", "packages", "memory"})
    if unknown:
        die(f"{path}: unknown key '{unknown[0]}' (the keys are vm, packages and memory)")
    vm = data.get("vm")
    if vm is not None and not (isinstance(vm, str) and VM_NAME.fullmatch(vm)):
        die(f"{path}: vm must be a machine name (letters, digits, '_', '.' and '-')")
    packages = data.get("packages", [])
    if not isinstance(packages, list):
        die(f"{path}: packages must be a list of package names")
    for package in packages:
        if not (isinstance(package, str) and PACKAGE_NAME.fullmatch(package)):
            die(f"{path}: not a package name: {package!r}")
    memory = data.get("memory")
    if memory is not None and not (isinstance(memory, str) and MEMORY_NAME.fullmatch(memory)):
        die(f"{path}: memory must be a folder name (letters, digits, '_', '.' and '-')")
    return Config(path, vm, packages, memory)
