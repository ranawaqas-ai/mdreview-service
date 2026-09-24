#!/usr/bin/env python3
"""oauth_selfcheck.py — the OAuth server a remote MCP client signs in through (#395).

WHAT THIS GUARDS. A claude.ai connector discovers /mcp's authorization server, registers, sends
the user through /oauth/authorize, and trades the code for tokens. A mistake here hands a stranger
a token that acts as the user, so this walks the whole flow the way Claude does it and then
attacks each step: a foreign redirect URI, a wrong PKCE verifier, code reuse, a forged consent
POST, a wrong resource, a spent refresh token, and a token revoked on /account.

Boots a throwaway hosted instance (stub email; the magic link is read from the server log).

Mutation checks: prune expired rows in UserService.mint_token with no grace and the idle-connector
case fails; make pkce_ok return True and the wrong-verifier case fails; make redirect_allowed
return True and the foreign-redirect case fails; drop the _same_origin check in _consent and the
cross-site consent case fails.

Run: python3 tests/oauth_selfcheck.py     (exit 0 = pass)
"""
import base64
import hashlib
import json
import os
import re
import secrets
import shutil
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from urllib.parse import parse_qs, quote, urlencode, urlparse

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(ROOT, ".scratch", "oauth_selfcheck_data")
PUBLIC = "https://l.test"
CALLBACK = "https://claude.ai/api/mcp/auth_callback"
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


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *a, **k):
        return None


_OPENER = urllib.request.build_opener(_NoRedirect, urllib.request.ProxyHandler({}))


def req(url, method="GET", body=None, headers=None):
    """(status, headers, body_bytes); redirects are returned, not followed."""
    r = urllib.request.Request(url, data=body, headers=headers or {}, method=method)
    try:
        with _OPENER.open(r, timeout=20) as x:
            return x.status, x.headers, x.read()
    except urllib.error.HTTPError as e:
        return e.code, e.headers, e.read()


def form(url, fields, headers=None):
    return req(url, "POST", urlencode(fields).encode(),
               dict({"Content-Type": "application/x-www-form-urlencoded"}, **(headers or {})))


def cookies_of(hdrs):
    return {c.split("=", 1)[0]: c.split("=", 1)[1].split(";")[0] for c in hdrs.get_all("Set-Cookie") or []}


def pkce():
    verifier = secrets.token_urlsafe(48)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    return verifier, challenge


class Instance:
    def __init__(self):
        port = free()
        self.base = "http://127.0.0.1:%d" % port
        self.log = os.path.join(DATA, "server.log")
        env = dict(os.environ, MDREVIEW_DATA=DATA, PORT=str(port), PYTHONPATH=os.path.join(ROOT, "src"),
                   MDREVIEW_WEB_DIR=os.path.join(ROOT, "web", "app"), MDREVIEW_REQUIRE_AUTH="1",
                   MDREVIEW_ALLOW_PROXY_PLANE="0", MDREVIEW_PROXY_SECRET="inert",
                   MDREVIEW_SESSION_SECRET="s", MDREVIEW_TOKEN_PEPPER="p",
                   MDREVIEW_OWNER_EMAIL="a@e.com", MDREVIEW_ALLOW_STUB_EMAIL="1",
                   MDREVIEW_PUBLIC_BASE=PUBLIC)
        self.proc = subprocess.Popen([sys.executable, "-m", "mdreview.hosted"], env=env,
                                     stdout=open(self.log, "w"), stderr=subprocess.STDOUT)
        for _ in range(80):
            try:
                if req(self.base + "/healthz")[0] == 200:
                    return
            except OSError:
                pass
            time.sleep(0.25)

    def login(self, email, extra_cookie=""):
        """Magic link -> redeem; returns (session cookie header, redeem Location)."""
        req(self.base + "/auth/magic-link", "POST", json.dumps({"email": email}).encode(),
            {"Content-Type": "application/json"})
        seen = len(re.findall(r"auth/redeem\?token=", open(self.log).read()))
        tok = ""
        for _ in range(40):
            hits = re.findall(r"auth/redeem\?token=([A-Za-z0-9._~-]+)", open(self.log).read())
            if hits and len(hits) >= seen:
                tok = hits[-1]
                break
            time.sleep(0.25)
        code, hdrs, _ = form(self.base + "/auth/redeem", {"token": tok},
                             {"Cookie": extra_cookie} if extra_cookie else None)
        return "mdr_session=" + cookies_of(hdrs).get("mdr_session", ""), hdrs.get("Location")

    def mcp(self, token, method="tools/list", params=None):
        body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": method, "params": params or {}}).encode()
        code, _, raw = req(self.base + "/mcp", "POST", body,
                           {"Content-Type": "application/json", "Authorization": "Bearer " + token})
        return code, (json.loads(raw) if code == 200 else None)


