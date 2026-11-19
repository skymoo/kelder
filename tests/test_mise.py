# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Adam Mercer
"""Recognising a mise project and reading what the guest's mise reports."""
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from kelder import machine, mise


class Mise(unittest.TestCase):
    def test_a_directory_with_a_mise_config_is_a_mise_project(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertFalse(mise.configured(tmp))
            (Path(tmp) / ".config").mkdir()
            self.assertFalse(mise.configured(tmp))
            for name in ("mise.toml", ".tool-versions", ".config/mise.toml"):
                with self.subTest(name):
                    (Path(tmp) / name).touch()
                    self.assertTrue(mise.configured(tmp))
                    (Path(tmp) / name).unlink()

    def missing(self, rc, out):
        with mock.patch.object(machine, "guest", return_value=(rc, out)) as guest:
            result = mise.missing("/proj")
        guest.assert_called_once_with("bash", "-c", mise.MISSING, "_", "/proj", capture="out")
        return result

    def test_missing_tools_are_named_with_their_versions(self):
        out = ("jq   1.7.1 (missing)  /proj/mise.toml  1.7.1\n"
               "zig  0.15 (missing)   /proj/mise.toml  0.15\n")
        self.assertEqual(self.missing(0, out), ["jq@1.7.1", "zig@0.15"])
        self.assertEqual(self.missing(0, ""), [])
        self.assertEqual(self.missing(1, ""), [])   # mise's own error is on the terminal

    def test_a_guest_without_mise_is_told_apart(self):
        self.assertIsNone(self.missing(127, ""))
