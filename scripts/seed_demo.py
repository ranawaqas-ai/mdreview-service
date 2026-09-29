#!/usr/bin/env python3
"""Seed (or wipe) the reviewer demo account on a RUNNING mdreview server. Stdlib only.

It talks to the server over HTTP and nothing else. It never touches users.json or a database:

  1. POST /auth/demo with the access code            -> a session cookie for the demo account
  2. GET  /auth/session                              -> the session's CSRF token
  3. POST /account/tokens (X-CSRF-Token)             -> a short-lived Bearer token, labelled "seed-demo-..."
  4. create the sample reviews through the normal API with that Bearer
  5. DELETE /account/tokens/{id}                     -> revoke the token again, always, even on failure

Idempotent. Every sample review carries the title prefix "[Demo] "; a rerun deletes those and
recreates them. --wipe deletes every review the demo account owns, not just the prefixed ones.

  MDREVIEW_DEMO_LOGIN_KEY=... python3 scripts/seed_demo.py --base https://app.mdreview.space
  python3 scripts/seed_demo.py --base http://127.0.0.1:8137 --key-file ~/.mdreview-demo-login-key --wipe

The access code is read from the environment or a file, never from an argument that would show up
in `ps`, and is never printed.
"""
import argparse
import base64
import json
import os
import secrets
import struct
import sys
import urllib.error
import urllib.request
import zlib
from urllib.parse import urlencode

PREFIX = "[Demo] "
SEED_LABEL = "seed-demo-"


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *a, **k):
        return None


_OPENER = urllib.request.build_opener(_NoRedirect, urllib.request.ProxyHandler({}))


def call(base, method, path, body=None, headers=None):
    """(status, response headers, parsed JSON or raw bytes). Redirects are returned, not followed."""
    data = None
    hdrs = dict(headers or {})
    if isinstance(body, dict):
        data = json.dumps(body).encode()
        hdrs["Content-Type"] = "application/json"
    elif body is not None:
        data = body
    req = urllib.request.Request(base + path, data=data, headers=hdrs, method=method)
    try:
        with _OPENER.open(req, timeout=30) as r:
            status, rh, raw = r.status, r.headers, r.read()
    except urllib.error.HTTPError as e:
        status, rh, raw = e.code, e.headers, e.read()
    try:
        return status, rh, json.loads(raw)
    except ValueError:
        return status, rh, raw


class Demo:
    def __init__(self, base, key):
        self.base = base.rstrip("/")
        self.key = key
        self.cookie = ""
        self.csrf = ""
        self.bearer = ""

    def _need(self, status, want, what, payload=None):
        if status not in want:
            detail = payload.get("error") if isinstance(payload, dict) else ""
            raise SystemExit("%s failed: HTTP %s %s" % (what, status, detail or ""))

    def sign_in(self):
        status, hdrs, _ = call(self.base, "POST", "/auth/demo",
                               urlencode({"code": self.key}).encode(),
                               {"Content-Type": "application/x-www-form-urlencoded"})
        if status == 404:
            raise SystemExit("sign-in failed: /auth/demo is not enabled on this server")
        self._need(status, (303,), "sign-in (wrong code, or this IP is locked out)")
        for c in hdrs.get_all("Set-Cookie") or []:
            if c.startswith("mdr_session="):
                self.cookie = c.split(";", 1)[0]
        if not self.cookie:
            raise SystemExit("sign-in returned no session cookie")
        status, _, info = call(self.base, "GET", "/auth/session", headers={"Cookie": self.cookie})
        self._need(status, (200,), "session lookup")
        if not isinstance(info, dict) or not info.get("authenticated"):
            raise SystemExit("session lookup did not authenticate")
        self.csrf = info["csrf"]

    def _cookie_hdrs(self):
        return {"Cookie": self.cookie, "X-CSRF-Token": self.csrf}

    def mint_token(self):
        label = SEED_LABEL + secrets.token_hex(4)
        status, _, out = call(self.base, "POST", "/account/tokens", {"label": label},
                              self._cookie_hdrs())
        self._need(status, (201,), "token mint", out)
        self.bearer = out["token"]

    def revoke_seed_tokens(self):
        """Delete every token whose label starts with SEED_LABEL. Listing by label, not by an id
        remembered from the mint, is what covers an interrupt between the server minting the token
        and this script seeing the response (and any token orphaned by an earlier killed run)."""
        status, _, listing = call(self.base, "GET", "/account/tokens", headers=self._cookie_hdrs())
        if status != 200 or not isinstance(listing, dict):
            print("WARNING: could not list tokens (HTTP %s); revoke any seed-demo-* token on /account"
                  % status, file=sys.stderr)
            return
        for t in listing.get("tokens", []):
            if not str(t.get("label", "")).startswith(SEED_LABEL):
                continue
            status, _, _ = call(self.base, "DELETE", "/account/tokens/" + t["tok_id"],
                                headers=self._cookie_hdrs())
            if status != 200:
                print("WARNING: could not revoke seed token %s (HTTP %s); revoke it on /account"
                      % (t["tok_id"], status), file=sys.stderr)
        self.bearer = ""

    def api(self, method, path, body=None, want=(200, 201)):
        status, _, out = call(self.base, method, path, body,
                              {"Authorization": "Bearer " + self.bearer})
        self._need(status, want, "%s %s" % (method, path), out)
        return out

    # ---- content ----
    def owned(self):
        return self.api("GET", "/api/reviews")["reviews"]

    def delete_reviews(self, only_demo):
        n = 0
        for r in self.owned():
            if only_demo and not str(r.get("title", "")).startswith(PREFIX):
                continue
            self.api("DELETE", "/api/reviews/" + r["id"])
            n += 1
        return n

    def review(self, title, markdown):
        out = self.api("POST", "/api/reviews",
                       {"title": PREFIX + title, "markdown": markdown, "kind": "markdown",
                        "project": "demo"})
        return out["id"]

    def comment(self, rid, quoted, text, block="1"):
        out = self.api("POST", "/api/reviews/%s/comments" % rid,
                       {"anchor": {"quoted_text": quoted, "block_num": block}, "text": text})
        return out["comment_id"]

    def seed(self):
        rid = self.review("Notification service spec", SPEC)
        cid = self.comment(rid, "at-least-once", "Do we dedupe on the consumer side?", "6")
        self.api("POST", "/api/reviews/%s/comments/%s/reply" % (rid, cid),
                 {"text": "Yes. Each message carries an id and the consumer keeps a 24h window."})
        self.api("POST", "/api/reviews/%s/comments/%s/resolve" % (rid, cid),
                 {"justification": "Answered in the thread; the spec text already says so."})

        rid = self.review("Release checklist", CHECKLIST)
        self.comment(rid, "Rollback plan", "Which step would you cut if we are short on time?", "5")

        rid = self.review("Onboarding illustration", IMAGE_DOC)
        self.api("POST", "/api/reviews/%s/assets" % rid,
                 {"name": "tiles.png", "content_b64": base64.b64encode(tiles_png()).decode()})

        self.review("Meeting notes", NOTES)


