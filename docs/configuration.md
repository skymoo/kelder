# Configuration

## .kelder.toml

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

## Your own config.toml

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

## Environment variables

These environment variables set by the host also change how it behaves:

- `VM=<name>`: the machine to use (default `kelder`), overriding
  the project's `.kelder.toml`; also read by `kelder self setup`.
- `CLAUDE_STATE=<dir>`: where Claude's state lives on the host (default
  `~/.local/share/kelder/claude`).
- `NO_UPDATE=1`: skip the weekly package update (see
  [Sessions and the machine](usage.md#sessions-and-the-machine)) for this
  run.
- `DRY_RUN=1`: print the commands that would start, change and enter the
  machine instead of running them.
