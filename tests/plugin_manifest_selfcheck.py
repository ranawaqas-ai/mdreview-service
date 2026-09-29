#!/usr/bin/env python3
"""plugin_manifest_selfcheck.py — the Claude Code plugin and marketplace manifests (#393).

WHAT THIS GUARDS. `/plugin marketplace add ranawaqas-ai/mdreview-service` reads
.claude-plugin/marketplace.json, then installs plugins/mdreview by COPYING it into the plugin
cache. The plugin holds real copies of src/mcp_server.py and src/mcp/*.py (the plugin directory
rejects symlinks); tests/plugin_wrapper_sync_selfcheck.py guards that they match src/.

So the ways this breaks here are: a manifest field the marketplace needs goes missing, or the
copied plugin no longer starts. The last is checked by running the copied entrypoint.

`claude plugin validate` is the stricter check but needs the claude CLI, which this runner does
not have; run it locally (`claude plugin validate . --strict`) when the manifests change.

Run: python3 tests/plugin_manifest_selfcheck.py     (exit 0 = pass)
"""
import json
import pathlib
import re
import shutil
import subprocess
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parent.parent
MARKETPLACE = ROOT / ".claude-plugin" / "marketplace.json"
SRC = ROOT / "src"


def load(path, fails):
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError) as e:
        fails.append(f"{path.relative_to(ROOT)}: {e}")
        return None


def check_marketplace(fails):
    m = load(MARKETPLACE, fails)
    if m is None:
        return None
    if not m.get("name") or not (m.get("owner") or {}).get("name"):
        fails.append("marketplace.json needs name and owner.name")
    plugins = m.get("plugins") or []
    entry = next((p for p in plugins if p.get("name") == "mdreview"), None)
    if entry is None:
        fails.append("marketplace.json lists no `mdreview` plugin")
        return None
    return (MARKETPLACE.parent.parent / entry.get("source", "")).resolve()


def check_plugin(plugin_dir, fails):
    p = load(plugin_dir / ".claude-plugin" / "plugin.json", fails)
    if p is None:
        return
    if p.get("name") != "mdreview":
        fails.append("plugin.json name must match the marketplace entry (`mdreview`)")
    if not re.fullmatch(r"\d+\.\d+\.\d+", p.get("version", "")):
        fails.append("plugin.json version must be semver; it is the plugin's update channel")

    server = (p.get("mcpServers") or {}).get("mdreview") or {}
    if server.get("args") != ["${CLAUDE_PLUGIN_ROOT}/mcp_server.py"]:
        fails.append("mcpServers.mdreview must run ${CLAUDE_PLUGIN_ROOT}/mcp_server.py, the shim "
                     "whose sys.path puts our `mcp` package ahead of a pip-installed `mcp` SDK")
    env = server.get("env") or {}
    # The plugin version is the update channel; the wrapper must not rewrite itself in the cache.
    if env.get("MDREVIEW_NO_AUTO_UPDATE") != "1":
        fails.append("mcpServers.mdreview.env must set MDREVIEW_NO_AUTO_UPDATE=1")
    token_ref = re.fullmatch(r"\$\{user_config\.(\w+)\}", env.get("MDREVIEW_TOKEN", ""))
    option = (p.get("userConfig") or {}).get(token_ref.group(1)) if token_ref else None
    if not option or option.get("sensitive") is not True:
        fails.append("MDREVIEW_TOKEN must come from a userConfig option marked sensitive "
                     "(stored in the keychain, never in settings.json)")

    check_listing_files(plugin_dir, p, fails)


def check_listing_files(plugin_dir, manifest, fails):
    """What Anthropic's plugin directory blocks on (docs: plugins/pre-submission-checklist)."""
    readme = plugin_dir / "README.md"
    if not readme.is_file():
        fails.append("plugins/mdreview/README.md is missing (the directory blocks without one)")
    else:
        prose = re.sub(r"```.*?```", "", readme.read_text(), flags=re.S)
        if len(prose.split()) < 40:
            fails.append("plugins/mdreview/README.md needs 40+ words outside code blocks")
    if not manifest.get("license") and not (plugin_dir / "LICENSE").is_file():
        fails.append("plugin.json needs a license field or the plugin folder a LICENSE file")
    for field in ("description", "author", "version"):
        if not manifest.get(field):
            fails.append(f"plugin.json needs {field} (the directory warns without it)")
    junk = {".DS_Store", "Thumbs.db", "desktop.ini", "__MACOSX"}
    for path in plugin_dir.rglob("*"):
        if path.name in junk:
            fails.append(f"remove {path.relative_to(ROOT)} (the directory blocks system files)")


def check_installed_copy_runs(plugin_dir, fails):
    """Copy the plugin the way an install does and start it from there."""
    scratch = ROOT / ".scratch"   # gitignored; keeps the copy inside the project, not the OS temp dir
    scratch.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(dir=scratch) as tmp:
        cache = pathlib.Path(tmp) / "mdreview"
        shutil.copytree(plugin_dir, cache,
                        ignore=shutil.ignore_patterns("__pycache__"))
        got = run_version(cache / "mcp_server.py")
    want = run_version(SRC / "mcp_server.py")
    if got is None or got != want:
        fails.append(f"installed copy reports {got}, src/ reports {want}")


def run_version(entry):
    r = subprocess.run([sys.executable, str(entry), "--print-version"],
                       capture_output=True, text=True, timeout=30)
    return json.loads(r.stdout) if r.returncode == 0 and r.stdout.strip() else None


def main():
    fails = []
    plugin_dir = check_marketplace(fails)
    if plugin_dir is not None:
        check_plugin(plugin_dir, fails)
        if not fails:
            check_installed_copy_runs(plugin_dir, fails)
    for f in fails:
        print("FAIL", f)
    if not fails:
        print("PASS plugin manifests and an installed copy that starts")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
