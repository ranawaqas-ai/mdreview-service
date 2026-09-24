#!/usr/bin/env python3
"""tool_annotations_selfcheck.py — every MCP tool carries annotations (#394).

WHAT THIS GUARDS. Clients read readOnlyHint / destructiveHint to decide which calls need the
user's confirmation, and the claude.ai Connectors Directory rejects a server with an unannotated
tool. This drives the real wrapper over stdio (tools/list needs no backend) and checks what a
client actually receives, not the Python table: every tool has a title and all three hints, the
hints are consistent, and the destructive set is exactly the tools that remove or replace data.

A new tool with no entry in _ANNOTATIONS already fails at import (KeyError), which this catches as
a wrapper that will not start.

Run: python3 tests/tool_annotations_selfcheck.py     (exit 0 = pass)
"""
import json
import os
import pathlib
import subprocess
import sys

SERVER = pathlib.Path(__file__).resolve().parent.parent / "src" / "mcp_server.py"
DESTRUCTIVE = {"update_source", "delete_review", "delete_comment"}


def list_tools():
    msgs = [
        {"jsonrpc": "2.0", "id": 1, "method": "initialize",
         "params": {"protocolVersion": "2025-06-18", "capabilities": {},
                    "clientInfo": {"name": "selfcheck", "version": "0"}}},
        {"jsonrpc": "2.0", "method": "notifications/initialized"},
        {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
    ]
    env = dict(os.environ, MDREVIEW_NO_AUTO_UPDATE="1", MDREVIEW_BASE="http://127.0.0.1:9")
    r = subprocess.run([sys.executable, str(SERVER)], input="".join(json.dumps(m) + "\n" for m in msgs),
                       capture_output=True, text=True, timeout=30, env=env)
    for line in r.stdout.splitlines():
        msg = json.loads(line)
        if msg.get("id") == 2:
            return msg["result"]["tools"]
    raise SystemExit(f"FAIL wrapper returned no tools/list result (exit {r.returncode}): {r.stderr[-400:]}")


def main():
    fails = []
    tools = list_tools()
    for t in tools:
        name, a = t["name"], t.get("annotations") or {}
        if not t.get("title") or a.get("title") != t.get("title"):
            fails.append(f"{name}: needs a title, the same at top level and in annotations")
        if a.get("openWorldHint") is not False:
            fails.append(f"{name}: openWorldHint must be false (tools touch only mdreview data)")
        if not isinstance(a.get("readOnlyHint"), bool):
            fails.append(f"{name}: readOnlyHint must be set")
        if a.get("readOnlyHint") is False and not isinstance(a.get("destructiveHint"), bool):
            fails.append(f"{name}: a write tool must say whether it is destructive")
        if a.get("readOnlyHint") is True and a.get("destructiveHint"):
            fails.append(f"{name}: read-only and destructive at once")
    got = {t["name"] for t in tools if (t.get("annotations") or {}).get("destructiveHint")}
    if got != DESTRUCTIVE:
        fails.append(f"destructive set is {sorted(got)}, expected {sorted(DESTRUCTIVE)}")
    for f in fails:
        print("FAIL", f)
    if not fails:
        print(f"PASS {len(tools)} tools annotated; destructive = {sorted(DESTRUCTIVE)}")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
