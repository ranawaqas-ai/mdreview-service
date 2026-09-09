#!/usr/bin/env python3
"""git_history_selfcheck.py — the #379 runnable check: materialize a real bare repo for a test
review and `git clone` it over HTTP for real (not a file-path clone — that would prove nothing
about routes.py's git-http-backend CGI proxy), then assert the checkout's content and commit
count match the review's round history.

Boots a throwaway LOCAL (REQUIRE_AUTH off) `python -m mdreview` instance with
MDREVIEW_ENABLE_GIT_HISTORY=1, drives a review through two PUT /source rounds plus one comment,
clones http://127.0.0.1:<port>/git/<id>.git with the real `git` binary, and checks:
  - the clone's source.md matches the review's CURRENT draft, not a stale round
  - commit count == historical rounds (2) + 1 live tip commit == 3
  - the oldest commit's source.md is round-0's archived draft, the middle one is round-1's
  - comments.json exists ONLY in the tip commit's tree, never in a historical round (#379 design:
    "comments.json only in current()'s snapshot")

Then boots a SECOND instance with the flag left off and asserts both the git_url endpoint and the
raw git route 404 — the "off means the composition root registers nothing" contract (mirrors
ENABLE_LATEX's own byte-identical-when-off guarantee).

Run: python3 tests/git_history_selfcheck.py     (exit 0 = pass)
"""
import json
import os
import shutil
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_ON = os.path.join(ROOT, ".scratch", "git_history_on_data")
DATA_OFF = os.path.join(ROOT, ".scratch", "git_history_off_data")
CLONE_DIR = os.path.join(ROOT, ".scratch", "git_history_clone")
FAILED = [0]


def ok(label, cond):
    print(("  ok   " if cond else "  FAIL ") + label)
    if not cond:
        FAILED[0] += 1


def free():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


_opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def req(u, m="GET", d=None, h=None):
    r = urllib.request.Request(u, data=d, headers=h or {}, method=m)
    try:
        with _opener.open(r, timeout=15) as x:
            return x.status, x.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()
    except urllib.error.URLError:
        return 0, b""


def boot(data_dir, extra_env):
    shutil.rmtree(data_dir, ignore_errors=True)
    os.makedirs(data_dir)
    port = free()
    env = dict(os.environ, MDREVIEW_DATA=data_dir, PORT=str(port),
               MDREVIEW_WEB_DIR=os.path.join(ROOT, "web", "app"),
               PYTHONPATH=os.path.join(ROOT, "src"))
    env.update(extra_env)
    log = open(os.path.join(data_dir, "s.log"), "w")
    srv = subprocess.Popen([sys.executable, "-m", "mdreview"], env=env, stdout=log, stderr=log)
    base = "http://127.0.0.1:%d" % port
    for _ in range(80):
        status, _ = req(base + "/healthz")
        if status == 200:
            break
        time.sleep(.25)
    return srv, base


