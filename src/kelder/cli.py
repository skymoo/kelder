# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Adam Mercer
"""The kelder command.

Only arguments after `self` are kelder's; everything else goes to
claude. `kelder -- self ...` gives claude a prompt starting with "self".

Each directory is attached at the same path it has on the host and detached
when the last session using it exits. Only one set of directories is attached
at a time, so Claude never sees a project it wasn't given; a second session
for a different set is refused (a project's .kelder.toml, or VM=<name>, runs it
in a separate machine).
"""
import importlib.metadata
import json
import os
import re
import shlex
import signal
import subprocess
import sys
import time
from pathlib import Path
from types import FrameType
from typing import NoReturn

from . import config, smolvm
from .session import Session
from .smolvm import die, note

USAGE = """\
Usage: kelder [--] [CLAUDE_ARGS...]
       kelder self [-C DIR] [-a DIR]... [--] [CLAUDE_ARGS...]
       kelder self COMMAND
       kelder self smoketest [--release VERSION]

Run Claude Code in the smolvm guest with DIR (default: the current
directory) attached at the same path it has on the host. Only arguments
after `self` are kelder's own.

Options (after self):
  -C DIR   attach and start Claude in DIR
  -a DIR   also attach DIR and give Claude access to it (repeatable)
  --       end of kelder options; the rest goes to claude

Commands (after self):
  help       show this help
  setup      create and provision the machine (safe to re-run)
  smoketest  test the installed smolvm in a throwaway machine; with
             --release VERSION, test that smolvm release instead,
             without installing it
  version    show kelder's version

Every other argument is passed to claude unchanged; run
`kelder --help` for Claude Code's options.

Project file (.kelder.toml in DIR):
  vm = "NAME"         the smolvm machine this project runs in
  packages = [...]    extra packages self setup installs in it
  memory = "NAME"     the memory folder its sessions use

Environment:
  VM            smolvm machine name; overrides the project file
                (default: kelder; kelder-test for self smoketest)
  CLAUDE_STATE  Claude's state directory on the host
                (default: ~/.local/share/kelder/claude)
  IMAGE         base image for a new machine (self setup;
                default: debian)
  NO_UPDATE     1 skips the weekly package update when a session
                boots the machine
  DRY_RUN       1 prints every smolvm call instead of running it
"""

COMMANDS = ("help", "setup", "smoketest", "version")

# What runs in the guest. It gets the primary directory, the state directory,
# the name of uv's project environment, the number of extra directories, the
# extra directories, then claude's arguments. Extra directories are passed as
# --add-dir=DIR: claude's --add-dir takes several values, and the separate
# form would swallow a following prompt.
GUEST_SCRIPT = """
dir=$1 state=$2 venv=$3 n=$4; shift 4
extra=("${@:1:n}"); shift "$n"
for d in "$dir" "${extra[@]}"; do
  mountpoint -q "$d" || { echo "kelder: $d is not attached in the guest" >&2; exit 1; }
done
mountpoint -q "$state" || { echo "kelder: Claude state directory is not mounted at $state; run kelder self setup" >&2; exit 1; }
cd "$dir"
# An API key or bearer token in the environment outranks the stored login
# and would silently bill the Console instead of the subscription.
unset ANTHROPIC_API_KEY ANTHROPIC_AUTH_TOKEN
export PATH="$HOME/.local/bin:$PATH" CLAUDE_CONFIG_DIR=$state
# The session is root but attached directories belong to the host user, so
# git would refuse them as "dubious ownership". Mark them, and repositories
# below them, as safe for this session only.
i=0
for d in "$dir" "${extra[@]}"; do
  for v in "$d" "$d/*"; do
    export "GIT_CONFIG_KEY_$i=safe.directory" "GIT_CONFIG_VALUE_$i=$v"
    i=$((i + 1))
  done
done
# The host user's global gitignore is not visible in the guest. A gitignore
# file in the state directory stands in for it, so files ignored on the
# host do not show up as untracked here.
if [[ -f $state/gitignore ]]; then
  export "GIT_CONFIG_KEY_$i=core.excludesFile" "GIT_CONFIG_VALUE_$i=$state/gitignore"
  i=$((i + 1))
fi
export GIT_CONFIG_COUNT=$i
# The directory is shared with the host, whose .venv points at the host's
# Python. Give uv in the guest an environment of its own in each project,
# named after the machine: one built in another machine points at that
# machine's Python.
export UV_PROJECT_ENVIRONMENT=$venv
# uv's cache is on the guest's disk and the environment is in the attached
# directory, so it can never hard-link between them; copy without warning.
export UV_LINK_MODE=copy
command -v claude >/dev/null || { echo "kelder: claude is not installed in the guest; run kelder self setup" >&2; exit 127; }
exec claude "${extra[@]/#/--add-dir=}" "$@"
"""

