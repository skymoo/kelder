# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Adam Mercer
"""The QEMU/KVM backend: a machine made of QEMU, virtiofsd and ssh.

The machine is a Debian cloud image booted by QEMU as the user running
kelder. Commands reach it over ssh on a forwarded localhost port. A
host directory is attached by starting a virtiofsd for it and plugging a
vhost-user-fs device into the running machine over QMP. Each one takes
one of the PCIe ports declared at boot: a machine starts with as many as
the session starting it attaches, or SLOTS when nothing says how many,
and is restarted with more when a session finds it running with fewer
than it needs.

Sessions run as a guest user with the host user's uid and gid, who may
sudo, so files in attached directories belong to the same user on both
sides. Run as root, kelder uses root in the guest too.
"""
import contextlib
import hashlib
import json
import os
import platform
import secrets
import shlex
import shutil
import signal
import socket
import subprocess
import sys
import time
import urllib.request
from pathlib import Path
from typing import Self

from . import settings
from .settings import Capture, die, note, warn

IMAGE = "debian-13"
IMAGE_URL = "https://cloud.debian.org/images/cloud/trixie/latest/debian-13-genericcloud-amd64.qcow2"
IMAGE_SUMS = "SHA512SUMS"
DISK = "20G"
MAX_CPUS = 4
MAX_MEM = 8192   # MiB
# Ports for attached directories a machine has when nothing says how many it
# needs, as when setup starts it; the state directory takes one more.
SLOTS = 2
BOOT_TIMEOUT = 300
GUEST_USER = "kelder"
# UEFI firmware (code, variables), used when present; otherwise QEMU's own BIOS.
OVMF = (Path("/usr/share/OVMF/OVMF_CODE_4M.fd"), Path("/usr/share/OVMF/OVMF_VARS_4M.fd"))
# virtiofsd is not on PATH in Debian or Fedora.
VIRTIOFSD = ("/usr/libexec/virtiofsd", "/usr/lib/qemu/virtiofsd")
# Each takes -output, -volid, -joliet and -rock. mkisofs is last because it
# is the one not tried.
ISO_TOOLS = ("genisoimage", "xorrisofs", "mkisofs")


# --- where things are --------------------------------------------------------

def machine_dir() -> Path:
    """The machine's disk, key and seed; it exists exactly when the machine does."""
    data = settings.env("XDG_DATA_HOME", os.path.join(settings.HOME, ".local/share"))
    return Path(data) / "kelder" / "machines" / settings.VM


def run_dir() -> Path:
    """What only a running machine has: sockets, the pid, the attachments."""
    base = settings.env("XDG_RUNTIME_DIR", os.path.join(settings.HOME, ".cache"))
    return Path(base) / "kelder" / settings.VM / "qemu"


def image_path() -> Path:
    cache = settings.env("XDG_CACHE_HOME", os.path.join(settings.HOME, ".cache"))
    return Path(cache) / "kelder" / "images" / f"{IMAGE}.qcow2"


def guest_user() -> str:
    return "root" if os.getuid() == 0 else GUEST_USER


def sudo() -> list[str]:
    """The prefix that makes a guest command run as root."""
    return [] if os.getuid() == 0 else ["sudo"]


def virtiofsd() -> str | None:
    return shutil.which("virtiofsd") or next((p for p in VIRTIOFSD if os.access(p, os.X_OK)), None)


def iso_tool() -> str | None:
    return next((t for t in ISO_TOOLS if shutil.which(t)), None)


# --- running things ----------------------------------------------------------

def run(argv: list[str], capture: Capture = None, stdin: int | None = subprocess.DEVNULL) -> tuple[int, str]:
    """Run a host command; returns (exit status, captured output).

    capture is as for smolvm.run. DRY_RUN prints the command and runs nothing.
    """
    if settings.DRY_RUN:
        print("+ " + shlex.join(argv), file=sys.stderr)
        return 0, ""
    stdout = {None: None, "drop": subprocess.DEVNULL}.get(capture, subprocess.PIPE)
    stderr = subprocess.STDOUT if capture == "all" else None
    p = subprocess.run(argv, check=False, stdin=stdin, stdout=stdout, stderr=stderr, text=True)
    return p.returncode, p.stdout or ""


