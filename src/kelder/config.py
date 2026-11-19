# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Adam Mercer
"""A project's settings: the machine it runs in, the extra packages
`kelder self setup` installs there, and the memory folder its sessions use.

They come from a .kelder.toml in the project:

  vm = "name"
  packages = [
      "package",
  ]
  memory = "name"

or, for a project without that file, from the user's own file
(~/.config/kelder/config.toml), which gives the same keys per directory:

  [projects."~/git/work"]
  vm = "name"

Every key is optional. Only the primary directory's .kelder.toml is read:
one in a directory above or below it is ignored. An entry in the user's file
covers its directory and everything below it, and the longest match is used.
A project's .kelder.toml replaces the entry outright: nothing is merged.
"""
import os
import re
import tomllib
from dataclasses import dataclass, field

from . import settings
from .settings import die

NAME = ".kelder.toml"
KEYS = {"vm", "packages", "memory"}

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
    """What a project asks for; path is the file it came from, None when
    neither file has anything for the project."""

    path: str | None = None
    vm: str | None = None
    packages: list[str] = field(default_factory=list)
    memory: str | None = None


def user_file() -> str:
    """The user's own file, for projects that have no .kelder.toml."""
    base = settings.env("XDG_CONFIG_HOME", os.path.join(settings.HOME, ".config"))
    return os.path.join(base, "kelder/config.toml")


def _read(path: str) -> dict:
    try:
        with open(path, "rb") as f:
            return tomllib.load(f)
    except (OSError, tomllib.TOMLDecodeError) as e:
        die(f"could not read {path}: {e}")


def _parse(data: dict, path: str, where: str) -> Config:
    """Check one set of keys; where names it in messages."""
    unknown = sorted(set(data) - KEYS)
    if unknown:
        die(f"{where}: unknown key '{unknown[0]}' (the keys are vm, packages and memory)")
    vm = data.get("vm")
    if vm is not None and not (isinstance(vm, str) and VM_NAME.fullmatch(vm)):
        die(f"{where}: vm must be a machine name (letters, digits, '_', '.' and '-')")
    packages = data.get("packages", [])
    if not isinstance(packages, list):
        die(f"{where}: packages must be a list of package names")
    for package in packages:
        if not (isinstance(package, str) and PACKAGE_NAME.fullmatch(package)):
            die(f"{where}: not a package name: {package!r}")
    memory = data.get("memory")
    if memory is not None and not (isinstance(memory, str) and MEMORY_NAME.fullmatch(memory)):
        die(f"{where}: memory must be a folder name (letters, digits, '_', '.' and '-')")
    return Config(path, vm, packages, memory)


def _user_entry(primary: str) -> Config:
    """The user's file's entry for the primary directory, if it has one."""
    path = user_file()
    if not os.path.isfile(path):
        return Config()
    data = _read(path)
    unknown = sorted(set(data) - {"projects"})
    if unknown:
        die(f"{path}: unknown key '{unknown[0]}' (the only key is projects)")
    projects = data.get("projects", {})
    if not isinstance(projects, dict):
        die(f"{path}: projects must be a table of directories")
    best = None
    # Every entry is checked, so a mistake shows up whichever project is run.
    for name, entry in projects.items():
        where = f'{path}: projects."{name}"'
        directory = os.path.expanduser(name)
        if not os.path.isabs(directory):
            die(f"{where}: the directory must be an absolute path or start with ~")
        if not isinstance(entry, dict):
            die(f"{where} must be a table")
        cfg = _parse(entry, path, where)
        directory = os.path.realpath(directory)
        if (primary + "/").startswith(directory.rstrip("/") + "/") and (
                best is None or len(directory) > len(best[0])):
            best = (directory, cfg)
    return best[1] if best else Config()


def load(primary: str) -> Config:
    """The primary directory's settings: its own file if it has one, otherwise
    its entry in the user's file; dies if the file read is invalid."""
    primary = os.path.realpath(primary)
    path = os.path.join(primary, NAME)
    if os.path.isfile(path):
        return _parse(_read(path), path, path)
    return _user_entry(primary)
