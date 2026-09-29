#!/usr/bin/env python3
"""demo_login_selfcheck.py: the reviewer sign-in at /auth/demo (docs/operations/demo-login.md).

WHAT THIS GUARDS. /auth/demo trades one shared access code for a session, so a mistake here is a
new way into the service. The checks cover: the route does not exist unless the key is set; a short
key refuses to boot; every wrong or malformed code gets one identical 403; the code is honoured
from the POST body only (never the URL); the per-IP lockout holds even for the right code while
another IP is unaffected; the session is an ordinary non-admin one; the return cookie can only send
the browser to /oauth/authorize; the code never leaks into any response, header, log line or audit
row; and scripts/seed_demo.py is idempotent, wipes cleanly and revokes its own token.

Boots throwaway hosted instances under .scratch/ (stub email, free ports, killed by PID).

Mutation checks (each was applied by hand and run; the check named after it must FAIL):
  * replace the digest compare with `==`          -> "the code is compared with hmac.compare_digest over SHA-256 digests"
  * remove the throttle (_locked_out -> False)    -> "the 6th attempt from one IP is refused even with the right code"
  * make the demo uid admin (set_admin after resolve) -> "/auth/session reports is_admin false"
  * accept the code from the query string          -> "?key=<right code> in the URL does not sign in"

Run: python3 tests/demo_login_selfcheck.py     (exit 0 = pass)
"""
import hashlib
import io
import json
import os
import re
import shutil
import socket
import sqlite3
import subprocess
import sys
import time
import urllib.error
import urllib.request
from urllib.parse import urlencode

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRATCH = os.path.join(ROOT, ".scratch", "demo_login_selfcheck")
PUBLIC = "https://l.test"
KEY = "K3y-" + "abcdefghij" * 4          # 44 chars, well over the 32 minimum
DEMO_UID = "email:mdreview-demo@mdreview.space"
fails = []
SEEN = []                                # every response byte the checks saw (for the leak sweep)


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
    r = urllib.request.Request(url, data=body, headers=headers or {}, method=method)
    try:
        with _OPENER.open(r, timeout=20) as x:
            out = (x.status, x.headers, x.read())
    except urllib.error.HTTPError as e:
        out = (e.code, e.headers, e.read())
    SEEN.append(str(out[1]).encode() + out[2])
    return out


def post_code(inst, code, ip, extra=None, path="/auth/demo", raw=None):
    body = raw if raw is not None else urlencode({"code": code}).encode()
    hdrs = {"Content-Type": "application/x-www-form-urlencoded", "X-Real-IP": ip}
    hdrs.update(extra or {})
    return req(inst.base + path, "POST", body, hdrs)


def session_cookie(hdrs):
    for c in hdrs.get_all("Set-Cookie") or []:
        if c.startswith("mdr_session=") and len(c.split(";")[0]) > len("mdr_session="):
            return c.split(";")[0]
    return ""


