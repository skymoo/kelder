# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Adam Mercer
"""The smol-claude command.

Only arguments after `self` are smol-claude's; everything else goes to
claude. `smol-claude -- self ...` gives claude a prompt starting with "self".

Each directory is attached at the same path it has on the host and detached
when the last session using it exits. Only one set of directories is attached
at a time, so Claude never sees a project it wasn't given; a second session
for a different set is refused (use VM=<name> to run it in a separate machine).
"""
import os
import re
import shlex
import signal
import subprocess
import sys
import time
from pathlib import Path

from . import smolvm
from .session import Session
from .smolvm import die, note

USAGE = """\
Usage: smol-claude [--] [CLAUDE_ARGS...]
       smol-claude self [-C DIR] [-a DIR]... [--] [CLAUDE_ARGS...]
       smol-claude self COMMAND

Run Claude Code in the smolvm guest with DIR (default: the current
directory) attached at the same path it has on the host. Only arguments
after `self` are smol-claude's own.

Options (after self):
  -C DIR   attach and start Claude in DIR
  -a DIR   also attach DIR and give Claude access to it (repeatable)
  --       end of smol-claude options; the rest goes to claude

Commands (after self):
  help       show this help
  setup      create and provision the machine (safe to re-run)
  smoketest  test the installed smolvm in a throwaway machine

Every other argument is passed to claude unchanged; run
`smol-claude --help` for Claude Code's options.

Environment:
  VM            smolvm machine name (default: smol; smol-test for
                self smoketest)
  CLAUDE_STATE  Claude's state directory on the host
                (default: ~/.local/share/smol-claude/claude)
  IMAGE         base image for a new machine (self setup;
                default: debian)
  NO_UPDATE     1 skips the weekly package update when a session
                boots the machine
  DRY_RUN       1 prints every smolvm call instead of running it
"""

COMMANDS = ("help", "setup", "smoketest")

# What runs in the guest. It gets the primary directory, the state directory,
# the number of extra directories, the extra directories, then claude's
# arguments. Extra directories are passed as --add-dir=DIR: claude's --add-dir
# takes several values, and the separate form would swallow a following prompt.
GUEST_SCRIPT = """
dir=$1 state=$2 n=$3; shift 3
extra=("${@:1:n}"); shift "$n"
for d in "$dir" "${extra[@]}"; do
  mountpoint -q "$d" || { echo "smol-claude: $d is not attached in the guest" >&2; exit 1; }
done
mountpoint -q "$state" || { echo "smol-claude: Claude state directory is not mounted at $state; run smol-claude self setup" >&2; exit 1; }
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
export GIT_CONFIG_COUNT=$i
# The directory is shared with the host, whose .venv points at the host's
# Python. Give uv in the guest an environment of its own in each project.
export UV_PROJECT_ENVIRONMENT=.venv-smol
# uv's cache is on the guest's disk and the environment is in the attached
# directory, so it can never hard-link between them; copy without warning.
export UV_LINK_MODE=copy
command -v claude >/dev/null || { echo "smol-claude: claude is not installed in the guest; run smol-claude self setup" >&2; exit 127; }
exec claude "${extra[@]/#/--add-dir=}" "$@"
"""


def parse_args(args):
    """Split the command line into (command, primary dir, extra dirs, claude's args).

    command is "run" or one of COMMANDS.
    """
    primary, extra = ".", []
    if args[:1] == ["self"]:
        sub = args[1] if len(args) > 1 else ""
        if sub in COMMANDS:
            return sub, primary, extra, []
        if sub not in ("-C", "-a"):
            die(f"unknown command or option 'self {sub}'; see 'smol-claude self help'")
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


def check_dir(path):
    """Resolve and check a directory to attach; returns its real path."""
    if not os.path.isdir(path):
        die(f"not a directory: {path}")
    d = os.path.realpath(path)
    if d in ("/", os.path.realpath(smolvm.HOME)):
        die(f"won't attach {d}; run smol-claude from a project directory")
    if ":" in d:
        die("can't attach a path containing ':'")
    state = os.path.realpath(smolvm.CLAUDE_STATE)
    if (d + "/").startswith(state + "/") or (state + "/").startswith(d + "/"):
        die(f"won't attach {d}: it overlaps Claude's state directory {state}")
    return d


def resume_command(dirs, since):
    """The smol-claude command that resumes the session that just ended, or None.

    Claude's own hint says `claude --resume ID`, which doesn't work on the
    host. The ID is the name of the newest transcript written since `since`
    in the state directory's folder for the primary directory.
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
    command = ["smol-claude", *(["self", *options, "--"] if options else []), "--resume", newest[1]]
    prefix = f"VM={shlex.quote(smolvm.VM)} " if smolvm.VM != "smol" else ""
    return prefix + shlex.join(command)


def _terminate(signum, _frame):
    """Turn a fatal signal into an exit, so cleanup still runs."""
    sys.exit(128 + signum)


def run_claude(primary, extra, claude_args):
    """Attach the directories, run claude in the guest, clean up; returns its exit status."""
    dirs = [check_dir(primary)]
    for d in extra:
        d = check_dir(d)
        if d not in dirs:
            dirs.append(d)

    if not smolvm.exists():
        die(f"machine '{smolvm.VM}' does not exist; run 'smol-claude self setup' first")

    from . import provision
    session = Session(dirs, on_boot=provision.update_if_stale)
    session.attach()
    signal.signal(signal.SIGTERM, _terminate)
    signal.signal(signal.SIGHUP, _terminate)
    # Let Ctrl-C reach Claude; smol-claude itself keeps going so cleanup still runs.
    signal.signal(signal.SIGINT, lambda *_: None)
    try:
        # Only ask for a TTY when there is one, so `smol-claude -p ... | less` and scripts work.
        flags = ["-i"]
        if sys.stdin.isatty() and sys.stdout.isatty():
            flags.append("-t")
        cmd = ["machine", "exec", "--name", smolvm.VM, *flags, "--", "bash", "-c", GUEST_SCRIPT, "_",
               dirs[0], smolvm.GUEST_CLAUDE_STATE, str(len(dirs) - 1), *dirs[1:], *claude_args]
        if smolvm.DRY_RUN:
            return smolvm.run(*cmd)[0]
        started = time.time() - 2   # allow for coarse file timestamps
        rc = subprocess.call([smolvm.SMOLVM_BIN, *cmd])
        if "-t" in flags:
            resume = resume_command(dirs, started)
            if resume:
                note(f"resume with: {resume}")
        return 128 - rc if rc < 0 else rc   # negative: killed by a signal
    finally:
        session.cleanup()


def main(argv=None):
    command, primary, extra, claude_args = parse_args(sys.argv[1:] if argv is None else argv)
    if command == "help":
        print(USAGE, end="")
        sys.exit(0)
    smolvm.require()
    if command == "setup":
        from . import provision
        provision.run()
    elif command == "smoketest":
        from . import smoketest
        smoketest.run()
    else:
        sys.exit(run_claude(primary, extra, claude_args))
