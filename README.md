# kelder: Claude Code in a QEMU/KVM guest

Claude Code runs inside a minimal virtual machine (Debian 13 under QEMU,
with nested KVM), as a user with your uid. It can start its own VMs
(`testvm`) and containers (`podman`) there to run tests.

## Install

Needs, on an x86_64 Linux host: `qemu-system-x86_64`, `qemu-img`,
`virtiofsd`, `ssh`, `ssh-keygen`, one of `genisoimage`, `xorrisofs` or
`mkisofs`, access to `/dev/kvm` with nested virtualization, and Python 3.11
or newer. kelder itself has no Python dependencies.

```sh
uv tool install kelder            # puts kelder on PATH
kelder self setup                 # create and provision the machine (safe to re-run)
```

`uv tool uninstall kelder` removes it again. From a checkout, `uv tool
install .` installs that copy instead, and `uv run kelder` runs it without
installing.

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

## Usage

```sh
cd ~/git/app && kelder                  # attach ~/git/app and start Claude there
kelder self -C ~/git/other              # or name the directory
cd ~/git/app && kelder self -a ../lib   # also give Claude ~/git/lib
kelder -p "run the tests" | less        # non-interactive works too
kelder self shell                       # a shell in the guest instead of Claude
```

```text
kelder [--] [CLAUDE_ARGS...]
kelder self [-C DIR] [-a DIR]... [--] [CLAUDE_ARGS...]
kelder self shell [-C DIR] [-a DIR]...
kelder self delete NAME
kelder self help
kelder self list
kelder self setup
kelder self smoketest
kelder self stop [NAME]
kelder self version
```

Only arguments after `self` (which must come first) are `kelder`'s
own:

- `self -C DIR`: attach and start Claude in `DIR` instead of the current
  directory.
- `self -a DIR`: also attach `DIR` and give Claude access to it (passed
  to `claude` as `--add-dir=DIR`). Repeat it for more directories. The
  directory Claude starts in stays the primary one: memory and sessions
  are filed under it.
- `-C` and `-a` can be given in any order and combined; they end at
  `--` or at the first argument that isn't one of them, and the rest
  goes to `claude`.
- `self shell`: open a shell in the guest instead of starting Claude. It
  is a session like any other: `-C` and `-a` attach the same directories,
  the shell starts in the primary one with the environment Claude gets
  (`~/.local/bin` on `PATH`, the git and uv settings), and the machine
  stays up until the last session exits. A project's `memory` key has no
  effect on it, as that is an argument to `claude`.
- `self help`: show `kelder`'s own usage.
- `self version`: show `kelder`'s version.
- `self setup`: create and provision the machine; safe to re-run, it
  updates packages, config and `testvm` on an existing machine. If it
  had to start the machine, it stops it again when it is done.