def tiles_png(size=96, tile=16):
    """A small checkerboard-of-gradients PNG, generated here so the script needs no image file."""
    rows = []
    for y in range(size):
        row = bytearray([0])
        for x in range(size):
            on = ((x // tile) + (y // tile)) % 2 == 0
            row += bytes((60 + x * 2, 90 + y, 220) if on else (245, 200 - y, 90 + x))
        rows.append(bytes(row))
    raw = b"".join(rows)

    def chunk(kind, payload):
        body = kind + payload
        return struct.pack(">I", len(payload)) + body + struct.pack(">I", zlib.crc32(body) & 0xFFFFFFFF)

    return (b"\x89PNG\r\n\x1a\n"
            + chunk(b"IHDR", struct.pack(">IIBBBBB", size, size, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw, 9))
            + chunk(b"IEND", b""))


SPEC = """# Notification service

A small service that turns application events into user notifications.

## Goals

Deliver each event to the right channel once, in order per user, without blocking the caller.

## Delivery

Delivery is at-least-once. Producers publish to a queue and the dispatcher fans out by channel.

```mermaid
flowchart LR
    P[Producer] --> Q[(Queue)]
    Q --> D[Dispatcher]
    D --> E[Email worker]
    D --> W[Webhook worker]
    E --> U((User))
    W --> U
```

## Failure handling

A failed send retries with exponential backoff, five attempts, then lands in a dead-letter queue.
"""

CHECKLIST = """# Release checklist

Steps to ship a minor version.

## Before

- Freeze the branch and run the full test suite.
- Draft the changelog from merged pull requests.

## Ship

- Tag the release and let the pipeline build the image.
- Verify the health endpoint on the new version.

## Rollback plan

Redeploy the previous tag. Data migrations in this release are additive, so no data rollback is needed.
"""

IMAGE_DOC = """# Onboarding illustration

The welcome screen shows this pattern behind the sign-up form.

![Tile pattern for the welcome screen](tiles.png)

The tiles alternate between a cool and a warm gradient. Reply with any concerns about contrast.
"""

NOTES = """# Meeting notes

Attendees: two engineers and a designer (fictional).

## Decisions

- Ship the dashboard refresh next sprint.
- Keep the old export format for one more release.

## Follow-ups

- Designer shares updated icons on Monday.
"""


def load_key(args):
    """The code exactly as the server has it. Only a file's line terminator is dropped; any other
    leading or trailing whitespace is an error, because the server refuses to boot on such a key."""
    if args.key_file:
        with open(os.path.expanduser(args.key_file)) as f:
            key = f.read()
        key = key[:-2] if key.endswith("\r\n") else key[:-1] if key.endswith("\n") else key
    else:
        key = os.environ.get("MDREVIEW_DEMO_LOGIN_KEY", "")
    if key != key.strip():
        raise SystemExit("the access code has leading or trailing whitespace; the server would "
                         "refuse to boot with it, so fix the source of the code")
    return key


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--base", required=True, help="server root, e.g. https://app.mdreview.space")
    ap.add_argument("--key-file", help="file holding the access code (default: env MDREVIEW_DEMO_LOGIN_KEY)")
    ap.add_argument("--wipe", action="store_true", help="delete every review the demo account owns, then stop")
    args = ap.parse_args()
    key = load_key(args)
    if not key:
        raise SystemExit("no access code: set MDREVIEW_DEMO_LOGIN_KEY or pass --key-file")
    demo = Demo(args.base, key)
    demo.sign_in()
    try:
        demo.mint_token()
        if args.wipe:
            print("wiped %d review(s)" % demo.delete_reviews(only_demo=False))
        else:
            removed = demo.delete_reviews(only_demo=True)
            demo.seed()
            print("removed %d old sample review(s), created 4" % removed)
    finally:
        demo.revoke_seed_tokens()


if __name__ == "__main__":
    main()
