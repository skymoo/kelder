# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Adam Mercer
"""Finding and reading a project's .kelder.toml and the user's file."""
import contextlib
import io
import os
import tempfile
import unittest
from unittest import mock

from kelder import config


class Load(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp = os.path.realpath(tmp.name)
        self.proj = os.path.join(self.tmp, "proj")
        os.makedirs(os.path.join(self.proj, "src/deep"))
        # Never the real user's file.
        patch = mock.patch.dict(os.environ, {"XDG_CONFIG_HOME": os.path.join(self.tmp, "xdg")})
        patch.start()
        self.addCleanup(patch.stop)

    def write_user(self, text):
        path = config.user_file()
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w") as f:
            f.write(text)
        return path

    def write(self, text, where="proj"):
        path = os.path.join(self.tmp, where, config.NAME)
        with open(path, "w") as f:
            f.write(text)
        return path

    def dies(self):
        """Load the project's file expecting that to die; returns the message."""
        err = io.StringIO()
        with contextlib.redirect_stderr(err), self.assertRaises(SystemExit):
            config.load(self.proj)
        return err.getvalue()

    def test_no_file(self):
        self.assertEqual(config.load(self.proj), config.Config())

    def test_machine_and_packages(self):
        path = self.write('vm = "homelab"\npackages = [\n    "ansible-lint",\n    "g++",  # why\n]\n')
        self.assertEqual(config.load(self.proj), config.Config(path, "homelab", ["ansible-lint", "g++"]))

    def test_both_keys_are_optional(self):
        path = self.write("")
        self.assertEqual(config.load(self.proj), config.Config(path, None, [], None))

    def test_memory(self):
        path = self.write('memory = "work"\n')
        self.assertEqual(config.load(self.proj), config.Config(path, None, [], "work"))

    def test_only_the_primary_directory_is_checked(self):
        self.write('vm = "above"\n', where="")
        self.write('vm = "below"\n', where="proj/src")
        self.assertEqual(config.load(self.proj), config.Config())
        self.assertEqual(config.load(os.path.join(self.proj, "src/deep")), config.Config())

    def test_invalid_files(self):
        path = self.write("vm = ")
        self.assertIn(f"could not read {path}: ", self.dies())
        for text, message in (
            ('machine = "x"\n', "unknown key 'machine'"),
            ("vm = 3\n", "vm must be a machine name"),
            ('vm = "-f"\n', "vm must be a machine name"),
            ('vm = "a/b"\n', "vm must be a machine name"),
            ('packages = "git"\n', "packages must be a list"),
            ('packages = ["--allow-unauthenticated"]\n', "not a package name: '--allow-unauthenticated'"),
            ("packages = [1]\n", "not a package name: 1"),
            ("memory = 3\n", "memory must be a folder name"),
            ('memory = "../work"\n', "memory must be a folder name"),
            ('memory = "a/b"\n', "memory must be a folder name"),
        ):
            path = self.write(text)
            self.assertIn(f"{path}: {message}", self.dies(), text)

    def test_user_file_covers_a_directory_and_those_below_it(self):
        path = self.write_user(f'[projects."{self.tmp}"]\nvm = "work"\nmemory = "work"\n')
        expected = config.Config(path, "work", [], "work")
        self.assertEqual(config.load(self.proj), expected)
        self.assertEqual(config.load(os.path.join(self.proj, "src/deep")), expected)

    def test_user_file_ignores_other_directories(self):
        self.write_user(f'[projects."{self.proj}/src"]\nvm = "a"\n[projects."{self.proj}x"]\nvm = "b"\n')
        self.assertEqual(config.load(self.proj), config.Config())

    def test_user_file_longest_match_wins(self):
        path = self.write_user(f'[projects."{self.tmp}"]\nvm = "outer"\npackages = ["git"]\n'
                               f'[projects."{self.proj}"]\nmemory = "inner"\n')
        self.assertEqual(config.load(self.proj), config.Config(path, None, [], "inner"))

    def test_user_file_expands_home(self):
        path = self.write_user('[projects."~/proj"]\nvm = "work"\n')
        with mock.patch.dict(os.environ, {"HOME": self.tmp}):
            self.assertEqual(config.load(self.proj), config.Config(path, "work"))

    def test_project_file_replaces_the_user_entry(self):
        self.write_user(f'[projects."{self.proj}"]\nvm = "work"\nmemory = "work"\n')
        path = self.write('packages = ["git"]\n')
        self.assertEqual(config.load(self.proj), config.Config(path, None, ["git"], None))

    def test_invalid_user_files(self):
        for text, message in (
            ('vm = "x"\n', "unknown key 'vm' (the only key is projects)"),
            ("projects = 3\n", "projects must be a table of directories"),
            ('[projects."rel/dir"]\n', 'projects."rel/dir": the directory must be an absolute path'),
            ('[projects]\n"/x" = 3\n', 'projects."/x" must be a table'),
            ('[projects."/elsewhere"]\nvm = "-f"\n', 'projects."/elsewhere": vm must be a machine name'),
            ('[projects."/elsewhere"]\nmachine = "x"\n', "projects.\"/elsewhere\": unknown key 'machine'"),
        ):
            path = self.write_user(text)
            self.assertIn(f"{path}: {message}", self.dies(), text)


if __name__ == "__main__":
    unittest.main()