- `self smoketest`: test the host's QEMU, virtiofsd and ssh in a
  throwaway machine (see [Testing the host's QEMU](#testing-the-hosts-qemu)).
- `self list`: list the machines, with whether each is running, the size
  of its disk on the host and the directories attached to it.
- `self stop [NAME]`: stop the machine `NAME`, or the one this project
  uses. For a machine left running by a session that was killed, or one
  that has hung; it is refused while a session is using the machine.
- `self delete NAME`: stop the machine `NAME` and delete it, disk and
  all, after asking you to confirm. Claude's state is not in the machine,
  so nothing of that is lost. It is refused while a session is using the
  machine.

Without `self`, every argument goes to `claude`. To give `claude` a
prompt starting with "self", use `kelder -- self ...`.

Everything else is passed unchanged to `claude` in the guest, so `-p`,
`--resume`, `--continue`, `--model` and the rest are Claude Code's own
options (see `kelder --help`, which shows `claude --help`).

A project can have a `.kelder.toml` in the directory Claude starts in:

```toml
vm = "homelab"
packages = [
    "ansible-lint",
    "zfsutils-linux",
]
memory = "homelab"
```

- `vm`: the machine this project runs in, instead of `kelder`.
  Projects with different machines can have sessions open at the same
  time. Each machine is set up once, with `kelder self setup` run from
  the project.
- `packages`: extra Debian packages `kelder self setup`, run from the
  project, installs in that machine. Only setup installs them: after
  adding one, run setup again. A session in a machine that lacks any of
  them says so and starts anyway. Projects that share a machine each add
  their own; a package taken off the list stays installed.
- `memory`: the folder Claude's memory for this project is kept in,
  `memory/<name>` in the state directory. Without it Claude Code files
  memory under the project's path, so it is left behind when the
  directory moves. Projects that give the same name share one memory.
  It reaches Claude as `--settings` with `autoMemoryDirectory`; session
  transcripts are still filed under the project's path. Claude takes
  only one `--settings`, so a session given one of your own is refused
  in a project with this key.

Every key is optional. Only the directory Claude starts in is checked: a
file in a directory above it, or in one added with `-a`, is ignored.

A project that cannot carry a `.kelder.toml` (a repository shared with
people who do not use kelder, say) can get the same keys from your own
file, `~/.config/kelder/config.toml` (under `$XDG_CONFIG_HOME` if set):

```toml
[projects."~/git/work"]
vm = "work"
memory = "work"
```

An entry covers its directory and every directory below it; when several
match, the longest path is used. The directory is an absolute path or
starts with `~`. A project's own `.kelder.toml` replaces the entry
outright, even an empty one: keys are never combined from both files.

These environment variables set by the host also change how it behaves:

- `VM=<name>`: the machine to use (default `kelder`), overriding
  the project's `.kelder.toml`; also read by `kelder self setup`.
- `CLAUDE_STATE=<dir>`: where Claude's state lives on the host (default
  `~/.local/share/kelder/claude`).
- `NO_UPDATE=1`: skip the weekly package update (below) for this run.
- `DRY_RUN=1`: print the commands that would start, change and enter the
  machine instead of running them.

One machine serves every project. `kelder` starts it if needed and
attaches each directory to it (a virtiofs device plugged into the running
machine) at the same path it has on the host, and detaches it when the
last session using it exits,
removing the empty directory the mount leaves behind in the guest. Only
one set of directories is attached at a time: a session for a different set is
refused while another is open, since mounts are visible machine-wide (give
the project its own machine in `.kelder.toml`, or use `VM=<name>`). A session
that crashes leaves its directories attached until the next run cleans them up.

The session that boots the machine also keeps it up to date: if the guest
was last updated more than a week ago, it upgrades the packages, uv and
Claude Code before starting Claude (`kelder: updating machine 'kelder'
...`), which can take a minute. A failed update is reported and the session
starts anyway.
`kelder self setup` does the same update whenever it runs.

When the last session exits, `kelder` stops the machine, so anything
left running in it (`testvm` VMs, containers) stops too. The next session
boots it again.

Claude's state (login, settings, history, memory) is kept on the host in
`~/.local/share/kelder/claude` (override with `CLAUDE_STATE`), mounted at
`/var/lib/kelder` in the machine, with `CLAUDE_CONFIG_DIR` pointing there.
The machine itself is disposable: delete it and run `kelder self setup` to
rebuild, and nothing is lost.

Git in the machine does not see your global gitignore. Put the patterns
you want ignored there in a `gitignore` file in the state directory
(`~/.local/share/kelder/claude/gitignore`); sessions point git's
`core.excludesFile` at it when it exists.

Sessions start with `ANTHROPIC_API_KEY` and `ANTHROPIC_AUTH_TOKEN` unset:
either would outrank the stored login and bill the Console instead of the
subscription.

Arguments go straight to `claude`, so sessions resume as usual. When Claude
exits it suggests `claude --resume <id>`; run `kelder --resume <id>`
instead, from the same directory (sessions are filed by the primary
directory, which matches the host path). `kelder --continue` and a bare
`kelder --resume` (to pick from a list) work too. Plain `claude` on the
host won't find these sessions, because they live in the kelder state
directory, not `~/.claude`.

The guest has `uv`, `make` and `shellcheck` for running a project's own
checks. The attached directory is shared with the host, whose `.venv` points
at the host's Python, so sessions set `UV_PROJECT_ENVIRONMENT` to `.venv-`
followed by the machine's name (`.venv-kelder` in the default machine):
`uv run` in the guest builds and uses that environment in each project and
leaves `.venv` to the host. Each machine gets its own.

In the guest:

```sh
testvm run -- 'apt-get update && apt-get install -y nginx'   # fresh VM, destroyed after
testvm up web -p 8080:80 && testvm ssh web                   # keep one around
testvm cp ./build web:/opt/ && testvm down web
podman run --rm docker.io/library/debian:trixie ...          # use full image names
```

## Tools

Tools Debian doesn't package, or packages too old, come from
[mise](https://mise.jdx.dev), which every machine has. A project lists
them in its own `mise.toml`, the same file mise reads on the host and in
CI:

```toml
[tools]
zig = "0.15"
rust = "1.91"
```

- `kelder self setup`, run from the project, attaches it and runs `mise
  install`. As with `packages`, only setup installs: a session in a
  machine that lacks one of the project's tools says so and starts
  anyway, and a shim for a tool that isn't installed fails, or runs
  Debian's copy if there is one, rather than downloading it mid-session
  (`MISE_AUTO_INSTALL` and `MISE_NOT_FOUND_AUTO_INSTALL` are `false`).
- Sessions have mise's shims (`~/.local/share/mise/shims`) first on
  `PATH`. A shim picks the version from the directory it is run in, so
  projects sharing a machine can ask for different versions, and
  `command -v` finds a shim even where the tool isn't installed (`mise
  which` tells).
- The directories a session attaches are trusted
  (`MISE_TRUSTED_CONFIG_PATHS`), so a config with `[env]` or templates
  works without `mise trust`.
- mise comes from its Debian repository, which setup and the weekly
  update add if it is missing, so the package upgrade keeps it current.
  The tools themselves stay at the versions the project asks for.
- Debian packages a tool needs (a C compiler for Rust, say) go in
  `packages` in `.kelder.toml`.

## The machine

The machine is a Debian 13 cloud image run by QEMU with KVM as your own
user, using what the host's distribution packages: virtiofsd shares the
attached directories and sessions go over ssh to a port on localhost.

- Sessions run as the guest user `kelder`, not root. It has your uid and
  gid, so files in attached directories belong to the same user on both
  sides, and it may `sudo` without a password. `podman` is rootless
  unless run with `sudo`. Run as root, kelder uses root in the guest too.
- It gets half the host's memory, up to 8 GiB, up to 4 CPUs and a 20 GB
  disk. Memory the guest has used is not returned to the host until the
  machine stops.
- Each attached directory takes a PCIe port, and those are fixed when the
  machine starts. A session that boots the machine gets as many as it
  attaches. One that finds it running with too few, as after a session
  that was killed, restarts it first (`kelder: restarting machine ...`);
  that only happens when no other session is in it.

The machine's disk, ssh key and cloud-init seed are in
`~/.local/share/kelder/machines/<name>`, and the image it was copied from
in `~/.cache/kelder/images`. While it runs, its console log and one log
for each attached directory's virtiofsd are in
`$XDG_RUNTIME_DIR/kelder/<name>/qemu`. `kelder self list` shows the
machines there are, and `kelder self delete <name>` removes one.

## Testing the host's QEMU

After the host's QEMU or virtiofsd has been upgraded, run `kelder self
smoketest` from a project directory. It creates a throwaway `kelder-test`
machine (`VM=<name>` picks another name), provisions it as `kelder self
setup` would, runs the guest's `smoketest` in it with the current directory
attached, and deletes the machine if everything passed. The guest script
checks the session user, `sudo`, the ownership of files it creates in the
attached directory, UDP and DNS in a container, and that `testvm` boots a
nested VM; each line is PASS or FAIL.

A machine that fails is kept for inspection, and the command that removes
it is printed.

## Development

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

## Quirks found while setting this up

**Nested VMs boot with UEFI.** `testvm` always boots with OVMF, so `ovmf`
is a required package in the guest. It was chosen when a cloud image
booted through BIOS GRUB reset the moment GRUB jumped into the kernel
under nested KVM (AMD host), in the smolvm guest kelder used before;
whether BIOS would work in today's machine has not been tried.

## License

Copyright (C) 2026 Adam Mercer. kelder is free software, licensed under
the GNU General Public License, version 3 or (at your option) any later
version. See [LICENSE](LICENSE).
