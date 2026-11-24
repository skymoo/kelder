# Changelog

All notable changes to kelder are documented in this file.

## 0.0.7 - 2026-11-24

Tools from a project's mise config are now installed in the project,
in `.mise-<machine>`, instead of in the machine. After upgrading from
0.0.6:

- Run `kelder self setup` once from each project with a mise config,
  to install its tools in the project.
- Tools installed by 0.0.6 stay in the machine's home directory,
  unused. Deleting and recreating the machine removes them.

### Added

- Install mise tools in the project

## 0.0.6 - 2026-11-20

This release replaces the backend: machines now run directly on
QEMU/KVM, and smolvm is no longer used or supported. Upgrading from
0.0.5 is not a drop-in change:

- The host needs `qemu-system-x86_64`, `qemu-img`, `virtiofsd`, `ssh`,
  `ssh-keygen` and one of `genisoimage`, `xorrisofs` or `mkisofs`
  instead of smolvm (see Install in the README).
- Existing smolvm machines are not converted. Run `kelder self setup`
  once for each machine to create it again, from each project that
  names a `vm` or lists `packages` in its `.kelder.toml`.
- Claude's state (settings, memory, sessions) is kept outside the
  machine and carries over unchanged.
- kelder no longer touches the old smolvm machines; remove them with
  smolvm once the new ones work.
- `kelder self smoketest` now tests the host's QEMU, and its
  `--release` option for trying a smolvm release is gone.

### Added

- Set project options in user config
- Add a QEMU/KVM machine backend
- Run self smoketest on QEMU machines
- Add self list, stop and delete commands

### Changed

- Split settings out of smolvm.py
- Route machine calls via machine.py
- Remove the smolvm backend

### Fixed

- Stop the machine setup started

## 0.0.5 - 2026-11-18

### Added

- Install a project's mise.toml tools

### Fixed

- Let libgit2 tools open attached repos
- Stream setup and smoketest output live

## 0.0.4 - 2026-11-17

### Added

- Smoketest smolvm releases via --release
- Add self shell for a shell in the guest

### Fixed

- Remove mount points on detach

## 0.0.3 - 2026-11-16

### Added

- Add per-project .kelder.toml
- Name guest venv after the machine
- Use a gitignore from the state dir
- Add memory key to .kelder.toml

## 0.0.2 - 2026-11-16

### Changed

- Rename default machine to kelder

## 0.0.1 - 2026-11-16

Initial release: run Claude Code in a smolvm guest with host directories attached.
