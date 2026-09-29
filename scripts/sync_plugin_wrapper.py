#!/usr/bin/env python3
"""Rebuild the wrapper files under plugins/mdreview/ from src/ (stdlib only).

The Claude plugin directory rejects symlinks, and a plugin install copies only the plugin folder,
so plugins/mdreview carries real copies of src/mcp_server.py and src/mcp/*.py. Run this after any
change under src/mcp/ or src/mcp_server.py; tests/plugin_wrapper_sync_selfcheck.py fails CI if
the copies drift.

update.py and bundle.py are left out on purpose. They are the managed-install self-update, which
the plugin never runs (it sets MDREVIEW_NO_AUTO_UPDATE=1 and its version is its update channel),
and `__main__` imports them inside try/except.

Run: python3 scripts/sync_plugin_wrapper.py
"""
import pathlib
import shutil
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
SRC = ROOT / "src"
PLUGIN = ROOT / "plugins" / "mdreview"
OMIT = {"update.py", "bundle.py"}


def expected():
    """Map of plugin-relative path to the bytes the plugin must contain."""
    files = {"mcp_server.py": (SRC / "mcp_server.py").read_bytes()}
    for f in sorted((SRC / "mcp").glob("*.py")):
        if f.name not in OMIT:
            files[f"mcp/{f.name}"] = f.read_bytes()
    return files


def main():
    want = expected()
    mcp_dir = PLUGIN / "mcp"
    if mcp_dir.is_symlink():
        mcp_dir.unlink()
    else:
        shutil.rmtree(mcp_dir, ignore_errors=True)
    (PLUGIN / "mcp_server.py").unlink(missing_ok=True)
    for rel, data in want.items():
        dest = PLUGIN / rel
        dest.parent.mkdir(exist_ok=True)
        dest.write_bytes(data)
    print(f"synced {len(want)} files into {PLUGIN.relative_to(ROOT)}")


if __name__ == "__main__":
    sys.exit(main())