class Instance:
    def __init__(self, name, key=None):
        self.data = os.path.join(SCRATCH, name)
        shutil.rmtree(self.data, ignore_errors=True)
        os.makedirs(self.data)
        port = free()
        self.base = "http://127.0.0.1:%d" % port
        self.log = os.path.join(self.data, "server.log")
        env = dict(os.environ, MDREVIEW_DATA=self.data, PORT=str(port),
                   PYTHONPATH=os.path.join(ROOT, "src"),
                   MDREVIEW_WEB_DIR=os.path.join(ROOT, "web", "app"), MDREVIEW_REQUIRE_AUTH="1",
                   MDREVIEW_ALLOW_PROXY_PLANE="0", MDREVIEW_PROXY_SECRET="inert",
                   MDREVIEW_SESSION_SECRET="s", MDREVIEW_TOKEN_PEPPER="p",
                   MDREVIEW_OWNER_EMAIL="a@e.com", MDREVIEW_ALLOW_STUB_EMAIL="1",
                   MDREVIEW_PUBLIC_BASE=PUBLIC)
        env.pop("MDREVIEW_DEMO_LOGIN_KEY", None)
        if key is not None:
            env["MDREVIEW_DEMO_LOGIN_KEY"] = key
        self.logf = open(self.log, "w")
        self.proc = subprocess.Popen([sys.executable, "-m", "mdreview.hosted"], env=env,
                                     stdout=self.logf, stderr=subprocess.STDOUT)
        self.up = False
        for _ in range(80):
            if self.proc.poll() is not None:
                break
            try:
                if req(self.base + "/healthz")[0] == 200:
                    self.up = True
                    return
            except OSError:
                pass
            time.sleep(0.25)

    def stop(self):
        if self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.proc.kill()
        self.logf.close()

    def audit_rows(self):
        conn = sqlite3.connect(os.path.join(self.data, "identity.db"))
        try:
            return conn.execute("SELECT event, uid, email, ip, detail FROM auth_audit").fetchall()
        finally:
            conn.close()

    def log_text(self):
        return open(self.log, errors="replace").read()


def route_absent_without_key():
    inst = Instance("nokey")
    try:
        check("no key: the instance boots", inst.up)
        check("no key: GET /auth/demo is 404", req(inst.base + "/auth/demo")[0] == 404)
        check("no key: POST /auth/demo is 404", post_code(inst, KEY, "10.9.0.1")[0] == 404)
    finally:
        inst.stop()
    empty = Instance("emptykey", key="")
    try:
        check("empty key: the instance boots and the route stays off",
              empty.up and req(empty.base + "/auth/demo")[0] == 404)
    finally:
        empty.stop()


def short_key_refuses_boot():
    short = "s" * 31
    inst = Instance("shortkey", key=short)
    try:
        for _ in range(40):
            if inst.proc.poll() is not None:
                break
            time.sleep(0.25)
        out = inst.log_text()
        check("a 31-character key refuses to boot", inst.proc.poll() not in (None, 0), str(inst.proc.poll()))
        check("...with a message naming the variable", "MDREVIEW_DEMO_LOGIN_KEY" in out)
        check("...that does not print the key", short not in out)
    finally:
        inst.stop()
    edge = Instance("edgekey", key="e" * 32)
    try:
        check("a 32-character key boots and serves the form",
              edge.up and req(edge.base + "/auth/demo")[0] == 200)
    finally:
        edge.stop()


def compare_is_digest_based():
    """Behavioural spy: drive one login attempt in process and record what reaches compare_digest."""
    sys.path.insert(0, os.path.join(ROOT, "src"))
    from mdreview.hosted import demologin

    calls = []
    real = demologin.hmac.compare_digest

    def spy(a, b):
        calls.append((a, b))
        return real(a, b)

    class Sink:
        def __init__(self):
            self.status, self.headers_out = None, []

        def send_response(self, c): self.status = c
        def send_header(self, k, v): self.headers_out.append((k, v))
        def end_headers(self): pass

    class Fake(Sink):
        def __init__(self, code):
            super().__init__()
            body = urlencode({"code": code}).encode()
            self.headers = {"Content-Length": str(len(body))}
            self.rfile, self.wfile = io.BytesIO(body), io.BytesIO()
            self.client_address = ("127.0.0.1", 1)

    class Lock:
        def __enter__(self): return self
        def __exit__(self, *a): return False

    class Store: lock = Lock()
    class Accounts:
        def resolve_verified_email(self, e): return "email:x", False
    class Sessions:
        def mint(self, *a, **k): return "c", "t"
        def set_cookie_header(self, v): return "mdr_session=" + v
    class Ids:
        def audit(self, *a, **k): pass

    mod = demologin.DemoLoginModule(Store(), Accounts(), Sessions(), Ids(), KEY)
    demologin.hmac.compare_digest = spy
    try:
        wrong, right = Fake("nope"), Fake(KEY)
        mod._login(wrong)
        mod._login(right)
    finally:
        demologin.hmac.compare_digest = real
    ok = (len(calls) == 2
          and all(isinstance(a, bytes) and isinstance(b, bytes) and len(a) == len(b) == 32
                  for a, b in calls)
          and calls[1][0] == hashlib.sha256(KEY.encode()).digest()
          and all(KEY.encode() not in (a, b) for a, b in calls))
    check("the code is compared with hmac.compare_digest over SHA-256 digests", ok, str(len(calls)))
    check("...and the unit run accepts the right code and refuses the wrong one",
          wrong.status == 403 and right.status == 303, "%s %s" % (wrong.status, right.status))


