# Changelog

All notable changes to kelder are documented in this file.

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