# Prints each of the packages given that isn't installed in the guest.
MISSING_PACKAGES = """
for p in "$@"; do
  [[ $(dpkg-query -W -f '${db:Status-Status}' "$p" 2>/dev/null) == installed ]] || echo "$p"
done
"""


def parse_args(args: list[str]) -> tuple[str, str, list[str], list[str]]:
    """Split the command line into (command, primary dir, extra dirs, claude's args).

    command is "run" or one of COMMANDS; for a command the last item is the
    command's own arguments.
    """
    # Parsed by hand, not with argparse: most of the command line is claude's,
    # and argparse takes every dash argument as its own. It rejects
    # `self -a DIR --resume ID` as an unrecognized option unless claude's
    # arguments always follow `--`, and parse_known_args would keep looking
    # for -C and -a among claude's arguments.
    primary, extra = ".", []
    if args[:1] == ["self"]:
        sub = args[1] if len(args) > 1 else ""
        if sub in COMMANDS:
            return sub, primary, extra, args[2:]
        if sub not in ("-C", "-a"):
            die(f"unknown command or option 'self {sub}'; see 'kelder self help'")
        args = args[1:]
        while args:
            if args[0] == "-C":
                if len(args) < 2:
                    die("-C needs a directory")
                primary, args = args[1], args[2:]
            elif args[0] == "-a":
                if len(args) < 2:
                    die("-a needs a directory")
                extra.append(args[1])
                args = args[2:]
            elif args[0] == "--":
                args = args[1:]
                break
            else:
                break
    elif args[:1] == ["--"]:
        args = args[1:]
    return "run", primary, extra, args


def check_dir(path: str) -> str:
    """Resolve and check a directory to attach; returns its real path."""
    if not os.path.isdir(path):
        die(f"not a directory: {path}")
    d = os.path.realpath(path)
    if d in ("/", os.path.realpath(smolvm.HOME)):
        die(f"won't attach {d}; run kelder from a project directory")
    if ":" in d:
        die("can't attach a path containing ':'")
    state = os.path.realpath(smolvm.CLAUDE_STATE)
    if (d + "/").startswith(state + "/") or (state + "/").startswith(d + "/"):
        die(f"won't attach {d}: it overlaps Claude's state directory {state}")
    return d


def resume_command(dirs: list[str], since: float, default_vm: str = "kelder") -> str | None:
    """The kelder command that resumes the session that just ended, or None.

    Claude's own hint says `claude --resume ID`, which doesn't work on the
    host. The ID is the name of the newest transcript written since `since`
    in the state directory's folder for the primary directory. default_vm is
    the machine the primary directory gets when VM isn't set.
    """
    projects = Path(smolvm.CLAUDE_STATE) / "projects"
    # Claude names the folder after the directory with other characters
    # replaced by "-"; try that and the "/"-only form, whichever exists.
    folders = {re.sub(r"[^A-Za-z0-9]", "-", dirs[0]), dirs[0].replace("/", "-")}
    newest = None
    for folder in folders:
        for transcript in (projects / folder).glob("*.jsonl"):
            try:
                mtime = transcript.stat().st_mtime
            except OSError:
                continue
            if mtime >= since and (newest is None or mtime > newest[0]):
                newest = (mtime, transcript.stem)
    if newest is None:
        return None
    options = []
    if dirs[0] != os.path.realpath("."):
        options += ["-C", dirs[0]]
    for d in dirs[1:]:
        options += ["-a", d]
    command = ["kelder", *(["self", *options, "--"] if options else []), "--resume", newest[1]]
    prefix = f"VM={shlex.quote(smolvm.VM)} " if smolvm.VM != default_vm else ""
    return prefix + shlex.join(command)


