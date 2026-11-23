# Development

## Source layout

```text
src/kelder/cli.py          the kelder command: arguments, running claude in the guest
src/kelder/session.py      which directories are attached, and by which sessions
src/kelder/config.py       a project's .kelder.toml, and the user's config.toml
src/kelder/settings.py     settings shared by every module
src/kelder/machine.py      the machine, as the rest of kelder sees it
src/kelder/qemu.py         the machine itself: QEMU, virtiofsd and ssh
src/kelder/provision.py    create and provision the guest (kelder self setup)
src/kelder/smoketest.py    test the host's QEMU (kelder self smoketest)
src/kelder/machines.py     list, stop and delete machines (kelder self list, stop, delete)
src/kelder/mise.py         tools a project lists in its mise.toml
src/kelder/guest/          files setup copies into the guest:
  testvm                     throwaway QEMU/KVM VMs (/usr/local/bin)
  smoketest                  checks the machine's behaviour (/usr/local/bin)
```

## Tests

```sh
uv run pytest         # the tests
uv run ruff check     # lint
uv run ty check       # type check
```

The tests need neither QEMU nor a network. The unit tests cover argument
parsing, the attachment bookkeeping and the commands kelder builds for
QEMU. The rest run `kelder` against `tests/fake_qemu.py`, which stands in
for QEMU, virtiofsd and ssh when linked into a directory at the front of
`PATH`: it records each call and runs the guest's commands locally with a
fake `claude`. They check what is started and plugged in, and the
environment `claude` would get. They are plain `unittest` tests, so
`PYTHONPATH=src python3 -m unittest discover -s tests` runs them without
uv.

## Releasing

With `X.Y.Z` as the new version, from an up-to-date `main`:

```sh
uv version X.Y.Z
uv run git-cliff --unreleased --tag vX.Y.Z --prepend CHANGELOG.md
git commit -am "chore(release): bump version to X.Y.Z"
git tag -a vX.Y.Z -m "kelder X.Y.Z"
git push origin main vX.Y.Z
```

- `uv version` sets the version in `pyproject.toml` and `uv.lock`. Choose
  the number by hand: while the version is 0.0.x anything may change in a
  patch release, from 0.1.0 a minor bump means a breaking change, and
  from 1.0 the project follows semantic versioning.
- `git-cliff` adds one section to `CHANGELOG.md` for the commits since the
  last tag, under the header, and leaves the earlier sections as they
  are. Only `feat`, `fix`, `refactor` and `perf` commits are listed (see
  `cliff.toml`), so the release commit itself never appears.
- Pushing the tag starts the Release workflow, which fails unless the tag
  is the version in `pyproject.toml` prefixed with `v`, then builds the
  distributions and publishes them to PyPI.