def port() -> int:
    try:
        return int((run_dir() / "port").read_text())
    except (OSError, ValueError):
        return 0


def ssh(*options: str) -> list[str]:
    """The ssh command that reaches the guest, up to and including the destination."""
    d = machine_dir()
    return ["ssh", "-i", str(d / "id_ed25519"), "-p", str(port()),
            "-o", "IdentitiesOnly=yes", "-o", f"UserKnownHostsFile={d / 'known_hosts'}",
            # The port changes with every boot; the alias keeps one known_hosts entry.
            "-o", f"HostKeyAlias=kelder-{settings.VM}", "-o", "StrictHostKeyChecking=accept-new",
            "-o", "LogLevel=ERROR", *options, f"{guest_user()}@127.0.0.1"]


def guest(*cmd: str, capture: Capture = None) -> tuple[int, str]:
    """Run a command in the guest without a TTY or stdin; returns (exit status, output)."""
    return run([*ssh("-o", "BatchMode=yes"), shlex.join(cmd)], capture=capture)


def guest_ok(*cmd: str) -> bool:
    """True if the command succeeds in the guest; its output is discarded."""
    return guest(*(cmd or ("true",)), capture="all")[0] == 0


def session(tty: bool, cmd: list[str]) -> int:
    """Run a command in the guest on this terminal; returns its exit status."""
    # -e none: a "~" at the start of a line is Claude's, not ssh's escape.
    argv = [*ssh(*(["-t", "-e", "none"] if tty else [])), shlex.join(cmd)]
    if settings.DRY_RUN:
        return run(argv)[0]
    return subprocess.call(argv)


class QmpError(Exception):
    pass


class Qmp:
    """A connection to the running machine's QMP socket."""

    def __init__(self, path: str | os.PathLike[str] | None = None, timeout: float = 10) -> None:
        self.sock = socket.socket(socket.AF_UNIX)
        self.sock.settimeout(timeout)
        self.sock.connect(str(path or run_dir() / "qmp.sock"))
        self.file = self.sock.makefile("rw")
        self.events: list[dict] = []
        self._reply()   # the greeting
        self.command("qmp_capabilities")

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *exc: object) -> None:
        self.file.close()
        self.sock.close()

    def _read(self) -> dict:
        line = self.file.readline()
        if not line:
            raise QmpError("QEMU closed the QMP connection")
        return json.loads(line)

    def _reply(self) -> dict:
        """The next message that is not an event; events are kept for wait_event."""
        while True:
            msg = self._read()
            if "event" not in msg:
                return msg
            self.events.append(msg)

    def command(self, name: str, arguments: dict | None = None) -> object:
        msg = {"execute": name, **({"arguments": arguments} if arguments else {})}
        self.file.write(json.dumps(msg) + "\n")
        self.file.flush()
        reply = self._reply()
        if "error" in reply:
            raise QmpError(f"{name}: {reply['error'].get('desc', reply['error'])}")
        return reply.get("return")

    def wait_event(self, name: str, device: str) -> bool:
        """True once the event has arrived for the device; False if the timeout passes first."""
        while True:
            for event in self.events:
                if event["event"] == name and event.get("data", {}).get("device") == device:
                    return True
            try:
                self.events.append(self._read())
            except (TimeoutError, QmpError):
                return False


# --- the machine -------------------------------------------------------------

def require() -> None:
    """Fail unless QEMU, virtiofsd, ssh and an ISO tool are installed."""
    if settings.DRY_RUN:
        return
    missing = [p for p in ("qemu-system-x86_64", "qemu-img", "ssh", "ssh-keygen") if not shutil.which(p)]
    if not virtiofsd():
        missing.append("virtiofsd")
    if not iso_tool():
        missing.append("genisoimage")
    if missing:
        die(f"not found on the host: {' '.join(missing)}")


def host_checks() -> None:
    if platform.machine() != "x86_64":
        die(f"the QEMU backend needs an x86_64 host, not {platform.machine()}")
    if not os.access("/dev/kvm", os.R_OK | os.W_OK):
        die("no access to /dev/kvm; is this user in the kvm group?")
    if len(str(run_dir() / "fs0.sock")) > 100:
        die(f"{run_dir()} is too long a path for the machine's sockets; set XDG_RUNTIME_DIR")


def exists() -> bool:
    """True if the machine exists, running or not."""
    return (machine_dir() / "disk.qcow2").exists()


