#!/usr/bin/env python3
"""test_reapx_paths.py — the data-dir seam (A.2.1.2). Hermes-free.

The contract: explicit ``--data-dir`` > ``REAPX_DATA_DIR`` env >
``<repo>/data`` (the standalone default, unchanged). Zero standalone
behavior change; the plugin passes its plugin-data dir.
"""
import os
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = REPO_ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))

import reapx_paths  # noqa: E402


class ResolveDataDirTests(unittest.TestCase):
    def setUp(self):
        self._env = os.environ.pop("REAPX_DATA_DIR", None)
        self.addCleanup(lambda: os.environ.pop("REAPX_DATA_DIR", None))
        if self._env:
            os.environ["REAPX_DATA_DIR"] = self._env

    def test_default_is_repo_data(self):
        self.assertEqual(reapx_paths.resolve_data_dir(),
                         reapx_paths.DEFAULT_DATA_DIR)
        self.assertEqual(reapx_paths.DEFAULT_DATA_DIR,
                         SCRIPTS.parent / "data")

    def test_env_override(self):
        with tempfile.TemporaryDirectory() as td:
            os.environ["REAPX_DATA_DIR"] = td
            self.assertEqual(reapx_paths.resolve_data_dir(), Path(td))

    def test_explicit_arg_beats_env(self):
        with tempfile.TemporaryDirectory() as td1, \
                tempfile.TemporaryDirectory() as td2:
            os.environ["REAPX_DATA_DIR"] = td1
            self.assertEqual(reapx_paths.resolve_data_dir(td2), Path(td2))

    def test_env_empty_means_default(self):
        os.environ["REAPX_DATA_DIR"] = ""
        self.assertEqual(reapx_paths.resolve_data_dir(),
                         reapx_paths.DEFAULT_DATA_DIR)

    def test_data_file(self):
        with tempfile.TemporaryDirectory() as td:
            self.assertEqual(reapx_paths.data_file("ledger.json", td),
                             Path(td) / "ledger.json")

    def test_strip_data_dir_flag(self):
        self.assertEqual(
            reapx_paths.strip_data_dir_flag(["--data-dir", "/x", "y"]),
            ["y"])


class AddDataDirArgTests(unittest.TestCase):
    def test_declares_the_flag(self):
        import argparse
        p = argparse.ArgumentParser(prog="t")
        reapx_paths.add_data_dir_arg(p)
        a = p.parse_args(["--data-dir", "/tmp/x"])
        self.assertEqual(a.data_dir, "/tmp/x")
        b = p.parse_args([])
        self.assertIsNone(b.data_dir)


class StandaloneShapeTests(unittest.TestCase):
    """The seam never changes the standalone: nothing in scripts/ resolves a
    data dir at import time except through reapx_paths (default = repo
    data/)."""

    def test_reapx_paths_has_no_engine_imports(self):
        src = (SCRIPTS / "reapx_paths.py").read_text(encoding="utf-8")
        self.assertNotIn("import ledger", src)
        self.assertNotIn("import categorize", src)
        self.assertNotIn("import browser_locator", src)

    def test_map_main_takes_argv(self):
        src = (SCRIPTS / "map_bookmarks_to_repos.py").read_text(
            encoding="utf-8")
        self.assertIn("def main(argv=None):", src)
        self.assertIn("add_data_dir_arg(parser)", src)
        self.assertNotIn("INPUT_FILE.exists", src)  # no module-constant read


if __name__ == "__main__":
    unittest.main(verbosity=2)
