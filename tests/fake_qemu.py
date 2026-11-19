#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Adam Mercer
"""Stand-ins for the programs the QEMU backend runs, so kelder can be run
with BACKEND=qemu and no QEMU. What it does depends on the name it is
called by; the tests link it into a directory at the front of PATH.

  qemu-system-x86_64  writes its pidfile and serves QMP in the background:
                      every command succeeds, device_del is followed by
                      DEVICE_DELETED and quit ends it
  virtiofsd           makes its socket and waits to be stopped
  ssh                 runs the command it is given locally, as the "guest";
                      with FAKE_QEMU_NOEXEC=1 it runs nothing, prints 0 for
                      `id -u` and exits 124 for `timeout`, but still powers
                      the machine off
  ssh-keygen          writes a key pair where -f says
  sudo                runs its arguments
  poweroff            stops the stand-in QEMU
  anything else       (mount, umount, rmdir, fuser, ...) succeeds and does
                      nothing: here the real ones would act on the host

Each call is appended to $QEMU_TRACE as a JSON list, the name first. A
stand-in left running ends by itself after LIFETIME seconds.
"""
import glob
import json
import os
import signal
import socket
import sys
import time

LIFETIME = 120
name, args = os.path.basename(sys.argv[0]), sys.argv[1:]


def trace(*entry):
    with open(os.environ["QEMU_TRACE"], "a") as f:
        f.write(json.dumps(entry) + "\n")


def option(flag):
    return args[args.index(flag) + 1]


def daemonize():
    """Carry on in a background process, as QEMU does with -daemonize. The
    process that was started exits once the returned function is called."""
    ready, done = os.pipe()
    if os.fork():
        os.close(done)
        os.read(ready, 1)
        os._exit(0)
    os.close(ready)
    os.setsid()
    null = os.open(os.devnull, os.O_RDWR)
    for fd in (0, 1, 2):   # or whoever waits for our output waits for ever
        os.dup2(null, fd)
    signal.signal(signal.SIGTERM, lambda *_: os._exit(0))
    signal.alarm(LIFETIME)
    return lambda: os.close(done)


def qemu():
    trace(name, *args)
    path = option("-qmp").removeprefix("unix:").split(",")[0]
    server = socket.socket(socket.AF_UNIX)
    server.bind(path)
    server.listen(4)
    ready = daemonize()
    with open(option("-pidfile"), "w") as f:
        f.write(str(os.getpid()))
    ready()
    while True:
        conn, _ = server.accept()
        with conn, conn.makefile("rw") as f:
            f.write(json.dumps({"QMP": {"version": {}, "capabilities": []}}) + "\n")
            f.flush()
            for line in f:
                msg = json.loads(line)
                device = msg.get("arguments", {}).get("id", "")
                if msg["execute"] != "qmp_capabilities":
                    trace("qmp", msg["execute"], device)
                f.write(json.dumps({"return": {}}) + "\n")
                if msg["execute"] == "device_del":
                    f.write(json.dumps({"event": "DEVICE_DELETED", "data": {"device": device}}) + "\n")
                f.flush()
                if msg["execute"] == "quit":
                    os._exit(0)


def virtiofsd():
    trace(name, *args)
    path = next(a.split("=", 1)[1] for a in args if a.startswith("--socket-path="))
    server = socket.socket(socket.AF_UNIX)
    server.bind(path)
    signal.signal(signal.SIGTERM, lambda *_: os._exit(0))
    signal.alarm(LIFETIME)
    while True:
        time.sleep(1)


if name == "qemu-system-x86_64":
    qemu()
elif name == "virtiofsd":
    virtiofsd()
elif name == "ssh":
    trace(name, *(["-t"] if "-t" in args else []), args[-1])
    command = args[-1].removeprefix("sudo ")
    if not os.environ.get("FAKE_QEMU_NOEXEC") or command == "poweroff":
        os.execvp("bash", ["bash", "-c", args[-1]])
    if command == "id -u":
        print(0)
    sys.exit(124 if command.startswith("timeout ") else 0)
elif name == "ssh-keygen":
    trace(name, *args)
    for suffix, text in (("", "PRIVATE KEY\n"), (".pub", "ssh-ed25519 AAAA kelder-test\n")):
        with open(option("-f") + suffix, "w") as f:
            f.write(text)
elif name == "sudo":
    os.execvp(args[0], args)
elif name == "poweroff":
    trace(name)
    for pidfile in glob.glob(os.path.join(os.environ["XDG_RUNTIME_DIR"], "kelder/*/qemu/qemu.pid")):
        with open(pidfile) as f:
            os.kill(int(f.read()), signal.SIGTERM)
else:
    trace(name, *args)