def qemu_pid() -> int | None:
    """The PID of the machine's QEMU process, or None if it isn't running."""
    try:
        pid = int((run_dir() / "qemu.pid").read_text())
        cmdline = Path(f"/proc/{pid}/cmdline").read_bytes()
    except (OSError, ValueError):
        return None
    # A recycled PID belongs to something else.
    return pid if str(run_dir()).encode() in cmdline else None


def running() -> bool:
    return qemu_pid() is not None


def pull() -> Path:
    """Download the base image unless it is cached; returns its path."""
    dest = image_path()
    if dest.exists():
        return dest
    dest.parent.mkdir(parents=True, exist_ok=True)
    base, name = IMAGE_URL.rsplit("/", 1)
    try:
        with urllib.request.urlopen(f"{base}/{IMAGE_SUMS}", timeout=30) as r:
            sums = r.read().decode()
        want = next((ln.split()[0] for ln in sums.splitlines()
                     if ln.split() and ln.split()[-1].lstrip("*") == name), None)
        if not want:
            die(f"{name} is not listed in {base}/{IMAGE_SUMS}")
        tmp = dest.with_suffix(f".part{os.getpid()}")
        digest, done, shown = hashlib.sha512(), 0, -1
        print(f"Downloading {IMAGE_URL}...")
        with urllib.request.urlopen(IMAGE_URL, timeout=60) as r, open(tmp, "wb") as f:
            total = int(r.headers.get("Content-Length") or 0)
            while chunk := r.read(1 << 20):
                f.write(chunk)
                digest.update(chunk)
                done += len(chunk)
                tenth = done * 10 // total if total else -1
                if tenth > shown:
                    shown = tenth
                    print(f"  {tenth * 10}% of {total >> 20} MiB")
    except OSError as e:
        die(f"could not download {IMAGE_URL}: {e}")
    if digest.hexdigest() != want:
        tmp.unlink()
        die(f"checksum mismatch for {name}; download discarded")
    os.replace(tmp, dest)
    return dest


def user_data(pubkey: str) -> str:
    """cloud-init's configuration: who sessions log in as."""
    if os.getuid() == 0:
        config: dict = {"disable_root": False, "ssh_authorized_keys": [pubkey]}
    else:
        gid = os.getgid()
        config = {
            # Before the user is made: a group with the host user's gid, whatever else has it.
            "bootcmd": [["sh", "-c", f"getent group {GUEST_USER} || groupadd -o -g {gid} {GUEST_USER}"]],
            "users": [{
                "name": GUEST_USER, "uid": os.getuid(), "primary_group": GUEST_USER,
                "groups": ["kvm"], "shell": "/bin/bash", "sudo": "ALL=(ALL) NOPASSWD:ALL",
                "ssh_authorized_keys": [pubkey],
            }],
        }
    return "#cloud-config\n" + json.dumps(config, indent=1) + "\n"   # JSON is YAML


def create() -> None:
    """Make the machine's disk, ssh key and cloud-init seed."""
    base = pull()
    d = machine_dir()
    if settings.DRY_RUN:
        print(f"+ create {d} from {base}", file=sys.stderr)
        return
    tmp = d.with_name(f".{d.name}.{os.getpid()}")
    tmp.mkdir(parents=True, mode=0o700)
    try:
        shutil.copyfile(base, tmp / "disk.qcow2")
        steps = [
            ["qemu-img", "resize", "-q", str(tmp / "disk.qcow2"), DISK],
            ["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-C", f"kelder-{settings.VM}",
             "-f", str(tmp / "id_ed25519")],
        ]
        for step in steps:
            if run(step)[0] != 0:
                die(f"could not create machine '{settings.VM}': {step[0]} failed")
        (tmp / "user-data").write_text(user_data((tmp / "id_ed25519.pub").read_text().strip()))
        (tmp / "meta-data").write_text(
            f"instance-id: kelder-{settings.VM}-{secrets.token_hex(4)}\nlocal-hostname: {settings.VM}\n")
        # xorrisofs prints a banner even with -quiet, so its output is only
        # shown when it fails.
        rc, out = run([iso_tool() or ISO_TOOLS[0], "-quiet", "-output", str(tmp / "seed.iso"),
                       "-volid", "cidata", "-joliet", "-rock",
                       str(tmp / "user-data"), str(tmp / "meta-data")], capture="all")
        if rc != 0:
            die(f"could not create machine '{settings.VM}': no cloud-init seed\n{out.strip()}")
        if OVMF[0].exists() and OVMF[1].exists():
            shutil.copyfile(OVMF[1], tmp / "efivars.fd")
        os.replace(tmp, d)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def ensure(state: str, guest_state: str) -> None:
    """Create the machine if there is none and leave it running, with the
    host directory state mounted at guest_state."""
    if exists():
        print(f"Machine '{settings.VM}' exists; reusing it.")
    else:
        if "IMAGE" in os.environ:
            warn(f"IMAGE is ignored: QEMU machines are made from {IMAGE}")
        print(f"Creating machine '{settings.VM}'...")
        create()
    start()
    if not guest_ok("mountpoint", "-q", guest_state):
        die(f"state directory is not mounted at {guest_state}")


