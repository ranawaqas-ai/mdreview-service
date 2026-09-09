#!/usr/bin/env python3
"""mcp_smoke.py — dependency-free smoke for mcp_server.py (the MCP analogue of render-smoke.sh).

Stdlib only (json + subprocess + os/sys; NO jq, NO pip): drives mcp_server.py over stdio with a
handcrafted JSON-RPC sequence and asserts the protocol surface, the tools/call envelope, the
error paths, and a live create_review -> update_source round-trip.

Usage:  MDREVIEW_BASE=http://localhost:8137 python3 mcp_smoke.py
Exit 0 = all pass; nonzero = a failure (with the failing assertion named).
"""
import os
import sys
import json
import subprocess
import urllib.request

BASE = os.environ.get("MDREVIEW_BASE", "http://localhost:8137")
HERE = os.path.dirname(os.path.abspath(__file__))
SERVER = os.path.join(HERE, "..", "src", "mcp_server.py")   # tests/ -> ../src/mcp_server.py (MR-078)

INIT = {"jsonrpc": "2.0", "id": 1, "method": "initialize",
        "params": {"protocolVersion": "2025-06-18", "capabilities": {},
                   "clientInfo": {"name": "mcp_smoke", "version": "0"}}}
INITED = {"jsonrpc": "2.0", "method": "notifications/initialized"}

_fails = []


def check(label, cond):
    print(("  ok  " if cond else "  FAIL") + " " + label)
    if not cond:
        _fails.append(label)


def drive(messages):
    """Run mcp_server.py with the given JSON-RPC messages on stdin; return parsed responses."""
    proc = subprocess.run(
        [sys.executable, SERVER],
        input="".join(json.dumps(m) + "\n" for m in messages),
        capture_output=True, text=True,
        env={**os.environ, "MDREVIEW_BASE": BASE},
        timeout=60,
    )
    return [json.loads(line) for line in proc.stdout.splitlines() if line.strip()]


