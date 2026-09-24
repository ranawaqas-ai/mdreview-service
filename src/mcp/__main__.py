"""JSON-RPC 2.0 stdio transport + lifecycle dispatch — the `python -m mcp` entry point.

Reads newline-delimited JSON-RPC from stdin, dispatches initialize / tools/list / tools/call / ping,
and writes responses to stdout. The agent-visible schema surface lives in tools.py; the HTTP routing
in client.py. `--print-version` prints the on-disk identity (the staleness comparand) and exits.
"""
import os
import sys
import json

from .tools import SERVER_INFO
from .client import ServiceClient, BASE, TOKEN, open_review, OPEN_IN_BROWSER
from . import rpc


# ---- JSON-RPC stdio framing (isolated so a spec change is a one-function fix) ----
def write_message(msg):
    sys.stdout.write(json.dumps(msg) + "\n")
    sys.stdout.flush()


def read_messages():
    """Yield parsed JSON-RPC messages from stdin, one per line; stop cleanly on EOF."""
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            yield json.loads(line)
        except json.JSONDecodeError:
            # Malformed line with no id we can answer; skip rather than crash the stream.
            continue


def dispatch(msg, client):
    resp = rpc.handle(msg, client)
    if resp is None:
        return
    write_message(resp)
    if OPEN_IN_BROWSER and msg.get("method") == "tools/call" \
            and (msg.get("params") or {}).get("name") == "create_review" \
            and not resp.get("result", {}).get("isError", True):
        open_review(resp["result"]["content"][0]["text"])   # opt-in local-browser pop, after the result is sent


def main():
    if "--print-version" in sys.argv:
        # on-disk comparand for staleness checks: print what THIS code would serve, then exit.
        print(json.dumps({"version": SERVER_INFO["version"], "tools_hash": SERVER_INFO["tools_hash"]}))
        return
    if os.environ.get("MDREVIEW_NO_AUTO_UPDATE", "").lower() not in ("1", "true", "yes"):
        try:
            from . import update
            update.maybe_self_update()  # managed installs track their server; dev trees untouched (#90)
        except Exception:
            pass  # self-update is best-effort — never block MCP startup on it
    client = ServiceClient(BASE, TOKEN)
    for msg in read_messages():
        try:
            dispatch(msg, client)
        except Exception as e:  # never let one bad message kill the stream
            rid = msg.get("id") if isinstance(msg, dict) else None
            if rid is not None:
                write_message(rpc.error(rid, -32603, "Internal error: %s" % e))


if __name__ == "__main__":
    main()
