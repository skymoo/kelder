# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Adam Mercer
"""The QEMU backend without QEMU: the commands it builds, its QMP client
against a stand-in server, and the bookkeeping of attached directories."""
import json
import os
import socket
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

from kelder import machine, qemu, settings, smolvm


class Base(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp = Path(tmp.name).resolve()
        for patch in (
            mock.patch.multiple(settings, VM="fake", HOME=str(self.tmp / "home"), DRY_RUN=False,
                                CLAUDE_STATE=str(self.tmp / "state"), GUEST_CLAUDE_STATE="/var/lib/kelder"),
            mock.patch.dict(os.environ, {"XDG_RUNTIME_DIR": str(self.tmp / "run"),
                                         "XDG_DATA_HOME": str(self.tmp / "data"),
                                         "XDG_CACHE_HOME": str(self.tmp / "cache")}),
        ):
            patch.start()
            self.addCleanup(patch.stop)
        os.environ.pop("BACKEND", None)

    def make_machine(self):
        qemu.machine_dir().mkdir(parents=True)
        (qemu.machine_dir() / "disk.qcow2").touch()


class Backend(Base):
    def test_a_machine_is_smolvms_unless_qemu_made_it(self):
        self.assertIs(machine.backend(), smolvm)
        self.make_machine()
        self.assertIs(machine.backend(), qemu)

    def test_BACKEND_names_it_either_way(self):
        os.environ["BACKEND"] = "qemu"
        self.assertIs(machine.backend(), qemu)
        self.make_machine()
        os.environ["BACKEND"] = "smolvm"
        self.assertIs(machine.backend(), smolvm)

    def test_an_unknown_BACKEND_is_refused(self):
        os.environ["BACKEND"] = "xen"
        with self.assertRaises(SystemExit), mock.patch("sys.stderr"):
            machine.backend()


class Commands(Base):
    def test_qemu_shares_its_memory_and_declares_a_port_for_each_directory(self):
        with mock.patch.object(qemu, "memory", return_value=2048):
            argv = qemu.qemu_argv(2222)
        self.assertEqual(argv[argv.index("-m") + 1], "2048")
        self.assertIn("memory-backend-memfd,id=mem,size=2048M,share=on", argv)
        ports = [a for a in argv if a.startswith("pcie-root-port,")]
        self.assertEqual(len(ports), qemu.SLOTS + 1)   # one more for the state directory
        more = [a for a in qemu.qemu_argv(2222, 12) if a.startswith("pcie-root-port,")]
        self.assertEqual(len(more), 13)
        self.assertIn("user,id=n0,hostfwd=tcp:127.0.0.1:2222-:22", argv)
        self.assertIn(f"unix:{qemu.run_dir()}/qmp.sock,server=on,wait=off", argv)
        self.assertNotIn("if=pflash", " ".join(argv))   # no efivars.fd: QEMU's own BIOS

    def test_a_machine_made_with_uefi_boots_with_it(self):
        self.make_machine()
        (qemu.machine_dir() / "efivars.fd").touch()
        argv = " ".join(qemu.qemu_argv(2222))
        self.assertIn(f"if=pflash,format=raw,readonly=on,file={qemu.OVMF[0]}", argv)
        self.assertIn(f"if=pflash,format=raw,file={qemu.machine_dir()}/efivars.fd", argv)

    def test_memory_is_half_the_hosts_up_to_the_maximum(self):
        for total_kb, want in ((4 << 20, 2048), (64 << 20, qemu.MAX_MEM)):
            with mock.patch("builtins.open", mock.mock_open(read_data=f"MemTotal: {total_kb} kB\n")):
                self.assertEqual(qemu.memory(), want)

    def test_an_interactive_session_gets_a_tty_and_no_escape_character(self):
        with mock.patch.object(qemu.subprocess, "call", return_value=7) as call, \
                mock.patch.object(qemu.os, "getuid", return_value=1000):
            self.assertEqual(qemu.session(True, ["bash", "-c", "echo 'a b'", "_", "it's"]), 7)
        argv = call.call_args.args[0]
        self.assertEqual(argv[0], "ssh")
        self.assertEqual(argv[argv.index("-t"):argv.index("-t") + 3], ["-t", "-e", "none"])
        self.assertEqual(argv[-2], "kelder@127.0.0.1")
        self.assertEqual(argv[-1], """bash -c 'echo '"'"'a b'"'"'' _ 'it'"'"'s'""")
        with mock.patch.object(qemu.subprocess, "call", return_value=0) as call:
            qemu.session(False, ["true"])
        self.assertNotIn("-t", call.call_args.args[0])

    def test_guest_commands_get_no_stdin(self):
        done = mock.Mock(returncode=3, stdout="out")
        with mock.patch.object(qemu.subprocess, "run", return_value=done) as run:
            self.assertEqual(qemu.guest("id", "-u", capture="out"), (3, "out"))
        self.assertIs(run.call_args.kwargs["stdin"], qemu.subprocess.DEVNULL)
        self.assertIn("BatchMode=yes", run.call_args.args[0])

    def test_the_guest_user_has_the_host_users_ids_and_root_stays_root(self):
        with mock.patch.object(qemu.os, "getuid", return_value=1234), \
                mock.patch.object(qemu.os, "getgid", return_value=100):
            text = qemu.user_data("ssh-ed25519 KEY")
            self.assertEqual(qemu.guest_user(), "kelder")
            self.assertEqual(qemu.sudo(), ["sudo"])
        self.assertTrue(text.startswith("#cloud-config\n"))
        config = json.loads(text.split("\n", 1)[1])
        user = config["users"][0]
        self.assertEqual((user["name"], user["uid"], user["primary_group"]), ("kelder", 1234, "kelder"))
        self.assertEqual(user["ssh_authorized_keys"], ["ssh-ed25519 KEY"])
        self.assertIn("groupadd -o -g 100 kelder", config["bootcmd"][0][2])
        with mock.patch.object(qemu.os, "getuid", return_value=0):
            config = json.loads(qemu.user_data("ssh-ed25519 KEY").split("\n", 1)[1])
            self.assertEqual(qemu.guest_user(), "root")
            self.assertEqual(qemu.sudo(), [])
        self.assertEqual(config, {"disable_root": False, "ssh_authorized_keys": ["ssh-ed25519 KEY"]})


class FakeQmp(threading.Thread):
    """A QMP server for one connection: answers every command with what
    `replies` holds for it, each a list of messages, and records the commands."""

    def __init__(self, path, replies):
        super().__init__(daemon=True)
        self.replies, self.seen = replies, []
        self.server = socket.socket(socket.AF_UNIX)
        self.server.bind(str(path))
        self.server.listen(1)
        self.start()

    def run(self):
        conn, _ = self.server.accept()
        with conn, conn.makefile("rw") as f:
            f.write(json.dumps({"QMP": {"version": {}, "capabilities": []}}) + "\n")
            f.flush()
            for line in f:
                msg = json.loads(line)
                self.seen.append(msg)
                for reply in self.replies.get(msg["execute"], [{"return": {}}]):
                    f.write(json.dumps(reply) + "\n")
                f.flush()
        self.server.close()


class QmpClient(Base):
    def serve(self, replies):
        path = self.tmp / "qmp.sock"
        server = FakeQmp(path, replies)
        self.addCleanup(server.join, 5)
        return path, server

    def test_commands_return_their_result_past_any_events(self):
        path, server = self.serve({"query-status": [{"event": "RESUME"}, {"return": {"running": True}}]})
        with qemu.Qmp(path) as qmp:
            self.assertEqual(qmp.command("query-status"), {"running": True})
            self.assertEqual([e["event"] for e in qmp.events], ["RESUME"])
        self.assertEqual([m["execute"] for m in server.seen], ["qmp_capabilities", "query-status"])

    def test_an_error_reply_is_raised(self):
        path, _ = self.serve({"device_add": [{"error": {"class": "GenericError", "desc": "Bus 'rp9' not found"}}]})
        with qemu.Qmp(path) as qmp, self.assertRaisesRegex(qemu.QmpError, "device_add: Bus 'rp9' not found"):
            qmp.command("device_add", {"driver": "x", "bus": "rp9"})

    def test_wait_event_finds_the_devices_event_or_gives_up(self):
        deleted = {"event": "DEVICE_DELETED", "data": {"device": "dev3"}}
        path, _ = self.serve({"device_del": [{"return": {}}, {"event": "DEVICE_DELETED", "data": {}}, deleted]})
        with qemu.Qmp(path, timeout=0.3) as qmp:
            qmp.command("device_del", {"id": "dev3"})
            self.assertTrue(qmp.wait_event("DEVICE_DELETED", "dev3"))
            self.assertFalse(qmp.wait_event("DEVICE_DELETED", "dev4"))


class Attachments(Base):
    """mount and detach with the virtiofsd, the QMP socket and the guest stood in for."""

    def setUp(self):
        super().setUp()
        qemu.run_dir().mkdir(parents=True)
        self.qmp = mock.MagicMock()
        self.qmp.__enter__.return_value = self.qmp
        self.guest_rc = 0
        self.busy = 0   # how many times fuser has to run before umount works
        self.guest_calls = []
        for patch in (
            mock.patch.object(qemu, "Qmp", return_value=self.qmp),
            mock.patch.object(qemu, "spawn_virtiofsd", side_effect=lambda source, sock: 4242),
            mock.patch.object(qemu, "kill_virtiofsd"),
            mock.patch.object(qemu, "guest", side_effect=self.guest),
            mock.patch.object(qemu.os, "getuid", return_value=1000),
            mock.patch.object(qemu.time, "sleep"),
        ):
            patch.start()
            self.addCleanup(patch.stop)

    def guest(self, *cmd, capture=None):
        self.guest_calls.append(list(cmd))
        if "umount" in cmd and self.busy:
            if "fuser" not in self.guest_calls[-2:][0]:
                return 32, ""
            self.busy -= 1
            return (32, "") if self.busy else (0, "")
        return self.guest_rc, ""

    def commands(self):
        return [(c.args[0], c.args[1]["id"]) for c in self.qmp.command.call_args_list]

    def test_attach_plugs_a_device_into_the_first_free_port_and_mounts_it(self):
        self.assertTrue(qemu.attach("/home/me/a"))
        self.assertTrue(qemu.attach("/home/me/b"))
        self.assertEqual(self.commands(), [("chardev-add", "ch0"), ("device_add", "dev0"),
                                           ("chardev-add", "ch1"), ("device_add", "dev1")])
        device = self.qmp.command.call_args_list[3].args[1]
        self.assertEqual((device["driver"], device["tag"], device["bus"]), ("vhost-user-fs-pci", "fs1", "rp1"))
        self.assertEqual(self.guest_calls[1],
                         ["sudo", "sh", "-c", 'mkdir -p "$1" && mount -t virtiofs "$0" "$1"', "fs1", "/home/me/b"])
        self.assertTrue(qemu.attached("/home/me/a"))
        self.assertEqual(qemu.mounts()["/home/me/b"], {"slot": 1, "source": "/home/me/b", "pid": 4242})

    def test_attaching_again_does_nothing(self):
        qemu.attach("/home/me/a")
        self.qmp.command.reset_mock()
        self.assertTrue(qemu.attach("/home/me/a"))
        self.qmp.command.assert_not_called()

    def test_detach_unmounts_unplugs_and_frees_the_port(self):
        qemu.attach("/home/me/a")
        qemu.attach("/home/me/b")
        self.qmp.command.reset_mock()
        self.guest_calls.clear()
        self.assertTrue(qemu.detach("/home/me/a"))
        self.assertEqual(self.guest_calls, [["sudo", "umount", "/home/me/a"],
                                            ["sudo", "rmdir", "-p", "--ignore-fail-on-non-empty", "/home/me/a"]])
        self.assertEqual(self.commands(), [("device_del", "dev0"), ("chardev-remove", "ch0")])
        self.qmp.wait_event.assert_called_once_with("DEVICE_DELETED", "dev0")
        self.assertFalse(qemu.attached("/home/me/a"))
        qemu.attach("/home/me/c")
        self.assertEqual(qemu.mounts()["/home/me/c"]["slot"], 0)

    def test_detach_kills_what_a_cut_off_session_left_in_the_directory(self):
        qemu.attach("/home/me/a")
        self.guest_calls.clear()
        self.busy = 1
        self.assertTrue(qemu.detach("/home/me/a"))
        self.assertEqual(self.guest_calls[:3], [["sudo", "umount", "/home/me/a"],
                                                ["sudo", "fuser", "-k", "-M", "-m", "/home/me/a"],
                                                ["sudo", "umount", "/home/me/a"]])
        self.assertFalse(qemu.attached("/home/me/a"))

    def test_a_directory_that_will_not_unmount_stays_attached(self):
        qemu.attach("/home/me/a")
        self.qmp.command.reset_mock()
        self.busy = 99
        self.assertFalse(qemu.detach("/home/me/a"))
        self.qmp.command.assert_not_called()
        self.assertTrue(qemu.attached("/home/me/a"))

    def test_a_mount_that_fails_in_the_guest_is_unplugged_again(self):
        self.guest_rc = 32
        with mock.patch("sys.stderr"):
            self.assertFalse(qemu.attach("/home/me/a"))
        self.assertEqual(self.commands()[-2:], [("device_del", "dev0"), ("chardev-remove", "ch0")])
        self.assertEqual(qemu.mounts(), {})

    def test_only_as_many_directories_fit_as_the_machine_has_ports_for(self):
        self.assertTrue(qemu.mount("/host/state", "/var/lib/kelder"))
        for i in range(qemu.SLOTS):
            self.assertTrue(qemu.attach(f"/home/me/{i}"))
        with mock.patch("sys.stderr"):
            self.assertFalse(qemu.attach("/home/me/one-too-many"))
        (qemu.run_dir() / "slots").write_text("9")   # a machine started with a ninth port
        self.assertTrue(qemu.attach("/home/me/one-too-many"))

    def test_make_room_leaves_a_machine_with_enough_ports_alone(self):
        qemu.mount("/host/state", "/var/lib/kelder")
        with mock.patch.object(qemu, "stop") as stop, mock.patch.object(qemu, "start") as start:
            self.assertTrue(qemu.make_room(qemu.SLOTS))
        stop.assert_not_called()
        start.assert_not_called()

    def test_make_room_restarts_a_machine_with_too_few_ports(self):
        qemu.mount("/host/state", "/var/lib/kelder")
        with mock.patch.object(qemu, "stop", return_value=True) as stop, \
                mock.patch.object(qemu, "start") as start, mock.patch("sys.stderr"):
            self.assertTrue(qemu.make_room(qemu.SLOTS + 3))
        stop.assert_called_once_with()
        start.assert_called_once_with(qemu.SLOTS + 3)

    def test_make_room_fails_when_the_machine_will_not_stop(self):
        with mock.patch.object(qemu, "stop", return_value=False), \
                mock.patch.object(qemu, "start") as start, mock.patch("sys.stderr"):
            self.assertFalse(qemu.make_room(qemu.SLOTS + 1))
        start.assert_not_called()


if __name__ == "__main__":
    unittest.main()
