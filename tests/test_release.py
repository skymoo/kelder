# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Adam Mercer
"""Fetching a smolvm release, against a tarball made here instead of GitHub."""
import contextlib
import hashlib
import io
import os
import shutil
import tarfile
import tempfile
import unittest
from unittest import mock

from kelder import release, smolvm

VERSION = "9.9.9"


def dies(test, fn, *args):
    """Call fn expecting it to die; returns the message."""
    err = io.StringIO()
    with contextlib.redirect_stderr(err), test.assertRaises(SystemExit) as caught:
        fn(*args)
    test.assertEqual(caught.exception.code, 1)
    return err.getvalue()


class Fetch(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.served = os.path.join(tmp.name, "served")
        self.dest = os.path.join(tmp.name, "dest")
        os.makedirs(self.served)
        os.makedirs(self.dest)

        name = release.tarball(VERSION)
        top = name.removesuffix(".tar.gz")
        wrapper = os.path.join(tmp.name, "smolvm")
        with open(wrapper, "w") as f:
            f.write("#!/bin/sh\n")
        os.chmod(wrapper, 0o755)
        archive = os.path.join(self.served, name)
        with tarfile.open(archive, "w:gz") as tar:
            tar.add(wrapper, arcname=f"{top}/smolvm")
        with open(archive, "rb") as f:
            self.digest = hashlib.file_digest(f, "sha256").hexdigest()
        self.write_sums(self.digest)
        self.wrapper = os.path.join(self.dest, top, "smolvm")

        self.urls = []
        patch = mock.patch.object(release, "download", self.download)
        patch.start()
        self.addCleanup(patch.stop)

    def write_sums(self, digest):
        with open(os.path.join(self.served, "checksums.sha256"), "w") as f:
            f.write(f"{'0' * 64}  smolvm-{VERSION}-darwin-arm64.tar.gz\n")
            f.write(f"{digest}  {release.tarball(VERSION)}\n")

    def download(self, url, path):
        self.urls.append(url)
        shutil.copy(os.path.join(self.served, os.path.basename(url)), path)

    def test_unpacks_a_release_whose_checksum_matches(self):
        self.assertEqual(release.fetch(VERSION, self.dest), self.wrapper)
        self.assertTrue(os.access(self.wrapper, os.X_OK))
        base = f"https://github.com/smol-machines/smolvm/releases/download/v{VERSION}/"
        self.assertEqual(self.urls, [base + release.tarball(VERSION), base + "checksums.sha256"])

    def test_refuses_a_release_whose_checksum_differs(self):
        self.write_sums("f" * 64)
        self.assertIn("checksum mismatch", dies(self, release.fetch, VERSION, self.dest))
        self.assertFalse(os.path.exists(self.wrapper))

    def test_refuses_a_release_missing_from_the_checksums(self):
        with open(os.path.join(self.served, "checksums.sha256"), "w") as f:
            f.write("")
        self.assertIn("checksum mismatch", dies(self, release.fetch, VERSION, self.dest))

    def test_reports_a_failed_download(self):
        os.remove(os.path.join(self.served, "checksums.sha256"))
        self.assertIn("could not download", dies(self, release.fetch, VERSION, self.dest))


class Use(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp = tmp.name
        for patch in (mock.patch.object(release, "TMP", self.tmp),
                      mock.patch.object(release, "fetch", lambda version, dest: f"{dest}/smolvm"),
                      mock.patch.object(smolvm, "SMOLVM_BIN", "smolvm"),
                      mock.patch.dict(os.environ)):
            patch.start()
            self.addCleanup(patch.stop)
        os.environ.pop("SMOLVM_VM_UID_DROP", None)

    def test_points_smolvm_at_the_release_and_its_own_data(self):
        with mock.patch.object(os, "geteuid", return_value=1000):
            tmp = release.use("v" + VERSION)
        self.assertEqual(os.path.dirname(tmp), self.tmp)
        self.assertEqual(smolvm.SMOLVM_BIN, f"{tmp}/smolvm")
        self.assertEqual(os.environ["SMOLVM_DATA_DIR"], f"{tmp}/data")
        self.assertNotIn("SMOLVM_VM_UID_DROP", os.environ)

    def test_turns_the_uid_drop_off_for_root(self):
        with mock.patch.object(os, "geteuid", return_value=0):
            release.use(VERSION)
        self.assertEqual(os.environ["SMOLVM_VM_UID_DROP"], "off")

    def test_refuses_what_is_not_a_version(self):
        self.assertIn("not a smolvm version", dies(self, release.use, "latest"))
        self.assertEqual(os.listdir(self.tmp), [])

    def test_removes_its_directory_when_the_fetch_fails(self):
        with mock.patch.object(release, "fetch", side_effect=SystemExit(1)), \
                self.assertRaises(SystemExit):
            release.use(VERSION)
        self.assertEqual(os.listdir(self.tmp), [])