def memory() -> int:
    """MiB for the machine: half the host's memory, MAX_MEM at most."""
    try:
        with open("/proc/meminfo") as f:
            total = int(f.readline().split()[1]) >> 10
    except (OSError, ValueError, IndexError):
        return MAX_MEM
    return min(MAX_MEM, total // 2)


def slots() -> int:
    """How many directories the running machine has ports for."""
    try:
        return int((run_dir() / "slots").read_text())
    except (OSError, ValueError):
        return SLOTS


def qemu_argv(ssh_port: int, directories: int = SLOTS) -> list[str]:
    d, r, mem = machine_dir(), run_dir(), memory()
    firmware = []
    if (d / "efivars.fd").exists():
        firmware = ["-drive", f"if=pflash,format=raw,readonly=on,file={OVMF[0]}",
                    "-drive", f"if=pflash,format=raw,file={d / 'efivars.fd'}"]
    ports = []
    for slot in range(directories + 1):
        ports += ["-device", f"pcie-root-port,id=rp{slot},bus=pcie.0,chassis={slot + 1}"]
    return [
        "qemu-system-x86_64", "-name", f"kelder-{settings.VM}", "-machine", "q35,accel=kvm", "-cpu", "host",
        "-smp", str(min(MAX_CPUS, os.cpu_count() or 1)), "-m", str(mem),
        # virtiofsd reads the guest's memory, so it has to be shared.
        "-object", f"memory-backend-memfd,id=mem,size={mem}M,share=on", "-numa", "node,memdev=mem",
        "-nodefaults", "-display", "none", "-serial", f"file:{r / 'console.log'}", *firmware,
        "-drive", f"if=virtio,file={d / 'disk.qcow2'},format=qcow2",
        "-drive", f"if=virtio,file={d / 'seed.iso'},format=raw,readonly=on",
        "-netdev", f"user,id=n0,hostfwd=tcp:127.0.0.1:{ssh_port}-:22", "-device", "virtio-net-pci,netdev=n0",
        "-device", "virtio-rng-pci", *ports,
        "-qmp", f"unix:{r / 'qmp.sock'},server=on,wait=off",
        "-daemonize", "-pidfile", str(r / "qemu.pid"),
    ]


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def console_tail(n: int = 20) -> str:
    try:
        return "\n".join((run_dir() / "console.log").read_text(errors="replace").splitlines()[-n:])
    except OSError:
        return "(no console output)"


def start(directories: int = 0) -> bool:
    """Start the machine if needed, then wait until it accepts ssh.

    A machine this starts has ports for that many directories, or for
    SLOTS when the caller gives no number. Returns True if this call
    booted it, False if it was already running.
    """
    directories = directories or SLOTS
    if running():
        return False
    note(f"starting machine '{settings.VM}'...")
    clear_runtime()
    r = run_dir()
    if settings.DRY_RUN:
        run(qemu_argv(0, directories))
        return True
    r.mkdir(parents=True, mode=0o700)
    for attempt in range(3):   # another program can take the port first
        ssh_port = free_port()
        rc, out = run(qemu_argv(ssh_port, directories), capture="all")
        if rc == 0:
            break
        if "hostfwd" not in out or attempt == 2:
            die(f"could not start machine '{settings.VM}':\n{out.strip()}")
    (r / "port").write_text(str(ssh_port))
    (r / "slots").write_text(str(directories))

    # The first boot also waits for cloud-init, which makes the user ssh logs in as.
    deadline = time.monotonic() + BOOT_TIMEOUT
    probe = [*ssh("-o", "BatchMode=yes", "-o", "ConnectTimeout=3"), "cloud-init status --wait >/dev/null 2>&1; true"]
    while run(probe, capture="all")[0] != 0:
        if not running():
            die(f"machine '{settings.VM}' stopped while booting. Its console:\n{console_tail()}")
        if time.monotonic() > deadline:
            die(f"machine '{settings.VM}' did not answer ssh within {BOOT_TIMEOUT}s. Its console:\n{console_tail()}")
        time.sleep(1)
    if not mount(os.path.realpath(settings.CLAUDE_STATE), settings.GUEST_CLAUDE_STATE):
        die(f"could not mount Claude's state directory in machine '{settings.VM}'")
    return True


def stop() -> bool:
    pid = qemu_pid()
    if pid is None:
        clear_runtime()
        return True
    if settings.DRY_RUN:
        return guest(*sudo(), "poweroff")[0] == 0

    def gone(seconds: float) -> bool:
        deadline = time.monotonic() + seconds
        while qemu_pid():
            if time.monotonic() > deadline:
                return False
            time.sleep(0.2)
        return True

    guest(*sudo(), "poweroff", capture="all")   # ssh may lose the connection: the status says nothing
    if not gone(60):
        with contextlib.suppress(OSError, QmpError), Qmp() as qmp:
            qmp.command("quit")
        if not gone(10):
            os.kill(pid, signal.SIGKILL)
            if not gone(10):
                return False
    clear_runtime()
    return True


def delete() -> bool:
    """Delete the machine, running or not."""
    if not stop():
        return False
    if settings.DRY_RUN:
        print(f"+ rm -rf {machine_dir()}", file=sys.stderr)
        return True
    shutil.rmtree(machine_dir(), ignore_errors=True)
    return not machine_dir().exists()


def delete_command() -> str:
    """The command that deletes the machine, to show the user."""
    return (f"kill $(cat {shlex.quote(str(run_dir() / 'qemu.pid'))}); "
            f"rm -rf {shlex.quote(str(machine_dir()))}")


def guest_files() -> list[tuple[str, str, str]]:
    return []


def smoketest_host() -> int:
    return 0


# --- attached directories ----------------------------------------------------
# mounts.json maps each mount point in the guest to the port it takes, the
# directory behind it and its virtiofsd. It is only ever changed under the
# session lock (session.py).

def mounts() -> dict[str, dict]:
    try:
        return json.loads((run_dir() / "mounts.json").read_text())
    except (OSError, ValueError):
        return {}


def save_mounts(m: dict[str, dict]) -> None:
    (run_dir() / "mounts.json").write_text(json.dumps(m))


def kill_virtiofsd(pid: int) -> None:
    try:
        if str(run_dir()).encode() in Path(f"/proc/{pid}/cmdline").read_bytes():
            os.kill(pid, signal.SIGTERM)
    except OSError:
        pass


def clear_runtime() -> None:
    """Remove what a machine that is no longer running left behind."""
    if settings.DRY_RUN:
        return
    for entry in mounts().values():
        kill_virtiofsd(entry["pid"])
    shutil.rmtree(run_dir(), ignore_errors=True)


def spawn_virtiofsd(source: str, sock: Path) -> int | None:
    """Start a virtiofsd that outlives this process; its PID once the socket is there."""
    with open(sock.with_suffix(".log"), "w") as log:
        p = subprocess.Popen([virtiofsd() or "virtiofsd", f"--socket-path={sock}", f"--shared-dir={source}"],
                             stdin=subprocess.DEVNULL, stdout=log, stderr=log, start_new_session=True)
    for _ in range(50):
        if sock.exists():
            return p.pid
        if p.poll() is not None:
            break
        time.sleep(0.1)
    p.kill()
    return None


def mount(source: str, target: str) -> bool:
    """Attach the host directory source to the running machine at target."""
    m = mounts()
    if target in m:
        return True
    taken = {entry["slot"] for entry in m.values()}
    slot = next((s for s in range(slots() + 1) if s not in taken), None)
    if slot is None:
        note(f"machine '{settings.VM}' has no free port for {target}")
        return False
    tag = f"fs{slot}"
    sock = run_dir() / f"{tag}.sock"
    add = [*sudo(), "sh", "-c", 'mkdir -p "$1" && mount -t virtiofs "$0" "$1"', tag, target]
    if settings.DRY_RUN:
        run([virtiofsd() or "virtiofsd", f"--socket-path={sock}", f"--shared-dir={source}"])
        print(f"+ qmp chardev-add ch{slot}; device_add vhost-user-fs-pci tag={tag}", file=sys.stderr)
        return guest(*add)[0] == 0
    pid = spawn_virtiofsd(source, sock)
    if pid is None:
        note(f"virtiofsd did not start for {source}; see {sock.with_suffix('.log')}")
        return False
    try:
        with Qmp() as qmp:
            qmp.command("chardev-add", {"id": f"ch{slot}", "backend": {"type": "socket", "data": {
                "addr": {"type": "unix", "data": {"path": str(sock)}}, "server": False}}})
            qmp.command("device_add", {"driver": "vhost-user-fs-pci", "id": f"dev{slot}", "chardev": f"ch{slot}",
                                       "tag": tag, "queue-size": 1024, "bus": f"rp{slot}"})
    except (OSError, QmpError) as e:
        note(f"could not plug {source} into machine '{settings.VM}': {e}")
        kill_virtiofsd(pid)
        return False
    m[target] = {"slot": slot, "source": source, "pid": pid}
    save_mounts(m)
    for _ in range(20):   # the guest takes a moment to see the new device
        if guest(*add, capture="all")[0] == 0:
            return True
        time.sleep(0.5)
    unplug(target)
    return False


def unplug(target: str) -> None:
    """Take the device behind target out of the machine and stop its virtiofsd."""
    m = mounts()
    entry = m.pop(target)
    slot = entry["slot"]
    try:
        with Qmp(timeout=15) as qmp:
            qmp.command("device_del", {"id": f"dev{slot}"})
            qmp.wait_event("DEVICE_DELETED", f"dev{slot}")
            qmp.command("chardev-remove", {"id": f"ch{slot}"})
    except (OSError, QmpError) as e:
        note(f"could not unplug {target} from machine '{settings.VM}': {e}")
    kill_virtiofsd(entry["pid"])   # it exits by itself when the device goes; this is for when it did not
    save_mounts(m)
    for suffix in (".sock", ".sock.pid", ".log"):   # .sock.pid is virtiofsd's own
        (run_dir() / f"fs{slot}{suffix}").unlink(missing_ok=True)


def attached(d: str) -> bool:
    return d in mounts()


def make_room(directories: int) -> bool:
    """Make sure that many more directories can be attached, restarting the
    machine with more ports if it has too few free. Only for when no
    session has a directory attached: a restart ends whatever runs in it."""
    used = len([target for target in mounts() if target != settings.GUEST_CLAUDE_STATE])
    if used + directories <= slots():
        return True
    if settings.DRY_RUN:
        return True
    note(f"restarting machine '{settings.VM}': it has ports for {slots()} directories, "
         f"and {used + directories} are needed")
    if not stop():
        return False
    start(used + directories)
    return True


def attach(d: str) -> bool:
    return mount(d, d)


def detach(d: str) -> bool:
    if d not in mounts():
        return True
    umount = [*sudo(), "umount", d]
    if guest(*umount, capture="all")[0] != 0:
        # No session is using the directory, so what keeps it busy was left by
        # one that was cut off: without a TTY, ssh does not hang up the guest's
        # side. -M makes fuser do nothing unless d is a mount point.
        guest(*sudo(), "fuser", "-k", "-M", "-m", d, capture="all")
        for _ in range(10):
            time.sleep(0.5)
            if guest(*umount, capture="all")[0] == 0:
                break
        else:
            return False
    if settings.DRY_RUN:
        print(f"+ qmp device_del; chardev-remove for {d}", file=sys.stderr)
    else:
        unplug(d)
    # The mount point stays on the machine's disk, where its name would tell
    # later sessions about a project they were not given. Remove it and the
    # parents it leaves empty; rmdir never removes a directory with content.
    guest(*sudo(), "rmdir", "-p", "--ignore-fail-on-non-empty", d, capture="all")
    return True