def main_flow():
    inst = Instance("main", key=KEY)
    try:
        check("keyed instance boots", inst.up)
        if not inst.up:
            return inst
        page = req(inst.base + "/auth/demo")
        check("GET /auth/demo serves the form (200, HTML, password field named code)",
              page[0] == 200 and b"name='code'" in page[2] and b"type='password'" in page[2])

        # wrong vs malformed codes: one identical page
        wrong = post_code(inst, "x" * 40, "10.1.0.1")
        malformed = [
            post_code(inst, "", "10.1.0.2", raw=b""),
            post_code(inst, "", "10.1.0.3", raw=b"other=1"),
            post_code(inst, "", "10.1.0.4", raw=b"code=%ff%fe%00"),
            post_code(inst, "", "10.1.0.5", raw=b"code=" + b"A" * 9000),
            post_code(inst, KEY[:-1], "10.1.0.6"),
            post_code(inst, KEY + "x", "10.1.0.7"),
        ]
        check("a wrong code gets 403 and no session cookie",
              wrong[0] == 403 and not session_cookie(wrong[1]))
        check("well-formed wrong and malformed codes get byte-identical pages and statuses",
              all(m[0] == 403 and m[2] == wrong[2] for m in malformed),
              str([(m[0], len(m[2])) for m in malformed]))
        rows = inst.audit_rows()
        fail_rows = [r for r in rows if r[0] == "demo_login_fail"]
        check("failures write demo_login_fail audit rows carrying the IP",
              len(fail_rows) == 7 and {"10.1.0.1", "10.1.0.7"} <= {r[3] for r in fail_rows},
              str(len(fail_rows)))

        # query string never signs in
        q1 = req(inst.base + "/auth/demo?key=" + KEY + "&code=" + KEY, "POST", b"",
                 {"X-Real-IP": "10.2.0.1", "Content-Type": "application/x-www-form-urlencoded"})
        q2 = req(inst.base + "/auth/demo?key=" + KEY + "&code=" + KEY, headers={"X-Real-IP": "10.2.0.2"})
        check("?key=<right code> in the URL does not sign in",
              q1[0] == 403 and not session_cookie(q1[1]) and not session_cookie(q2[1])
              and q2[0] == 200)

        # throttle
        for i in range(5):
            r = post_code(inst, "bad%d" % i, "10.3.0.1")
            if r[0] != 403:
                check("first 5 wrong attempts answer 403", False, str(r[0]))
        locked = post_code(inst, KEY, "10.3.0.1")
        check("the 6th attempt from one IP is refused even with the right code",
              locked[0] == 403 and not session_cookie(locked[1]), str(locked[0]))
        other = post_code(inst, KEY, "10.3.0.2")
        check("...while the right code from another IP still works",
              other[0] == 303 and session_cookie(other[1]) != "", str(other[0]))
        loc_rows = [r for r in inst.audit_rows() if r[0] == "demo_login_fail" and r[3] == "10.3.0.1"]
        check("the lockout is audited once and locked attempts do not add rows",
              len(loc_rows) == 5 and sum("lockout" in (r[4] or "") for r in loc_rows) == 1,
              str(len(loc_rows)))

        # success shape
        ok = post_code(inst, KEY, "10.4.0.1", {"User-Agent": "reviewer-ua/1"})
        cookie = session_cookie(ok[1])
        check("right code: 303 to / with a session cookie", ok[0] == 303 and ok[1]["Location"] == "/" and cookie != "")
        sess = json.loads(req(inst.base + "/auth/session", headers={"Cookie": cookie})[2])
        check("the session is for the demo account", sess.get("uid") == DEMO_UID, str(sess.get("uid")))
        check("/auth/session reports is_admin false", sess.get("is_admin") is False, str(sess.get("is_admin")))
        adm = req(inst.base + "/admin/users", headers={"Cookie": cookie, "Accept": "application/json"})
        check("/admin/users is 403 for the demo session", adm[0] == 403, str(adm[0]))
        good = [r for r in inst.audit_rows() if r[0] == "demo_login"]
        check("success writes a demo_login audit row with IP and user agent",
              any(r[1] == DEMO_UID and r[3] == "10.4.0.1" and "reviewer-ua/1" in (r[4] or "") for r in good))
        sessions = json.loads(req(inst.base + "/auth/sessions", headers={"Cookie": cookie})[2])
        check("the session row records IP and user agent",
              any(s["ip"] == "10.4.0.1" and s["user_agent"] == "reviewer-ua/1" for s in sessions["sessions"]))

        # return cookie
        def landing(value, ip):
            r = post_code(inst, KEY, ip, {"Cookie": "mdr_return=" + value})
            return r[0], r[1]["Location"], r[1].get_all("Set-Cookie") or []

        target = "/oauth/authorize?client_id=abc&state=s1"
        code, loc, cookies = landing(target, "10.5.0.1")
        check("mdr_return set to an /oauth/authorize path: redirect goes there",
              code == 303 and loc == target, loc)
        check("...and the mdr_return cookie is cleared",
              any(c.startswith("mdr_return=;") and "Max-Age=0" in c for c in cookies))
        for i, bad in enumerate(["https://evil.example/x", "//evil.example", "/oauth/authorize?a=b\\c",
                                 "/somewhere/else", "%2F%2Fevil.example", "/oauth/authorizeX?a=1", ""]):
            code, loc, _ = landing(bad, "10.5.1.%d" % (i + 1))
            check("mdr_return %r lands on / only" % bad, code == 303 and loc == "/", loc)
    except Exception:
        inst.stop()
        raise
    return inst


