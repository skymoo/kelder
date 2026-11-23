# Usage

## Commands

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
  throwaway machine (see
  [Testing the host's QEMU](machine.md#testing-the-hosts-qemu)).
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

## Sessions and the machine

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

## Claude's state

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

## Resuming sessions

Arguments go straight to `claude`, so sessions resume as usual. When Claude
exits it suggests `claude --resume <id>`; run `kelder --resume <id>`
instead, from the same directory (sessions are filed by the primary
directory, which matches the host path). `kelder --continue` and a bare
`kelder --resume` (to pick from a list) work too. Plain `claude` on the
host won't find these sessions, because they live in the kelder state
directory, not `~/.claude`.
