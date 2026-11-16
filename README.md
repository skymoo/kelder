# smol-claude: Claude Code in a smolvm guest

Claude Code runs inside a minimal smolvm VM (Debian, nested KVM). It can start
its own VMs (`testvm`) and containers (`podman`) there to run tests.

## Install

Needs, on the host: smolvm 1.32 or newer, KVM with nested virtualization,
Python 3.11 or newer and bash. smol-claude itself has no Python dependencies.

```sh
uv tool install .                 # from a checkout; puts smol-claude on PATH
smol-claude self setup            # create and provision the machine (safe to re-run)
```

`uv tool uninstall smol-claude` removes it again. From a checkout it also runs
without installing, as `uv run smol-claude`.

```text
src/smol_claude/cli.py          the smol-claude command: arguments, running claude in the guest
src/smol_claude/session.py      which directories are attached, and by which sessions
src/smol_claude/smolvm.py       settings and every smolvm CLI assumption
src/smol_claude/provision.py    create and provision the guest (smol-claude self setup)
src/smol_claude/smoketest.py    test a smolvm upgrade (smol-claude self smoketest)
src/smol_claude/guest/          files setup copies into the guest:
  testvm                          throwaway QEMU/KVM VMs (/usr/local/bin)
  smoketest                       checks smolvm behaviour after an upgrade (/usr/local/bin)
  containers-tsi.conf             podman DNS workaround (see below)
```

## Usage

```sh
cd ~/git/app && smol-claude                  # attach ~/git/app and start Claude there
smol-claude self -C ~/git/other              # or name the directory
cd ~/git/app && smol-claude self -a ../lib   # also give Claude ~/git/lib
smol-claude -p "run the tests" | less        # non-interactive works too
```

```text
smol-claude [--] [CLAUDE_ARGS...]
smol-claude self [-C DIR] [-a DIR]... [--] [CLAUDE_ARGS...]
smol-claude self help
smol-claude self setup
smol-claude self version
```

Only arguments after `self` (which must come first) are `smol-claude`'s
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
- `self help`: show `smol-claude`'s own usage.
- `self version`: show `smol-claude`'s version.
- `self setup`: create and provision the machine; safe to re-run, it
  updates packages, config and `testvm` on an existing machine.
