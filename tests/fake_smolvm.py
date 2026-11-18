#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Adam Mercer
"""Stand-in for smolvm, selected with SMOLVM_BIN, so kelder can be run
without a host.

It appends each call's arguments to $SMOLVM_TRACE (a JSON list per line) and
answers just enough to keep kelder going:
  machine status     reports the machine as running; with FAKE_SMOLVM_FRESH=1
                     only machines made with `machine create` are known
  machine mount ...  add/rm/ls kept in $SMOLVM_TRACE.mounts
  machine exec       runs the command locally, without the CLI prefix up to
                     `--`; with FAKE_SMOLVM_NOEXEC=1 it runs nothing, prints 0
                     for `id -u` and exits 124 for `timeout`; `rmdir` is
                     never run, as here it would remove the host's directory
  anything else      exits 0
"""
import json
import os
import sys

args = sys.argv[1:]
trace = os.environ["SMOLVM_TRACE"]
with open(trace, "a") as f:
    f.write(json.dumps(args) + "\n")

if args[:1] != ["machine"] or len(args) < 2:
    sys.exit(0)
name = args[args.index("--name") + 1] if "--name" in args else "fake"
mounts = trace + ".mounts"
created = trace + ".created"


def flag_values(flag):
    return [args[i + 1] for i, a in enumerate(args[:-1]) if a == flag]


def read_lines(path):
    try:
        with open(path) as f:
            return f.read().splitlines()
    except FileNotFoundError:
        return []


command = args[1]
if command == "create":
    with open(created, "a") as f:
        f.write(name + "\n")
elif command == "status":
    if os.environ.get("FAKE_SMOLVM_FRESH") and name not in read_lines(created):
        sys.exit(f"machine '{name}' not found")
    print(f"Machine '{name}': running")
elif command == "mount" and len(args) > 2:
    lines = read_lines(mounts)
    if args[2] == "ls":
        print("\n".join(lines))
        sys.exit(0)
    if args[2] == "add":
        lines += ["{} -> {}".format(*v.split(":")[:2]) for v in flag_values("-v")]
    elif args[2] == "rm":
        gone = flag_values("-t")
        lines = [m for m in lines if m.split(" -> ")[1] not in gone]
    with open(mounts, "w") as f:
        f.write("".join(m + "\n" for m in lines))
elif command == "exec":
    cmd = args[args.index("--") + 1:]
    if cmd[:1] == ["rmdir"]:
        sys.exit(0)
    if os.environ.get("FAKE_SMOLVM_NOEXEC"):
        if cmd[:2] == ["id", "-u"]:
            print(0)
        sys.exit(124 if cmd[:1] == ["timeout"] else 0)
    os.execvp(cmd[0], cmd)