def _terminate(signum: int, _frame: FrameType | None) -> NoReturn:
    """Turn a fatal signal into an exit, so cleanup still runs."""
    sys.exit(128 + signum)


def note_missing(cfg: config.Config) -> None:
    """Say which of the project's packages the machine lacks; setup installs them."""
    missing = smolvm.guest("bash", "-c", MISSING_PACKAGES, "_", *cfg.packages, capture="out")[1].split()
    if missing:
        note(f"machine '{smolvm.VM}' lacks packages listed in {cfg.path}: {' '.join(missing)}; "
             "run 'kelder self setup' to install them")


def run_claude(primary: str, extra: list[str], claude_args: list[str],
               cfg: config.Config | None = None) -> int:
    """Attach the directories, run claude in the guest, clean up; returns its exit status.

    cfg is the primary directory's project file, if it has one.
    """
    cfg = cfg or config.Config()
    if cfg.memory:
        # Claude takes only the last --settings it is given, so one of the
        # user's would silently replace this one, or this one theirs.
        for arg in claude_args:
            if arg == "--":
                break
            if arg == "--settings" or arg.startswith("--settings="):
                die(f"--settings cannot be used with memory in {cfg.path}: Claude takes only one "
                    "--settings, and memory is passed as one")
        # Claude files memory under the project's path unless told otherwise.
        # The folder is named by its path in the guest; the "=" form keeps a
        # following prompt from being read as the option's value.
        folder = f"{smolvm.GUEST_CLAUDE_STATE}/memory/{cfg.memory}"
        claude_args = [f"--settings={json.dumps({'autoMemoryDirectory': folder})}", *claude_args]
    dirs = [check_dir(primary)]
    for d in extra:
        d = check_dir(d)
        if d not in dirs:
            dirs.append(d)

    if not smolvm.exists():
        die(f"machine '{smolvm.VM}' does not exist; run 'kelder self setup' first")

    from . import provision
    session = Session(dirs, on_boot=provision.update_if_stale)
    session.attach()
    signal.signal(signal.SIGTERM, _terminate)
    signal.signal(signal.SIGHUP, _terminate)
    # Let Ctrl-C reach Claude; kelder itself keeps going so cleanup still runs.
    signal.signal(signal.SIGINT, lambda *_: None)
    try:
        if cfg.packages:
            note_missing(cfg)
        # Only ask for a TTY when there is one, so `kelder -p ... | less` and scripts work.
        flags = ["-i"]
        if sys.stdin.isatty() and sys.stdout.isatty():
            flags.append("-t")
        cmd = ["machine", "exec", "--name", smolvm.VM, *flags, "--", "bash", "-c", GUEST_SCRIPT, "_",
               dirs[0], smolvm.GUEST_CLAUDE_STATE, f".venv-{smolvm.VM}",
               str(len(dirs) - 1), *dirs[1:], *claude_args]
        if smolvm.DRY_RUN:
            return smolvm.run(*cmd)[0]
        started = time.time() - 2   # allow for coarse file timestamps
        rc = subprocess.call([smolvm.SMOLVM_BIN, *cmd])
        if "-t" in flags:
            resume = resume_command(dirs, started, cfg.vm or "kelder")
            if resume:
                note(f"resume with: {resume}")
        return 128 - rc if rc < 0 else rc   # negative: killed by a signal
    finally:
        session.cleanup()


def main(argv: list[str] | None = None) -> None:
    command, primary, extra, rest = parse_args(sys.argv[1:] if argv is None else argv)
    if command == "help":
        print(USAGE, end="")
        sys.exit(0)
    if command == "version":
        print("kelder", importlib.metadata.version("kelder"))
        sys.exit(0)
    if command == "smoketest":
        from . import smoketest
        smoketest.run(rest)
        return
    smolvm.require()
    # The project file names the machine unless VM does.
    cfg = config.load(primary)
    if cfg.vm and not smolvm.env("VM", ""):
        smolvm.VM = cfg.vm
    if command == "setup":
        from . import provision
        if cfg.path:
            print(f"Using {cfg.path} for machine '{smolvm.VM}'.")
        provision.run(cfg.packages)
    else:
        sys.exit(run_claude(primary, extra, rest, cfg))
