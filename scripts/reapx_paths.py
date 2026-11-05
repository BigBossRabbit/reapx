#!/usr/bin/env python3
"""reapx_paths.py — the data-dir seam (the one-tree, two-install-shapes contract).

Everything the engine writes (x_bookmarks.json, starred_repos.json,
new_repos.json, categorized_repos.json, opportunities.json, ledger.json) lives
under :func:`resolve_data_dir`, resolved in this order:

  1. an explicit ``--data-dir <path>`` argument (what the plugin layer passes —
     the host-owned ``plugin_data_dir("reapx")``: outside the install tree,
     survives ``hermes plugins update``/``remove``, follows the active profile);
  2. the ``REAPX_DATA_DIR`` environment variable (a standalone debugging
     affordance; never required);
  3. the standalone default ``<repo>/data`` — the pre-plugin behaviour,
     bit-for-bit unchanged.

This module must never import another engine script (it is imported first by
everything else). It is the ONLY place the engine learns about a data root.
"""
from __future__ import annotations

import os
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent

# The standalone default (repo-relative, exactly as before the seam).
DEFAULT_DATA_DIR = SCRIPT_DIR.parent / "data"

# The data-dir flag every seam-enabled script declares (see add_data_dir_arg).
DATA_DIR_FLAG = "--data-dir"


def resolve_data_dir(data_dir=None) -> Path:
    """Resolve the engine's data root: explicit arg > ``REAPX_DATA_DIR`` env >
    ``<repo>/data`` (the standalone default)."""
    if data_dir:
        return Path(data_dir).expanduser()
    env = os.environ.get("REAPX_DATA_DIR")
    if env:
        return Path(env).expanduser()
    return DEFAULT_DATA_DIR


def data_file(name, data_dir=None) -> Path:
    """``resolve_data_dir(data_dir) / name`` — the one way engine scripts name a
    data file (callers must not build their own data paths)."""
    return resolve_data_dir(data_dir) / name


def add_data_dir_arg(parser) -> None:
    """Declare ``--data-dir`` on a script's argument parser (seam-enabled
    scripts call this so the flag exists everywhere it is accepted)."""
    parser.add_argument(
        DATA_DIR_FLAG, default=None,
        help="engine data directory (default: the repo's data/; the plugin "
             "layer passes its plugin-data dir)")


def strip_data_dir_flag(argv) -> list:
    """Drop a leading ``--data-dir <path>`` pair from *argv* for scripts that
    pre-scan it (none today; kept for the plugin driver's error paths)."""
    out = list(argv or [])
    if DATA_DIR_FLAG in out:
        i = out.index(DATA_DIR_FLAG)
        del out[i:i + 2]
    return out


if __name__ == "__main__":  # tiny self-test (no engine imports)
    import sys

    here = resolve_data_dir()
    ok = here == DEFAULT_DATA_DIR
    print(f"default data dir : {here} {'OK' if ok else 'MISMATCH'}")
    os.environ["REAPX_DATA_DIR"] = "/tmp/reapx-env-probe"
    ok = str(resolve_data_dir()) == "/tmp/reapx-env-probe"
    print(f"env override    : {resolve_data_dir()} {'OK' if ok else 'MISMATCH'}")
    ok = str(resolve_data_dir("/tmp/reapx-explicit")) == "/tmp/reapx-explicit"
    print(f"explicit arg    : {resolve_data_dir('/tmp/reapx-explicit')} "
          f"{'OK' if ok else 'MISMATCH'}")
    del os.environ["REAPX_DATA_DIR"]
    sys.exit(0 if ok else 1)