def git(args, cwd=None):
    p = subprocess.run(["git"] + args, cwd=cwd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if p.returncode != 0:
        raise RuntimeError("git %s failed: %s" % (args, p.stderr.decode("utf-8", "replace")))
    return p.stdout.decode("utf-8", "replace")


# ==== instance A: feature ON ====================================================================
srv_on, base_on = boot(DATA_ON, {"MDREVIEW_ENABLE_GIT_HISTORY": "1"})
try:
    status, body = req(base_on + "/api/reviews", "POST",
                        json.dumps({"markdown": "v0 draft"}).encode(),
                        {"Content-Type": "application/json"})
    ok("create review (flag on)", status == 201)
    rid = json.loads(body)["id"]

    status, _ = req(base_on + "/api/reviews/%s/source" % rid, "PUT",
                     json.dumps({"markdown": "v1 draft"}).encode(),
                     {"Content-Type": "application/json"})
    ok("PUT round 0->1", status == 200)

    status, _ = req(base_on + "/api/reviews/%s/source" % rid, "PUT",
                     json.dumps({"markdown": "v2 draft (current)"}).encode(),
                     {"Content-Type": "application/json"})
    ok("PUT round 1->2", status == 200)

    status, _ = req(base_on + "/api/reviews/%s/comments" % rid, "POST",
                     json.dumps({"anchor": {}, "text": "a live comment", "role": "agent"}).encode(),
                     {"Content-Type": "application/json"})
    ok("post a comment (current-only, never per-round)", status == 201)

    status, body = req(base_on + "/api/reviews/%s/git_url" % rid)
    ok("git_url endpoint present when flag is on", status == 200)
    git_url = json.loads(body).get("git_url", "") if status == 200 else ""
    ok("git_url ends with /git/<id>.git", git_url.endswith("/git/%s.git" % rid))

    shutil.rmtree(CLONE_DIR, ignore_errors=True)
    cloned = False
    try:
        git(["clone", "--quiet", git_url, CLONE_DIR])
        cloned = True
    except RuntimeError as e:
        print("    clone error:", e)
    ok("real `git clone` over HTTP succeeds", cloned)

    if cloned:
        with open(os.path.join(CLONE_DIR, "source.md"), encoding="utf-8") as f:
            got_source = f.read()
        ok("clone's source.md == current draft (never a stale round)",
           got_source == "v2 draft (current)")

        count = int(git(["rev-list", "--count", "HEAD"], cwd=CLONE_DIR).strip())
        ok("commit count == 2 historical rounds + 1 live tip == 3", count == 3)

        hashes = git(["log", "--reverse", "--format=%H"], cwd=CLONE_DIR).split()
        if len(hashes) == 3:
            root_source = git(["show", "%s:source.md" % hashes[0]], cwd=CLONE_DIR)
            mid_source = git(["show", "%s:source.md" % hashes[1]], cwd=CLONE_DIR)
            ok("oldest commit == round-0's archived draft (v0)", root_source == "v0 draft")
            ok("middle commit == round-1's archived draft (v1)", mid_source == "v1 draft")

            comments_in_root = subprocess.run(
                ["git", "show", "%s:comments.json" % hashes[0]],
                cwd=CLONE_DIR, stdout=subprocess.PIPE, stderr=subprocess.PIPE).returncode
            ok("comments.json absent from a HISTORICAL round's tree",
               comments_in_root != 0)
            tip_comments = json.loads(git(["show", "%s:comments.json" % hashes[2]], cwd=CLONE_DIR))
            ok("comments.json present in the TIP commit and non-empty",
               isinstance(tip_comments, list) and len(tip_comments) == 1)
        else:
            ok("exactly 3 commits to walk (root/middle/tip)", False)
finally:
    srv_on.terminate()
    srv_on.wait(timeout=10)

# ==== instance B: feature OFF (byte-identical-when-off contract) ================================
srv_off, base_off = boot(DATA_OFF, {})
try:
    status, body = req(base_off + "/api/reviews", "POST",
                        json.dumps({"markdown": "off"}).encode(),
                        {"Content-Type": "application/json"})
    ok("create review (flag off)", status == 201)
    rid_off = json.loads(body)["id"]

    status, _ = req(base_off + "/api/reviews/%s/git_url" % rid_off)
    ok("git_url endpoint 404s when the flag is off", status == 404)

    status, _ = req(base_off + "/git/%s.git/info/refs?service=git-upload-pack" % rid_off)
    ok("raw git route 404s when the flag is off (module never registered)", status == 404)
finally:
    srv_off.terminate()
    srv_off.wait(timeout=10)

shutil.rmtree(CLONE_DIR, ignore_errors=True)
shutil.rmtree(DATA_ON, ignore_errors=True)
shutil.rmtree(DATA_OFF, ignore_errors=True)

if FAILED[0]:
    print("%d check(s) FAILED" % FAILED[0])
    sys.exit(1)
print("all checks passed")
