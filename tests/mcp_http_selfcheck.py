#!/usr/bin/env python3
"""mcp_http_selfcheck.py — the remote MCP transport, POST /mcp (#395).

WHAT THIS GUARDS. /mcp is how a claude.ai custom connector reaches mdreview. It must follow the
Streamable HTTP rules a client depends on, and on the hosted tier it must be exactly as strong as
the caller's Bearer: no Bearer means 401 with the metadata pointer an OAuth client starts from, a
user never sees another user's review through it, and a revoked token stops working. It must also
never read a file off the server's disk (attach_asset's `path`).

Boots two throwaway servers: the local tier (python -m mdreview, auth off) and the hosted tier
(python -m mdreview.hosted, stub email, magic-link login read from the server log).

Mutation checks: drop `or not bearer` in McpModule.handle and the browser-session-alone case
fails; drop the local_files=False argument and the attach_asset case fails.

Run: python3 tests/mcp_http_selfcheck.py     (exit 0 = pass)
"""
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(ROOT, ".scratch", "mcp_http_selfcheck_data")
PUBLIC = "https://l.test"
fails = []


def check(name, cond, detail=""):
    print(("  ok   " if cond else "  FAIL ") + name + ("  [%s]" % detail if not cond and detail != "" else ""))
    if not cond:
        fails.append(name)


def free():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


def req(url, method="GET", body=None, headers=None):
    """(status, headers, body_bytes); never raises on an HTTP status."""
    data = body if isinstance(body, (bytes, type(None))) else json.dumps(body).encode()
    r = urllib.request.Request(url, data=data, headers=headers or {}, method=method)
    op = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with op.open(r, timeout=20) as x:
            return x.status, x.headers, x.read()
    except urllib.error.HTTPError as e:
        return e.code, e.headers, e.read()


def boot(module, extra, name):
    data = os.path.join(DATA, name)
    os.makedirs(data, exist_ok=True)
    port = free()
    env = dict(os.environ, MDREVIEW_DATA=data, PORT=str(port), PYTHONPATH=os.path.join(ROOT, "src"),
               MDREVIEW_WEB_DIR=os.path.join(ROOT, "web", "app"), **extra)
    log_path = os.path.join(data, "server.log")
    proc = subprocess.Popen([sys.executable, "-m", module], env=env,
                            stdout=open(log_path, "w"), stderr=subprocess.STDOUT)
    base = "http://127.0.0.1:%d" % port
    for _ in range(80):
        try:
            if req(base + "/healthz")[0] == 200:
                break
        except OSError:
            pass
        time.sleep(0.25)
    return proc, base, log_path


def rpc(base, msg, token=None, headers=None):
    h = {"Content-Type": "application/json", "Accept": "application/json, text/event-stream"}
    if token:
        h["Authorization"] = "Bearer " + token
    h.update(headers or {})
    code, hdrs, raw = req(base + "/mcp", "POST", msg, h)
    try:
        return code, hdrs, json.loads(raw) if raw else None
    except ValueError:
        return code, hdrs, raw


def call(base, name, args, token=None):
    code, _, resp = rpc(base, {"jsonrpc": "2.0", "id": 7, "method": "tools/call",
                               "params": {"name": name, "arguments": args}}, token)
    result = (resp or {}).get("result") or {}
    text = (result.get("content") or [{}])[0].get("text", "")
    return code, result.get("isError"), text


def local_tier():
    proc, base, _ = boot("mdreview", {}, "local")
    try:
        code, _, resp = rpc(base, {"jsonrpc": "2.0", "id": 1, "method": "initialize",
                                   "params": {"protocolVersion": "2025-11-25", "capabilities": {},
                                              "clientInfo": {"name": "t", "version": "0"}}})
        check("local: initialize negotiates 2025-11-25",
              code == 200 and resp["result"]["protocolVersion"] == "2025-11-25", code)
        code, _, resp = rpc(base, {"jsonrpc": "2.0", "method": "notifications/initialized"})
        check("local: a notification gets 202 and no body", code == 202 and resp is None, code)
        code, hdrs, _ = req(base + "/mcp")
        check("local: GET /mcp -> 405 Allow: POST", code == 405 and hdrs.get("Allow") == "POST", code)
        code, _, _ = rpc(base, {"jsonrpc": "2.0", "id": 1, "method": "ping"},
                         headers={"MCP-Protocol-Version": "1999-01-01"})
        check("local: unsupported MCP-Protocol-Version -> 400", code == 400, code)
        code, _, _ = rpc(base, {"jsonrpc": "2.0", "id": 1, "method": "ping"},
                         headers={"Origin": "https://evil.example"})
        check("local: a foreign Origin -> 403", code == 403, code)
        code, _, _ = rpc(base, {"jsonrpc": "2.0", "id": 1, "method": "ping"},
                         headers={"Origin": "https://claude.ai"})
        check("local: Origin https://claude.ai is accepted", code == 200, code)
        code, _, _ = rpc(base, [{"jsonrpc": "2.0", "id": 1, "method": "ping"}])
        check("local: a JSON-RPC batch -> 400", code == 400, code)

        code, err, text = call(base, "create_review", {"markdown": "# local"})
        url = json.loads(text).get("review_url", "") if not err else ""
        check("local: create_review works with no auth, review_url on the caller's host",
              code == 200 and url.startswith(base + "/review/"), url or text[:120])
        rid = json.loads(text).get("id", "") if not err else ""
        code, err, text = call(base, "attach_asset", {"id": rid, "name": "x.txt", "path": "/etc/hosts"})
        check("local: attach_asset(path) is refused, the server's disk is never read",
              err is True and "content_b64" in text, text[:120])
    finally:
        proc.terminate()