def authorize_url(inst, client_id, challenge, redirect=CALLBACK, resource=PUBLIC + "/mcp", state="st8"):
    return inst.base + "/oauth/authorize?" + urlencode({
        "response_type": "code", "client_id": client_id, "redirect_uri": redirect, "state": state,
        "code_challenge": challenge, "code_challenge_method": "S256", "resource": resource})


def consent(inst, session, client_id, challenge, **kw):
    """GET the consent page as `session`; returns (nonce, status, headers)."""
    code, hdrs, raw = req(authorize_url(inst, client_id, challenge, **kw), headers={"Cookie": session})
    m = re.search(rb"name=nonce value='([^']+)'", raw)
    return (m.group(1).decode() if m else ""), code, hdrs


def approve(inst, session, nonce, decision="approve", origin=PUBLIC):
    """The browser's form post: same-origin unless `origin` says otherwise."""
    return form(inst.base + "/oauth/authorize", {"nonce": nonce, "decision": decision},
                {"Cookie": session, "Origin": origin})


def expired_row_survives_other_mints():
    """An idle connector's access token expires after 1h. Another user's mint must not prune its
    row, because the refresh grant reads a missing row as "revoked on /account"."""
    sys.path.insert(0, os.path.join(ROOT, "src"))
    from mdreview.store import Store
    from mdreview.users import UserService
    d = os.path.join(DATA, "unit")
    os.makedirs(d, exist_ok=True)
    users = UserService(Store(d), "pepper")
    with users.store.lock:
        idle = users.mint_token("u:1", "OAuth: Claude", ttl_s=-1)      # already expired
        users.mint_token("u:2", "someone else")                        # triggers the prune
        survived = users.revoke_token("u:1", users.token_id(idle))
    check("an expired access row survives other mints, so an idle connector can still refresh",
          survived)
    check("...while the expired token itself no longer authenticates",
          users.resolve("Bearer " + idle) is None)


def main():
    shutil.rmtree(DATA, ignore_errors=True)
    os.makedirs(DATA)
    expired_row_survives_other_mints()
    inst = Instance()
    try:
        run(inst)
    finally:
        inst.proc.terminate()
        shutil.rmtree(DATA, ignore_errors=True)
    print("PASS" if not fails else "FAILED: %d check(s)" % len(fails))
    return 1 if fails else 0


