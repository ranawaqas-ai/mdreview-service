#!/usr/bin/env python3
"""plugin_wrapper_sync_selfcheck.py: plugins/mdreview mirrors src/mcp byte for byte (#393).

The Claude plugin directory rejects symlinks and an install copies only the plugin folder, so the
plugin holds real copies of the wrapper. Copies can drift, so this fails when a wrapper file
differs from src/, is missing, or is extra, and when any symlink exists under plugins/.

Fix every failure by running: python3 scripts/sync_plugin_wrapper.py

Run: python3 tests/plugin_wrapper_sync_selfcheck.py     (exit 0 = pass)
"""
import importlib.util
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
PLUGIN = ROOT / "plugins" / "mdreview"
# Plugin files that are not copies of src/: the manifest folder and the docs.
NOT_WRAPPER = {".claude-plugin", "README.md", "LICENSE"}
FIX ="run `python3 scripts/sync_plugin_wrapper.py` and commit the result"


def load_sync():
    spec = importlib.util.spec_from_file_location("sync_plugin_wrapper",
                                                  ROOT / "scripts" / "sync_plugin_wrapper.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def actual_files():
    found = {}
    for p in PLUGIN.rglob("*"):
        rel = p.relative_to(PLUGIN)
        if p.is_dir() or p.is_symlink() or "__pycache__" in rel.parts or rel.parts[0] in NOT_WRAPPER:
            continue
        found[rel.as_posix()] = p.read_bytes()
    return found


def main():
    fails = []
    for p in (ROOT / "plugins").rglob("*"):
        if p.is_symlink():
            fails.append(f"{p.relative_to(ROOT)} is a symlink; the plugin directory rejects them")

    want = load_sync().expected()
    have = actual_files()
    for rel in sorted(want.keys() - have.keys()):
        fails.append(f"plugins/mdreview/{rel} is missing")
    for rel in sorted(have.keys() - want.keys()):
        fails.append(f"plugins/mdreview/{rel} is not part of the wrapper (extra file)")
    for rel in sorted(want.keys() & have.keys()):
        if want[rel] != have[rel]:
            fails.append(f"plugins/mdreview/{rel} differs from its src/ original")

    for f in fails:
        print("FAIL", f)
    if fails:
        print(f"FAIL plugin wrapper is out of sync with src/: {FIX}")
        return 1
    print(f"PASS plugins/mdreview matches src/ ({len(want)} files, no symlinks)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