def login(base, log_path, email):
    req(base + "/auth/magic-link", "POST", {"email": email}, {"Content-Type": "application/json"})
    tok = ""
    for _ in range(40):
        hits = re.findall(r"auth/redeem\?token=([A-Za-z0-9._~-]+)", open(log_path).read())
        if hits:
            tok = hits[-1]
            break
        time.sleep(0.25)

    class NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, *a, **k):
            return None
    op = urllib.request.build_opener(NoRedirect, urllib.request.ProxyHandler({}))
    r = urllib.request.Request(base + "/auth/redeem", data=("token=" + tok).encode(), method="POST",
                               headers={"Content-Type": "application/x-www-form-urlencoded"})
    try:
        rs = op.open(r, timeout=15)
    except urllib.error.HTTPError as e:
        rs = e
    cookie = rs.headers.get("Set-Cookie", "").split(";")[0]
    csrf = json.loads(req(base + "/auth/session", headers={"Cookie": cookie})[2]).get("csrf", "")
    return cookie, csrf


def mint(base, cookie, csrf):
    code, _, raw = req(base + "/account/tokens", "POST", {"label": "mcp"},
                       {"Content-Type": "application/json", "Cookie": cookie, "X-CSRF-Token": csrf})
    return json.loads(raw).get("token", "") if code == 201 else ""


def hosted_tier():
    proc, base, log_path = boot("mdreview.hosted", {
        "MDREVIEW_REQUIRE_AUTH": "1", "MDREVIEW_ALLOW_PROXY_PLANE": "0",
        "MDREVIEW_PROXY_SECRET": "inert", "MDREVIEW_SESSION_SECRET": "s",
        "MDREVIEW_TOKEN_PEPPER": "p", "MDREVIEW_OWNER_EMAIL": "a@e.com",
        "MDREVIEW_ALLOW_STUB_EMAIL": "1", "MDREVIEW_PUBLIC_BASE": PUBLIC}, "hosted")
    try:
        code, hdrs, _ = rpc(base, {"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
        check("hosted: no Bearer -> 401 pointing at the protected-resource metadata",
              code == 401 and hdrs.get("WWW-Authenticate") ==
              'Bearer resource_metadata="%s/.well-known/oauth-protected-resource/mcp"' % PUBLIC,
              "%s %s" % (code, hdrs.get("WWW-Authenticate")))

        cookie_a, csrf_a = login(base, log_path, "a@e.com")
        code, _, _ = rpc(base, {"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
                         headers={"Cookie": cookie_a})
        check("hosted: a browser session alone is not enough (Bearer required)", code == 401, code)
        code, _, _ = rpc(base, {"jsonrpc": "2.0", "id": 1, "method": "tools/list"}, "mdr_bogus",
                         headers={"Cookie": cookie_a})
        check("hosted: a session cookie plus a bogus Bearer -> 401, not a 200 of failing tools",
              code == 401, code)

        tok_a = mint(base, cookie_a, csrf_a)
        cookie_b, csrf_b = login(base, log_path, "b@e.com")
        tok_b = mint(base, cookie_b, csrf_b)
        check("setup: two users with tokens", tok_a.startswith("mdr_") and tok_b.startswith("mdr_"))

        code, _, resp = rpc(base, {"jsonrpc": "2.0", "id": 1, "method": "tools/list"}, tok_a)
        tools = (resp or {}).get("result", {}).get("tools", [])
        check("hosted: tools/list with a Bearer returns the annotated tools",
              len(tools) == 21 and all("annotations" in t for t in tools), len(tools))

        code, err, text = call(base, "create_review", {"markdown": "# A's draft"}, tok_a)
        rid = json.loads(text).get("id", "") if not err else ""
        check("hosted: A creates a review through /mcp, review_url on the public base",
              bool(rid) and json.loads(text)["review_url"].startswith(PUBLIC + "/review/"), text[:120])

        code, err, text = call(base, "list_reviews", {}, tok_b)
        check("hosted: B's list_reviews does not include A's review", err is False and rid not in text)
        code, err, text = call(base, "get_source", {"id": rid}, tok_b)
        check("hosted: B cannot read A's review through /mcp", err is True, text[:80])
        code, err, text = call(base, "get_source", {"id": rid}, tok_a)
        check("hosted: A can read it", err is False and text == "# A's draft", text[:80])

        tid = json.loads(req(base + "/account/tokens", headers={"Cookie": cookie_a})[2])["tokens"][0]["tok_id"]
        req(base + "/account/tokens/" + tid, "DELETE", headers={"Cookie": cookie_a, "X-CSRF-Token": csrf_a})
        code, _, _ = rpc(base, {"jsonrpc": "2.0", "id": 1, "method": "tools/list"}, tok_a)
        check("hosted: a revoked token gets 401 on its next call", code == 401, code)
    finally:
        proc.terminate()


def main():
    shutil.rmtree(DATA, ignore_errors=True)
    local_tier()
    hosted_tier()
    shutil.rmtree(DATA, ignore_errors=True)
    print("PASS" if not fails else "FAILED: %d check(s)" % len(fails))
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