def run(inst):
    b = inst.base
    # ---- discovery ----
    _, _, raw = req(b + "/.well-known/oauth-protected-resource/mcp")
    prm = json.loads(raw)
    check("PRM: resource is the public /mcp URL, AS is the public base",
          prm.get("resource") == PUBLIC + "/mcp" and prm.get("authorization_servers") == [PUBLIC], prm)
    _, _, raw = req(b + "/.well-known/oauth-authorization-server")
    asm = json.loads(raw)
    check("AS metadata: issuer, S256, public clients, registration endpoint",
          asm.get("issuer") == PUBLIC and asm.get("code_challenge_methods_supported") == ["S256"]
          and asm.get("token_endpoint_auth_methods_supported") == ["none"]
          and asm.get("registration_endpoint") == PUBLIC + "/oauth/register", asm)
    check("AS metadata does not advertise CIMD (the server never fetches client URLs)",
          "client_id_metadata_document_supported" not in asm)

    # ---- registration ----
    def register(uris, name="Claude"):
        code, _, raw = req(b + "/oauth/register", "POST",
                           json.dumps({"redirect_uris": uris, "client_name": name}).encode(),
                           {"Content-Type": "application/json"})
        return code, json.loads(raw)
    code, reg = register(["https://evil.example/cb"])
    check("register: a foreign redirect URI is refused", code == 400 and reg.get("error") == "invalid_redirect_uri", code)
    code, reg = register(["http://127.0.0.1:5555/callback"], "Claude Code")
    check("register: a loopback redirect URI is accepted", code == 201, code)
    code, reg = register([CALLBACK])
    client_id = reg.get("client_id", "")
    check("register: the claude.ai callback -> 201 with a public client_id",
          code == 201 and client_id and reg.get("token_endpoint_auth_method") == "none", reg)

    # ---- authorize: bad requests never redirect ----
    verifier, challenge = pkce()
    code, hdrs, _ = req(authorize_url(inst, "mdrc_nope", challenge))
    check("authorize: unknown client -> 400 page, no redirect", code == 400 and not hdrs.get("Location"), code)
    code, hdrs, _ = req(authorize_url(inst, client_id, challenge, redirect="https://claude.ai/other"))
    check("authorize: unregistered redirect -> 400 page, no redirect", code == 400 and not hdrs.get("Location"), code)
    code, hdrs, _ = req(authorize_url(inst, client_id, challenge, resource="https://elsewhere.test/mcp"))
    check("authorize: a foreign resource -> redirect with invalid_target",
          code == 302 and "error=invalid_target" in hdrs.get("Location", ""), code)

    # ---- authorize: anonymous -> sign in -> back ----
    url = authorize_url(inst, client_id, challenge)
    code, hdrs, _ = req(url)
    ret = cookies_of(hdrs).get("mdr_return", "")
    check("authorize while signed out -> sign-in, remembering the request",
          code == 302 and hdrs.get("Location") == "/" and ret, code)
    session_a, landed = inst.login("a@e.com", extra_cookie="mdr_return=" + ret)
    check("sign-in returns to the authorize request", landed == url[len(b):], landed)
    _, landed_evil = inst.login("a@e.com", extra_cookie="mdr_return=" + quote("https://evil.example/x", safe=""))
    check("sign-in ignores a return cookie that is not /oauth/authorize (no open redirect)", landed_evil == "/", landed_evil)

    # ---- consent ----
    nonce, code, hdrs = consent(inst, session_a, client_id, challenge)
    check("consent page for a signed-in user, framing refused",
          code == 200 and nonce
          and "frame-ancestors 'none'" in (hdrs.get("Content-Security-Policy") or ""), code)
    code, _, _ = approve(inst, session_a, nonce, origin="https://evil.example")
    check("consent POST from another site's page (Origin) -> 403", code == 403, code)
    session_b, _ = inst.login("b@e.com")
    code, _, _ = approve(inst, session_b, nonce)
    check("consent POST from a different user -> 403", code == 403, code)
    # two consent pages open (a retried popup, an old tab): Allow on the OLDER one must still work
    older, _, _ = consent(inst, session_a, client_id, challenge)
    consent(inst, session_a, client_id, challenge)
    code, hdrs, _ = approve(inst, session_a, older)
    loc = urlparse(hdrs.get("Location", ""))
    q = {k: v[0] for k, v in parse_qs(loc.query).items()}
    check("with two consent pages open, Allow on the older one -> 302 with code and state",
          code == 302 and hdrs.get("Location", "").startswith(CALLBACK) and q.get("code") and q.get("state") == "st8",
          hdrs.get("Location"))
    code_ = q.get("code", "")

    # ---- token ----
    tok_url = b + "/oauth/token"
    base_fields = {"grant_type": "authorization_code", "code": code_, "redirect_uri": CALLBACK,
                   "client_id": client_id, "resource": PUBLIC + "/mcp"}
    code, _, raw = form(tok_url, dict(base_fields, code_verifier=pkce()[0]))
    check("token: a wrong PKCE verifier -> invalid_grant", code == 400 and b"invalid_grant" in raw, code)
    code, _, raw = form(tok_url, dict(base_fields, code_verifier=verifier))
    check("token: the code was burned by the failed attempt (single use)", code == 400, code)

    nonce, _, _ = consent(inst, session_a, client_id, challenge)
    _, hdrs, _ = approve(inst, session_a, nonce)
    code_ = parse_qs(urlparse(hdrs["Location"]).query)["code"][0]
    base_fields["code"] = code_
    code, hdrs, raw = form(tok_url, dict(base_fields, code_verifier=verifier))
    grant = json.loads(raw) if code == 200 else {}
    check("token: code + verifier -> Bearer access (mdr_, 1h) + refresh, no-store",
          grant.get("access_token", "").startswith("mdr_") and grant.get("expires_in") == 3600
          and grant.get("refresh_token") and "no-store" in (hdrs.get("Cache-Control") or ""), raw[:120])
    code, _, _ = form(tok_url, dict(base_fields, code_verifier=verifier))
    check("token: replaying the same code -> 400", code == 400, code)

    access, refresh = grant.get("access_token", ""), grant.get("refresh_token", "")
    code, resp = inst.mcp(access)
    check("the OAuth access token works on /mcp", code == 200 and len(resp["result"]["tools"]) == 21, code)
    code, resp = inst.mcp(access, "tools/call", {"name": "create_review", "arguments": {"markdown": "# via oauth"}})
    rid = json.loads(resp["result"]["content"][0]["text"]).get("id") if code == 200 else ""
    check("create_review through the OAuth token", bool(rid), code)

    # isolation: B's own OAuth-free token cannot see A's review
    csrf_b = json.loads(req(b + "/auth/session", headers={"Cookie": session_b})[2])["csrf"]
    _, _, raw = req(b + "/account/tokens", "POST", json.dumps({"label": "b"}).encode(),
                    {"Content-Type": "application/json", "Cookie": session_b, "X-CSRF-Token": csrf_b})
    tok_b = json.loads(raw)["token"]
    _, resp = inst.mcp(tok_b, "tools/call", {"name": "get_source", "arguments": {"id": rid}})
    check("another user cannot read the review the connector created", resp["result"]["isError"] is True)

    # ---- refresh rotation ----
    code, _, raw = form(tok_url, {"grant_type": "refresh_token", "refresh_token": refresh, "client_id": client_id})
    new = json.loads(raw) if code == 200 else {}
    check("refresh -> a new access + refresh pair", new.get("access_token", "").startswith("mdr_")
          and new.get("refresh_token") not in ("", None, refresh), raw[:120])
    check("refresh revoked the previous access token", inst.mcp(access)[0] == 401)
    code, _, _ = form(tok_url, {"grant_type": "refresh_token", "refresh_token": refresh, "client_id": client_id})
    check("a spent refresh token -> 400", code == 400, code)

    # ---- revoke on /account ends the grant ----
    csrf_a = json.loads(req(b + "/auth/session", headers={"Cookie": session_a})[2])["csrf"]
    toks = json.loads(req(b + "/account/tokens", headers={"Cookie": session_a})[2])["tokens"]
    oauth_rows = [t for t in toks if t["label"].startswith("OAuth:")]
    check("/account lists exactly one live OAuth token for the grant", len(oauth_rows) == 1, toks)
    req(b + "/account/tokens/" + oauth_rows[0]["tok_id"], "DELETE",
        headers={"Cookie": session_a, "X-CSRF-Token": csrf_a})
    check("after revoking on /account, /mcp says 401", inst.mcp(new["access_token"])[0] == 401)
    code, _, _ = form(tok_url, {"grant_type": "refresh_token", "refresh_token": new["refresh_token"],
                                "client_id": client_id})
    check("...and the refresh token is dead too", code == 400, code)

    # ---- deny ----
    nonce, _, _ = consent(inst, session_a, client_id, challenge)
    code, hdrs, _ = approve(inst, session_a, nonce, decision="deny")
    check("deny -> 302 with error=access_denied", code == 302 and "error=access_denied" in hdrs.get("Location", ""), code)


if __name__ == "__main__":
    sys.exit(main())