def main():
    print("mcp_smoke against MDREVIEW_BASE=%s" % BASE)

    # 1. protocol surface (no service needed for initialize/tools_list, but a running service is
    #    required for the round-trip below; we keep one sequence for simplicity).
    base = [INIT, INITED, {"jsonrpc": "2.0", "id": 2, "method": "tools/list"}]
    out = drive(base)
    check("notification gets no response (2 requests -> 2 responses)", len(out) == 2)
    init = out[0].get("result", {})
    check("initialize protocolVersion == 2025-06-18", init.get("protocolVersion") == "2025-06-18")
    check("capabilities are tools-only", init.get("capabilities") == {"tools": {}})
    check("serverInfo.name == mdreview-mcp", init.get("serverInfo", {}).get("name") == "mdreview-mcp")
    check("initialize surfaces the workflow as instructions",
          bool(init.get("instructions")) and "list_comments" in init.get("instructions", ""))
    tools = out[1].get("result", {}).get("tools", [])
    names = {t.get("name") for t in tools}
    expected = {"create_review", "list_reviews", "get_review", "get_source", "get_feedback",
                "get_status", "update_source", "get_history", "get_git_url", "delete_review",
                "attach_asset", "list_assets",
                "create_comment", "list_comments", "get_comment", "reply_to_comment", "resolve_comment",
                "delete_comment", "hand_back", "ping_working", "server_info"}
    check("tools/list returns exactly the 21 tools", names == expected)
    check("each tool has a description + object inputSchema",
          all(t.get("description") and t.get("inputSchema", {}).get("type") == "object" for t in tools))
    # the comment tools must encode the agent workflow in their descriptions (the brief's expectations)
    desc = {t["name"]: t.get("description", "").lower() for t in tools}
    check("list_comments description says call it FIRST",
          "first" in desc.get("list_comments", ""))
    check("reply_to_comment description says reply WITHOUT resolving",
          "without" in desc.get("reply_to_comment", ""))
    check("resolve_comment description says justification optional + reviewer can reopen",
          "optional" in desc.get("resolve_comment", "") and "reopen" in desc.get("resolve_comment", ""))
    # MR-053: the turn-baton tools encode their workflow in their descriptions
    check("hand_back description: returns the turn to the reviewer (done/blocked)",
          "reviewer" in desc.get("hand_back", "") and (
              "done" in desc.get("hand_back", "") or "blocked" in desc.get("hand_back", "")))
    check("ping_working description: a lease that backs off on a foreign owner (409)",
          "lease" in desc.get("ping_working", "") and (
              "409" in desc.get("ping_working", "") or "back off" in desc.get("ping_working", "")))

    # --- MR-042: staleness signal + discoverability (no service needed; all from the static surface) ---
    instr = init.get("instructions", "").lower()
    # discoverability — an agent reading tools/list + instructions can self-find the paths that tripped it
    check("attach_asset description steers to `path`", "path" in desc.get("attach_asset", ""))
    check("get_source description says when to read the draft",
          "draft" in desc.get("get_source", "") or "source" in desc.get("get_source", ""))
    check("INSTRUCTIONS name path-attach, get_source, and the comment loop",
          "attach_asset" in instr and "get_source" in instr and "list_comments" in instr)
    # authoring discoverability — the agent must learn to author for the renderer (mermaid, math, …)
    check("create_review description tells the agent to use the viewer renderer (mermaid/diagram)",
          "mermaid" in desc.get("create_review", "") and (
              "diagram" in desc.get("create_review", "") or "render" in desc.get("create_review", "")))
    check("update_source description carries the same author-to-renderer rule",
          "mermaid" in desc.get("update_source", ""))
    check("INSTRUCTIONS tell the agent to author for the renderer (mermaid, not ASCII/plain fence)",
          "mermaid" in instr and "ascii" in instr)
    # staleness signal — surfaced via serverInfo + a server_info tool; honest scoping (SHOULD-1)
    check("serverInfo carries a tools_hash", bool(init.get("serverInfo", {}).get("tools_hash")))
    check("server_info description: human/CI compares to --print-version (not the agent)",
          "--print-version" in desc.get("server_info", "") and (
              "human" in desc.get("server_info", "") or "human/ci" in desc.get("server_info", "")))
    check("no surface claims the agent self-detects staleness",
          "agent detects stale" not in instr and "self-detect" not in instr
          and "agent detects stale" not in desc.get("server_info", ""))
    # three-way tools_hash identity: serverInfo == server_info tool == --print-version (one _tools_hash())
    si = drive(base + [{"jsonrpc": "2.0", "id": 30, "method": "tools/call",
                        "params": {"name": "server_info", "arguments": {}}}])
    tool_hash = None
    try:
        tool_hash = json.loads(si[-1]["result"]["content"][0]["text"]).get("tools_hash")
    except Exception:
        pass
    disk_hash = None
    try:
        pv = subprocess.run([sys.executable, SERVER, "--print-version"], capture_output=True, text=True, timeout=15)
        disk_hash = json.loads(pv.stdout).get("tools_hash")
    except Exception:
        pass
    init_hash = init.get("serverInfo", {}).get("tools_hash")
    check("three-way tools_hash identity (serverInfo == server_info tool == --print-version)",
          bool(init_hash) and init_hash == tool_hash == disk_hash)
    check("server_info tool reports tool_count == 21 (local dispatch, no service touched)",
          isinstance(tool_hash, str) and json.loads(si[-1]["result"]["content"][0]["text"]).get("tool_count") == 21)
    check("create_comment description: author a NEW comment anchored to quoted_text",
          "quoted_text" in desc.get("create_comment", "") and "comment" in desc.get("create_comment", ""))

    # 2. happy-path envelope + round-trip (needs a running service)
    out = drive(base + [{"jsonrpc": "2.0", "id": 3, "method": "tools/call",
                         "params": {"name": "create_review",
                                    "arguments": {"markdown": "# smoke\n\nx", "title": "mcp_smoke",
                                                  "project": "mcp-smoke", "session": "ci"}}}])
    res = out[-1].get("result", {})
    envelope_ok = (res.get("content", [{}])[0].get("type") == "text" and not res.get("isError"))
    check("tools/call create_review -> text-content result, isError false", envelope_ok)
    rid = None
    if envelope_ok:
        try:
            rid = json.loads(res["content"][0]["text"]).get("id")
        except Exception:
            pass
    check("create_review result text parses as JSON with an id", bool(rid))

    if rid:
        out = drive(base + [{"jsonrpc": "2.0", "id": 4, "method": "tools/call",
                             "params": {"name": "update_source",
                                        "arguments": {"id": rid, "markdown": "# smoke\n\nrevised"}}}])
        upd = out[-1].get("result", {})
        rev = None
        try:
            rev = json.loads(upd["content"][0]["text"]).get("revision")
        except Exception:
            pass
        check("update_source round-trip -> revision >= 1", isinstance(rev, int) and rev >= 1)

    # MR-099: create_review(kind="latex") must reach the API (the client whitelist passes it) and
    # the created review must carry kind=latex in its metadata.
    out = drive(base + [{"jsonrpc": "2.0", "id": 45, "method": "tools/call",
                         "params": {"name": "create_review",
                                    "arguments": {"markdown": "\\documentclass{article}\\begin{document}x\\end{document}",
                                                  "title": "mcp_smoke_latex", "kind": "latex"}}}])
    lres = out[-1].get("result", {})
    lrid = None
    try:
        lrid = json.loads(lres["content"][0]["text"]).get("id")
    except Exception:
        pass
    check("create_review kind=latex -> id returned", bool(lrid))
    if lrid:
        out = drive(base + [{"jsonrpc": "2.0", "id": 46, "method": "tools/call",
                             "params": {"name": "get_review", "arguments": {"id": lrid}}}])
        kind = None
        try:
            kind = json.loads(out[-1]["result"]["content"][0]["text"]).get("kind")
        except Exception:
            pass
        check("get_review on the latex review reports kind=latex", kind == "latex")

    # MR-106: create_review(kind="latex", template="ieee") must reach the API (whitelist passes
    # `template`) and the created review must carry template=ieee in its metadata.
    out = drive(base + [{"jsonrpc": "2.0", "id": 47, "method": "tools/call",
                         "params": {"name": "create_review",
                                    "arguments": {"markdown": "", "title": "mcp_smoke_template",
                                                  "kind": "latex", "template": "ieee"}}}])
    trid = None
    try:
        trid = json.loads(out[-1]["result"]["content"][0]["text"]).get("id")
    except Exception:
        pass
    check("create_review kind=latex template=ieee -> id returned", bool(trid))
    if trid:
        out = drive(base + [{"jsonrpc": "2.0", "id": 48, "method": "tools/call",
                             "params": {"name": "get_review", "arguments": {"id": trid}}}])
        tmpl = None
        try:
            tmpl = json.loads(out[-1]["result"]["content"][0]["text"]).get("template")
        except Exception:
            pass
        check("get_review on the templated review reports template=ieee", tmpl == "ieee")

    if rid:
        # MR-053: turn-baton tools — ping_working (claim the lease) then hand_back (return the turn)
        out = drive(base + [{"jsonrpc": "2.0", "id": 40, "method": "tools/call",
                             "params": {"name": "ping_working",
                                        "arguments": {"document_id": rid, "owner": "ci-agent",
                                                      "message": "on it"}}}])
        pw = out[-1].get("result", {})
        pw_owner = None
        try:
            pw_owner = json.loads(pw["content"][0]["text"]).get("agent_status", {}).get("owner")
        except Exception:
            pass
        check("ping_working -> lease claimed (agent_status.owner set), isError false",
              pw_owner == "ci-agent" and not pw.get("isError"))

        out = drive(base + [{"jsonrpc": "2.0", "id": 41, "method": "tools/call",
                             "params": {"name": "hand_back",
                                        "arguments": {"document_id": rid,
                                                      "message": "addressed in ci", "state": "done"}}}])
        hb = out[-1].get("result", {})
        hb_meta = {}
        try:
            hb_meta = json.loads(hb["content"][0]["text"])
        except Exception:
            pass
        check("hand_back -> turn=reviewer, agent_status.state=done, isError false",
              hb_meta.get("turn") == "reviewer"
              and hb_meta.get("agent_status", {}).get("state") == "done"
              and not hb.get("isError"))

        # get_source returns the draft we just pushed (text content, not JSON)
        out = drive(base + [{"jsonrpc": "2.0", "id": 10, "method": "tools/call",
                             "params": {"name": "get_source", "arguments": {"id": rid}}}])
        src = out[-1].get("result", {}).get("content", [{}])[0].get("text", "")
        check("get_source -> the current draft markdown", "revised" in src)

        # attach_asset -> list_assets round-trip (a 1x1 png by base64)
        pix = ("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8z8BQ"
               "DwAEhQGAhKmMIQAAAABJRU5ErkJggg==")
        out = drive(base + [{"jsonrpc": "2.0", "id": 8, "method": "tools/call",
                             "params": {"name": "attach_asset",
                                        "arguments": {"id": rid, "name": "/assets/pixel.png",
                                                      "content_b64": pix}}}])
        att = out[-1].get("result", {})
        stored = None
        try:
            stored = json.loads(att["content"][0]["text"]).get("stored")
        except Exception:
            pass
        check("attach_asset -> stored sha1+ext, isError false",
              bool(stored) and not att.get("isError"))

        # attach_asset by PATH: the wrapper reads + encodes the file locally (no base64 in context)
        import tempfile
        with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tf:
            tf.write(__import__("base64").b64decode(pix)); tmp_png = tf.name
        out = drive(base + [{"jsonrpc": "2.0", "id": 11, "method": "tools/call",
                             "params": {"name": "attach_asset",
                                        "arguments": {"id": rid, "name": "/assets/by-path.png",
                                                      "path": tmp_png}}}])
        ap = out[-1].get("result", {})
        path_stored = None
        try:
            path_stored = json.loads(ap["content"][0]["text"]).get("stored")
        except Exception:
            pass
        os.unlink(tmp_png)
        check("attach_asset by path -> wrapper reads the file, stored returned, isError false",
              bool(path_stored) and not ap.get("isError"))
        out = drive(base + [{"jsonrpc": "2.0", "id": 9, "method": "tools/call",
                             "params": {"name": "list_assets", "arguments": {"id": rid}}}])
        listed = []
        try:
            listed = json.loads(out[-1]["result"]["content"][0]["text"]).get("assets", [])
        except Exception:
            pass
        check("list_assets -> includes the attached asset's stored name",
              any(a.get("stored") == stored for a in listed))

        # create_comment via the MCP tool: an agent authors a comment anchored to a quote -> list shows it
        cc = drive(base + [{"jsonrpc": "2.0", "id": 12, "method": "tools/call",
                            "params": {"name": "create_comment",
                                       "arguments": {"document_id": rid, "quoted_text": "revised",
                                                     "text": "agent review note"}}}])
        new_cid = None
        try:
            new_cid = json.loads(cc[-1]["result"]["content"][0]["text"]).get("comment_id")
        except Exception:
            pass
        check("create_comment -> a new comment_id, isError false",
              bool(new_cid) and not cc[-1].get("result", {}).get("isError"))
        lc = drive(base + [{"jsonrpc": "2.0", "id": 13, "method": "tools/call",
                            "params": {"name": "list_comments", "arguments": {"document_id": rid}}}])
        listed_c = []
        try:
            listed_c = json.loads(lc[-1]["result"]["content"][0]["text"]).get("comments", [])
        except Exception:
            pass
        check("create_comment round-trip -> list_comments shows it (anchored, role agent)",
              any(c.get("comment_id") == new_cid
                  and c.get("anchor", {}).get("quoted_text") == "revised"
                  and (c.get("thread") or [{}])[0].get("role") == "agent" for c in listed_c))

        # delete_comment: hard-remove the comment we just created -> list no longer shows it
        if new_cid:
            dl = drive(base + [{"jsonrpc": "2.0", "id": 14, "method": "tools/call",
                                "params": {"name": "delete_comment",
                                           "arguments": {"document_id": rid, "comment_id": new_cid}}}])
            deleted_ok = not dl[-1].get("result", {}).get("isError")
            lc2 = drive(base + [{"jsonrpc": "2.0", "id": 15, "method": "tools/call",
                                 "params": {"name": "list_comments",
                                            "arguments": {"document_id": rid, "status": "all"}}}])
            still_there = True
            try:
                still_there = any(c.get("comment_id") == new_cid for c in
                                  json.loads(lc2[-1]["result"]["content"][0]["text"]).get("comments", []))
            except Exception:
                pass
            check("delete_comment -> hard-removes it; list no longer shows it",
                  deleted_ok and not still_there)

        # comment round-trip: seed a comment over HTTP (the viewer path), then exercise the
        # four agent tools (list -> get -> reply -> resolve) and the open/resolved filter.
        cid = None
        try:
            req = urllib.request.Request(
                "%s/api/reviews/%s/comments" % (BASE, rid),
                data=json.dumps({"anchor": {"quoted_text": "smoke", "block_num": "1"},
                                 "text": "please clarify"}).encode("utf-8"),
                headers={"Content-Type": "application/json"}, method="POST")
            # Bypass any system/HTTP proxy: the backend is loopback, mirroring src/mcp/client.py.
            opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
            with opener.open(req, timeout=30) as r:
                cid = json.loads(r.read().decode("utf-8")).get("comment_id")
        except Exception:
            pass
        check("seeded a comment via HTTP (comment_id present)", bool(cid))

        if cid:
            def call_tool(call_id, tool, arguments):
                o = drive(base + [{"jsonrpc": "2.0", "id": call_id, "method": "tools/call",
                                   "params": {"name": tool, "arguments": arguments}}])
                r = o[-1].get("result", {})
                try:
                    return json.loads(r["content"][0]["text"]), r.get("isError")
                except Exception:
                    return None, r.get("isError")

            lst, err = call_tool(20, "list_comments", {"document_id": rid})  # default open
            check("list_comments(open) -> the seeded comment is listed, isError false",
                  not err and isinstance(lst, dict)
                  and any(c.get("comment_id") == cid for c in lst.get("comments", [])))
            one, err = call_tool(21, "get_comment", {"document_id": rid, "comment_id": cid})
            check("get_comment -> full thread (>=1 entry) + status_history",
                  not err and isinstance(one, dict) and len(one.get("thread", [])) >= 1
                  and len(one.get("status_history", [])) >= 1)
            rep, err = call_tool(22, "reply_to_comment",
                                 {"document_id": rid, "comment_id": cid, "text": "looking into it"})
            check("reply_to_comment -> thread grows, status still open",
                  not err and isinstance(rep, dict) and len(rep.get("thread", [])) == 2
                  and rep.get("status") == "open")
            res, err = call_tool(23, "resolve_comment", {"document_id": rid, "comment_id": cid})
            check("resolve_comment -> status resolved, resolved_by agent",
                  not err and isinstance(res, dict)
                  and res.get("status") == "resolved" and res.get("resolved_by") == "agent")
            op, _ = call_tool(24, "list_comments", {"document_id": rid, "status": "open"})
            rv, _ = call_tool(25, "list_comments", {"document_id": rid, "status": "resolved"})
            check("after resolve: excluded from open, included in resolved",
                  isinstance(op, dict) and isinstance(rv, dict)
                  and not any(c.get("comment_id") == cid for c in op.get("comments", []))
                  and any(c.get("comment_id") == cid for c in rv.get("comments", [])))

        # clean up the smoke review
        drive(base + [{"jsonrpc": "2.0", "id": 5, "method": "tools/call",
                       "params": {"name": "delete_review", "arguments": {"id": rid}}}])

    # 3. tool error path: bad id -> isError:true (not a protocol error)
    out = drive(base + [{"jsonrpc": "2.0", "id": 6, "method": "tools/call",
                         "params": {"name": "get_review", "arguments": {"id": "nope9999"}}}])
    res = out[-1].get("result", {})
    check("bad id -> isError:true result", res.get("isError") is True)

    # 4. protocol error path: unknown tool -> JSON-RPC -32602
    out = drive(base + [{"jsonrpc": "2.0", "id": 7, "method": "tools/call",
                         "params": {"name": "frobnicate", "arguments": {}}}])
    check("unknown tool -> JSON-RPC error -32602", out[-1].get("error", {}).get("code") == -32602)

    print()
    if _fails:
        print("FAILED: %d assertion(s): %s" % (len(_fails), "; ".join(_fails)))
        sys.exit(1)
    print("PASS: all MCP smoke assertions hold")


if __name__ == "__main__":
    main()