- `self smoketest`: test the installed smolvm in a throwaway machine (see
  [Testing a smolvm upgrade](#testing-a-smolvm-upgrade)).

Without `self`, every argument goes to `claude`. To give `claude` a
prompt starting with "self", use `smol-claude -- self ...`.

Everything else is passed unchanged to `claude` in the guest, so `-p`,
`--resume`, `--continue`, `--model` and the rest are Claude Code's own
options (see `smol-claude --help`, which shows `claude --help`).

These environment variables set by the host also change how it behaves:

- `VM=<name>`: the smolvm machine to use (default `smol`); also read by
  `smol-claude self setup`.
- `CLAUDE_STATE=<dir>`: where Claude's state lives on the host (default
  `~/.local/share/smol-claude/claude`).
- `IMAGE=<image>`: base image for `smol-claude self setup` (default `debian`).
- `NO_UPDATE=1`: skip the weekly package update (below) for this run.
- `DRY_RUN=1`: print every `smolvm` call instead of running it.

One machine serves every project. `smol-claude` starts it if needed and
attaches each directory to it (`smolvm machine mount add`) at the same path
it has on the host, and detaches it when the last session using it exits. Only
one set of directories is attached at a time: a session for a different set is
refused while another is open, since mounts are visible machine-wide (use
`VM=<name>` for a second machine). A session that crashes leaves its
directories attached until the next run cleans them up.

The session that boots the machine also keeps it up to date: if the guest
was last updated more than a week ago, it upgrades the packages, uv and
Claude Code before starting Claude (`smol-claude: updating machine 'smol'
...`), which can take a minute. A failed update is reported and the session
starts anyway.
`smol-claude self setup` does the same update whenever it runs.

When the last session exits, `smol-claude` stops the machine, so anything
left running in it (`testvm` VMs, containers) stops too. The next session
boots it again.

Claude's state (login, settings, history, memory) is kept on the host in
`~/.local/share/smol-claude/claude` (override with `CLAUDE_STATE`), mounted at
`/var/lib/smol-claude` in the machine, with `CLAUDE_CONFIG_DIR` pointing there.
The machine itself is disposable: delete it and run `smol-claude self setup` to
rebuild, and nothing is lost. Changing `IMAGE` works the same way.

Sessions start with `ANTHROPIC_API_KEY` and `ANTHROPIC_AUTH_TOKEN` unset:
either would outrank the stored login and bill the Console instead of the
subscription.

Arguments go straight to `claude`, so sessions resume as usual. When Claude
exits it suggests `claude --resume <id>`; run `smol-claude --resume <id>`
instead, from the same directory (sessions are filed by the primary
directory, which matches the host path). `smol-claude --continue` and a bare
`smol-claude --resume` (to pick from a list) work too. Plain `claude` on the
host won't find these sessions, because they live in the smol-claude state
directory, not `~/.claude`.

The guest has `uv`, `make` and `shellcheck` for running a project's own
checks. The attached directory is shared with the host, whose `.venv` points
at the host's Python, so sessions set `UV_PROJECT_ENVIRONMENT=.venv-smol`:
`uv run` in the guest builds and uses `.venv-smol` in each project and leaves
`.venv` to the host.

In the guest:

```sh
testvm run -- 'apt-get update && apt-get install -y nginx'   # fresh VM, destroyed after
testvm up web -p 8080:80 && testvm ssh web                   # keep one around
testvm cp ./build web:/opt/ && testvm down web
podman run --rm docker.io/library/debian:trixie ...          # use full image names
```

## Testing a smolvm upgrade

Exit every smol-claude session so `smol` is stopped, upgrade smolvm, then run
`smol-claude self smoketest` from a project directory. It creates a throwaway
`smol-test` machine (`VM=<name>` picks another name), runs the guest's
`smoketest` in it with the current directory attached, checks the smolvm
behaviour relied on (see below) from the host, and deletes the machine if
everything passed. FAIL means the upgrade broke something smol-claude needs;
KNOWN is a smolvm bug the guest config works around; CHANGED means that bug
is gone and its workaround may be removable.

A machine that fails is kept for inspection. If `smol` itself stops starting
after an upgrade, keep it too and create the replacement under another name.

## Development

```sh
uv run pytest         # the tests
uv run ruff check     # lint
```

The tests need neither smolvm nor a network. The unit tests cover argument
parsing and the attachment bookkeeping. The rest run `smol-claude` against
`tests/fake_smolvm.py`, a stand-in selected with `SMOLVM_BIN` that records
each call and runs `exec` commands locally with a fake `claude`; they check
the `smolvm` calls made and the environment `claude` would get. They are
plain `unittest` tests, so `PYTHONPATH=src python3 -m unittest discover -s
tests` runs them without uv.

## Quirks found while setting this up

**Nested VMs must boot with UEFI.** Under smolvm's nested KVM (AMD host), a
cloud image booted through BIOS GRUB resets the moment GRUB jumps into the
kernel. The same kernel boots fine when QEMU loads it directly, and the image
boots fine with OVMF. `testvm` always uses OVMF, so `ovmf` is a required package.

**UDP inside containers.** The guest has no real NIC: libkrun's TSI
(`tsi_hijack`) proxies every socket to the host. Inside a container network
namespace, a *connected* UDP socket can send only one datagram; later sends
fail with `EINVAL`. Unconnected `sendto` works, and the guest's own namespace
is unaffected. glibc sends DNS A and AAAA queries on one connected socket, so
container DNS broke (apt: "Temporary failure resolving"). The
`single-request-reopen` resolver option fixes DNS. Other UDP-heavy workloads in
containers (QUIC, statsd, syslog) may need `--network host`. This is a libkrun
bug, still present in smolvm 1.35.0, worth reporting upstream.

**smolvm behaviour relied on** (checked against the v1.34.4 source): `machine
status` exits 0 for a running or stopped machine and fails for an unknown one
(an unreadable state database is also reported as not found); `machine start`
on a running machine prints "already running" and succeeds, and fails for a
paused one; `machine exec` takes `-i` and `-t` as separate flags.

## License

Copyright (C) 2026 Adam Mercer. smol-claude is free software, licensed under
the GNU General Public License, version 3 or (at your option) any later
version. See [LICENSE](LICENSE).