def seed_script(inst):
    if not inst.up:
        return
    script = os.path.join(ROOT, "scripts", "seed_demo.py")
    env = dict(os.environ, MDREVIEW_DEMO_LOGIN_KEY=KEY)

    def run(*args, env=env):
        p = subprocess.run([sys.executable, script, "--base", inst.base] + list(args), env=env,
                           capture_output=True, text=True, timeout=120)
        SEEN.append((p.stdout + p.stderr).encode())
        return p

    # A cookie session of our own to look at the account from outside the script.
    r = post_code(inst, KEY, "10.6.0.1")
    cookie = session_cookie(r[1])
    csrf = json.loads(req(inst.base + "/auth/session", headers={"Cookie": cookie})[2])["csrf"]

    def reviews():
        return json.loads(req(inst.base + "/api/reviews", headers={"Cookie": cookie})[2])["reviews"]

    def tokens():
        return json.loads(req(inst.base + "/account/tokens", headers={"Cookie": cookie})[2])["tokens"]

    mine = req(inst.base + "/api/reviews", "POST",
               json.dumps({"title": "Not a sample", "markdown": "# mine"}).encode(),
               {"Cookie": cookie, "X-CSRF-Token": csrf, "Content-Type": "application/json"})
    check("setup: the demo account can hold its own review", mine[0] == 201, str(mine[0]))

    p1 = run()
    n1 = [x for x in reviews() if x["title"].startswith("[Demo] ")]
    check("seed run 1 exits 0 and creates 4 sample reviews", p1.returncode == 0 and len(n1) == 4,
          (p1.stderr or p1.stdout)[-300:] + str(len(n1)))
    p2 = run()
    n2 = [x for x in reviews() if x["title"].startswith("[Demo] ")]
    check("seed run 2 keeps the same count (4), replacing rather than adding",
          p2.returncode == 0 and len(n2) == 4 and {x["id"] for x in n1}.isdisjoint({x["id"] for x in n2}),
          (p2.stderr or p2.stdout)[-300:])
    check("a rerun leaves the account's own non-sample review alone",
          any(x["title"] == "Not a sample" for x in reviews()))

    by_title = {x["title"]: x["id"] for x in n2}
    spec = by_title["[Demo] Notification service spec"]
    src = req(inst.base + "/api/reviews/%s/source" % spec, headers={"Cookie": cookie})[2].decode()
    threads = json.loads(req(inst.base + "/api/reviews/%s/comments" % spec, headers={"Cookie": cookie})[2])["comments"]
    check("the spec has a Mermaid diagram and one resolved thread",
          "```mermaid" in src and [c["status"] for c in threads] == ["resolved"], str(threads and threads[0]["status"]))
    other = json.loads(req(inst.base + "/api/reviews/%s/comments" % by_title["[Demo] Release checklist"],
                           headers={"Cookie": cookie})[2])["comments"]
    check("the checklist has one open comment", [c["status"] for c in other] == ["open"])
    assets = json.loads(req(inst.base + "/api/reviews/%s/assets" % by_title["[Demo] Onboarding illustration"],
                            headers={"Cookie": cookie})[2])["assets"]
    check("the illustration review has an attached PNG",
          len(assets) == 1 and assets[0]["ctype"] == "image/png" and assets[0]["bytes"] > 100)
    check("after the runs the seed token is revoked (no seed-demo tokens listed)",
          not [t for t in tokens() if t["label"].startswith("seed-demo-")], str(tokens()))

    bad = run(env=dict(os.environ, MDREVIEW_DEMO_LOGIN_KEY="wrong-code-" + "z" * 30))
    check("the seed script with a wrong code exits non-zero", bad.returncode != 0)

    w = run("--wipe")
    check("--wipe exits 0 and empties the account", w.returncode == 0 and reviews() == [],
          (w.stderr or w.stdout)[-300:] + str(len(reviews())))
    check("...and the seed token is revoked after the wipe too",
          not [t for t in tokens() if t["label"].startswith("seed-demo-")])
    kf = os.path.join(inst.data, "keyfile")
    with open(kf, "w") as f:
        f.write(KEY + "\n")
    env_less = {k: v for k, v in os.environ.items() if k != "MDREVIEW_DEMO_LOGIN_KEY"}
    kp = run("--key-file", kf, env=env_less)
    check("--key-file works without the environment variable", kp.returncode == 0 and len(reviews()) == 4)


def leak_sweep(inst):
    if not inst.up:
        return
    k = KEY.encode()
    check("the code appears in no response body or header the checks saw", not any(k in b for b in SEEN))
    check("the code appears nowhere in the server log", KEY not in inst.log_text())
    leaked = [r for r in inst.audit_rows() if any(KEY in str(c) for c in r)]
    check("the code appears in no auth_audit row", not leaked, str(leaked[:1]))
    lines = [l for l in inst.log_text().splitlines() if "/auth/demo" in l]
    check("the server log has no request lines for /auth/demo", not lines, str(lines[:1]))


def main():
    shutil.rmtree(SCRATCH, ignore_errors=True)
    os.makedirs(SCRATCH)
    inst = None
    try:
        route_absent_without_key()
        short_key_refuses_boot()
        compare_is_digest_based()
        inst = main_flow()
        seed_script(inst)
        leak_sweep(inst)
    finally:
        if inst:
            inst.stop()
    print()
    if fails:
        print("FAILED %d check(s):" % len(fails))
        for f in fails:
            print("  - " + f)
        return 1
    print("all demo-login checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
