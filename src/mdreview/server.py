#!/usr/bin/env python3
"""mdreview-service: containerized markdown review microservice.

An agent POSTs markdown and gets a review URL for a human; the human marks it up
in a browser; the agent polls feedback back over HTTP. Multi-session (isolated by
id), stdlib only, file-backed under DATA_DIR.

API
  GET    /api/reviews                 ?scope=shared            -> {reviews}  (#284, hosted only: named
                                      shares granted TO the caller, whitelisted rows; absent scope =
                                      today's owned list, additively decorated on hosted with
                                      share_public/share_count when non-default)
  POST   /api/reviews                 {markdown, title?}      -> {id, review_url, feedback_url, source_url}
  GET    /api/reviews/{id}                                    -> meta
  DELETE /api/reviews/{id}                                    -> {deleted}
  GET    /api/reviews/{id}/source                             -> raw markdown (+ ETag: "revision", the edit precondition token)
  PUT    /api/reviews/{id}/source     {markdown}              -> meta (agent applies edits; live-reloads viewer)
                                      If-Match: "N" header or {expected_revision: N} body key (#288)
                                      -> 409 + re-read instruction when stale; omit = unconditional
                                      403 without X-CSRF-Token on a VERIFIED session (#289); other
                                      planes pass. Attribution: cookie/proxy plane (or local +
                                      X-Mdreview-Client: viewer) sets source_updated_by=reviewer,
                                      an agent write deletes it (readers default "agent")
  GET    /api/reviews/{id}/feedback                           -> {markdown, notes, ...meta}  (notes = legacy notes + projected comments)
  POST   /api/reviews/{id}/feedback                           -> 410    (retired MR-036/MR-046; viewer authors comments — POST /comments)
  GET    /api/reviews/{id}/comments   ?status=open|resolved|reopened|all  -> {comments}
  POST   /api/reviews/{id}/comments   {anchor, text, role?}   -> {comment}  (201; reviewer authors)
  GET    /api/reviews/{id}/comments/{cid}                     -> {comment}  (full thread + status_history)
  DELETE /api/reviews/{id}/comments/{cid}                     -> {deleted}  (hard-remove a junk comment)
  POST   /api/reviews/{id}/comments/{cid}/reply   {text}      -> {comment}  (append; status unchanged)
  POST   /api/reviews/{id}/comments/{cid}/resolve {justification?} -> {comment}  (409 if not
                                      open/reopened; attribution: cookie plane, or local +
                                      X-Mdreview-Client: viewer, -> reviewer; else agent, #287)
  POST   /api/reviews/{id}/comments/{cid}/reopen  {text?}     -> {comment}  (reviewer reopens; 409 if not resolved)
  GET    /api/reviews/{id}/status                             -> {status, source_updated, feedback_updated, comments_updated, revision, can_edit, source_updated_by, ...}
  POST   /api/reviews/{id}/resolve    {resolved: true|false}  -> summary  (human sign-off; cookie plane ONLY, see the arm)
  GET    /api/reviews/{id}/git_url                            -> {git_url}  (#379, MDREVIEW_ENABLE_GIT_HISTORY only; 404 when off)
  GET    /git/{id}.git/info/refs                              -> smart-HTTP ref advertisement (#379; `git clone` uses this, not an agent)
  POST   /git/{id}.git/git-upload-pack                        -> smart-HTTP pack negotiation (#379)
  GET    /review/{id}                                         -> viewer HTML (human opens)
  GET    /static/{file}                                       -> assets (marked/mermaid)
  GET    /healthz                                             -> {ok}
"""
import base64
import json
import os
import re
import shutil
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

from mdreview.config import (
    DATA_DIR, DISK_FLOOR, ENABLE_GIT_HISTORY, ENABLE_LATEX, GIT_CACHE_DIR,
    GIT_MATERIALIZE_MAX_ROUNDS, LEASE_TTL_S, MAX_BODY, OWNER_EMAIL, PORT, PROXY_SECRET,
    PUBLIC_BASE, REQUIRE_AUTH, RID, TOKEN_PEPPER, WAIT_TIMEOUT_S, WEB_DIR,
)
from mdreview.access import OperatorIdentity, OpenPolicy, OwnerPolicy, ProxyBearerIdentity
from mdreview.errors import ReviewWriteRejected
from mdreview import latexguard
from mdreview.store import Store
from mdreview.comments import CommentService
from mdreview.assets import AssetService
from mdreview.reviews import ReviewService
from mdreview.handoff import HandoffService
from mdreview.users import UserService

# A document's human-facing + raw-content read routes carry X-Robots-Tag: noindex so that a PUBLIC
# document (#101) means "anyone I hand the link to", NOT "search-engine indexed". Emitted
# unconditionally on these two routes (not only for public docs): a private doc is only ever served
# to its owner, who is not a crawler, and a crawler can only ever reach a PUBLIC doc (everything else
# 404/401s), so unconditional noindex is both correct and free of a per-request shares lookup. The
# viewer HTML additionally carries a static <meta name="robots"> for defence if a proxy strips the
# header. (The oracle transcript captures only status + Content-Type + body, so this added response
# header does not perturb the byte-identical comparison.)
NOINDEX = (("X-Robots-Tag", "noindex, nofollow"),)

# The POST routes that post a NOTE (owner OR a #68 comment-share grantee) rather than WRITE the
# document (owner-only): POST /comments and POST /comments/{cid}/reply. comment resolve/reopen are
# workflow writes and are deliberately NOT here. _authz derives can_comment-vs-can_write from this ONE
# regex, the single source of truth the central #110 choke and every per-arm gate share, so they can
# never disagree — a choke that treated a comment POST as a write would 404 a legitimate comment-share
# grantee BEFORE the arm ran, silently dropping the #68 collaboration control.
COMMENT_POST_RE = re.compile(r"/api/reviews/" + RID + r"/comments(?:/c[A-Za-z0-9]{10}/reply)?$")


class Services:
    """The composition root's bundle: one Store, injected into each service (constructor injection).
    The per-request handler reads these off the server as self.server.app.<name>, decoupled from how
    they are constructed; no service builds its own dependencies or its own lock."""

    def __init__(self, store):
        self.store = store
        self.comments = CommentService(store)
        self.assets = AssetService(store)
        self.reviews = ReviewService(store, self.comments)
        self.handoff = HandoffService(store, LEASE_TTL_S)
        self.users = UserService(store, TOKEN_PEPPER, OWNER_EMAIL)
        # Opt-in feature modules (MR-092). Each entry handles requests via H.route's dispatch
        # loop. Flag off: empty list, no import, byte-identical behavior. Flag on without the
        # package installed fails loud at boot: a misconfiguration must never boot half-enabled.
        self.modules = []
        if ENABLE_LATEX:
            import latex_review
            module, self.reviews = latex_review.build(
                store, self.reviews, self.comments, self.assets)
            self.modules.append(module)

        if ENABLE_GIT_HISTORY:
            import git_history
            from mdreview.git_history_adapter import MdreviewHistorySource
            source = MdreviewHistorySource(self.reviews, self.comments)
            module = git_history.build(GIT_CACHE_DIR, GIT_MATERIALIZE_MAX_ROUNDS, source,
                                        authorize=lambda h, rid: h._authz_read(rid))
            self.modules.append(module)

        # Access/identity seam (#103): the tier's injected (IdentityProvider, AccessPolicy) pair.
        # Wired LAST so the policy binds the FINAL self.reviews (the latex wrapper when enabled). The
        # handler consults these and hard-codes no access decision. REQUIRE_AUTH picks the tier:
        # off -> single operator, everything open; on -> oauth2-proxy/Bearer resolver + owner-only.
        if REQUIRE_AUTH:
            self.identity = ProxyBearerIdentity(self.users, store, PROXY_SECRET)
            self.policy = OwnerPolicy(self.reviews)
        else:
            self.identity = OperatorIdentity()
            self.policy = OpenPolicy(self.reviews)


class MdreviewServer(ThreadingHTTPServer):
    """ThreadingHTTPServer carrying the wired Services bundle so the per-request handler can reach it
    via self.server.app (the IoC seam; the handler never constructs a service)."""

    def __init__(self, addr, handler, app):
        super().__init__(addr, handler)
        self.app = app


class H(BaseHTTPRequestHandler):
    server_version = "mdreview/1.0"

    # ---- response helpers ----
    def _send(self, code, body=b"", ctype="application/json", extra_headers=None):
        if isinstance(body, str):
            body = body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", PUBLIC_BASE or "*")
        self.send_header("Access-Control-Allow-Methods", "GET,POST,PUT,DELETE,OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, Authorization")
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        for k, v in (extra_headers or ()):
            self.send_header(k, v)
        self.end_headers()
        # HEAD (#75): identical status + headers (incl. Content-Length above) as GET, but no body.
        if self.command != "HEAD":
            self.wfile.write(body)

    def _json(self, code, obj):
        self._send(code, json.dumps(obj), "application/json")

    def _body_json(self):
        n = int(self.headers.get("Content-Length", 0) or 0)
        raw = self.rfile.read(n) if n else b"{}"
        try:
            return json.loads(raw or b"{}")
        except json.JSONDecodeError:
            return {}

    # ---- access/identity seam (#103) ----
    # Identity resolution and every access decision live behind the injected IdentityProvider /
    # AccessPolicy on Services (self.server.app.identity / .policy); the handler hard-codes none.
    # _require_user surfaces the Principal (its callers need scope_list / stamp_owner); _authz keeps
    # the (uid, plane) contract its 15 call sites already destructure. A None plane / None Principal
    # means a 401/404 was already sent, so the caller returns.
    def _principal(self):
        """Resolve the caller to a Principal via the tier's IdentityProvider (no access decision).

        Memoized per request (reset at route() entry) so the central custody guard (#110) and the
        per-arm _authz resolve identity ONCE - avoiding a double ensure_user under store.lock on the
        cookie plane. Handler instances are REUSED across keep-alive connections, so the reset in
        route() is what stops a principal leaking from one request into the next."""
        if getattr(self, "_pcache", None) is None:
            self._pcache = self.server.app.identity.principal(self)
        return self._pcache

    def _audit(self, event, **kv):
        # Structured stdout line for an internet-facing multi-user service. NEVER logs a secret,
        # token, or the proxy header, only ids and outcomes.
        kv.update(event=event, ip=(self.headers.get("X-Real-IP") or self.client_address[0]))
        print("AUDIT " + json.dumps(kv), flush=True)

    def _disk_low(self):
        try:
            return shutil.disk_usage(DATA_DIR).free < DISK_FLOOR
        except OSError:
            return False

    def _require_user(self):
        """Identity-only gate for the account + collection routes: the Principal, or None after a 401.
        No per-review ownership (that is _authz); an active caller (the local operator included) is
        let through, an anonymous one on a require-auth tier gets 401."""
        p = self._principal()
        if p.is_anonymous:
            self._audit("auth_fail", path=urlparse(self.path).path)
            self._json(401, {"error": "authentication required"})
            return None
        return p

    def _csrf_ok(self):
        """Cookie-plane CSRF gate for the state-changing /account/tokens arms (#266). Same posture
        as SharingModule._owner(mutating=True) and the latex recompile gate (#250): only a request
        carrying a VERIFIED app-owned session cookie must present a matching X-CSRF-Token. The
        transitional proxy plane and the bearer-token plane carry no such cookie (neither is
        reachable cross-site with credentials), so they pass unchanged. app.sessions exists only on
        the hosted composition; the plain local tier has no cookie plane, so the gate is correctly
        absent there. Returns True to proceed; False after the 403 was written."""
        sessions = getattr(self.server.app, "sessions", None)
        if sessions is None:
            return True
        from mdreview.hosted.sessions import SessionService
        cookie = SessionService.read_cookie(self)
        sess = sessions.verify(cookie) if cookie else None
        if sess and not sessions.check_csrf(sess, self.headers.get("X-CSRF-Token", "")):
            self._json(403, {"error": "missing or invalid CSRF token"})
            return False
        return True

    def _authz(self, rid):
        """Per-review gate, read-order INVERTED (#103): consult the AccessPolicy FIRST, then demand
        identity only if it denied. Outcomes are byte-identical to the pre-#103 require-auth-first
        order under the owner-only policy - owner -> allowed; authenticated non-owner or absent
        review -> 404 (fail-closed, not probeable); unauthenticated -> 401 (checked BEFORE the
        exists/404 distinction, so an anonymous caller to any rid still 401s, never 404s).

        The capability is derived HERE from (command, path) — the ONE source of truth both the central
        #110 choke and every per-arm call share, so they can never disagree: GET -> can_read; DELETE ->
        can_delete; a POST to a comment route (POST /comments or .../reply, per COMMENT_POST_RE) ->
        can_comment (posting a note: owner OR a #68 comment-share grantee); every other POST/PUT ->
        can_write (document / workflow write, incl. comment resolve/reopen). On the owner-only tiers
        can_comment == can_write == can_read, so this is behaviour-preserving; only the hosted
        CustodyPolicy lets a comment-share grantee post — or a public/named-share reader read, or an
        audited admin super-read — without owning. Returns (uid, plane) on allow / (None, None) after a
        401/404, the contract every call site destructures."""
        app = self.server.app
        p = self._principal()
        m = self.command
        path = urlparse(self.path).path
        if m == "GET":
            allowed = app.policy.can_read(p, rid)
            # Audited admin super-access (#102): if the policy granted this read as a platform exception
            # (an admin reading a doc they do not own), write the immutable record through the core
            # _audit() sink - who (uid) / what (method+path) / when (ts) / which (rid+owner). The
            # classifier hook is absent on OwnerPolicy/OpenPolicy, so the base tiers are byte-identical.
            # Deduped per request (the #110 choke and the per-arm both call _authz for one GET), so a
            # single super-read is audited exactly once, never twice.
            if allowed and rid not in self._super_audited:
                classify = getattr(app.policy, "audit_super_access", None)
                rec = classify(p, rid) if classify else None
                if rec:
                    self._audit("admin_super_read", method=m, path=path, ts=time.time(), **rec)
                    self._super_audited.add(rid)
        elif m == "DELETE":
            allowed = app.policy.can_delete(p, rid)
        elif m == "POST" and COMMENT_POST_RE.fullmatch(path):
            allowed = app.policy.can_comment(p, rid)      # post a note / reply, NOT a document write
        else:                                             # POST, PUT (document write / workflow)
            allowed = app.policy.can_write(p, rid)
        if allowed:
            return (p.uid, p.plane)
        if p.is_anonymous:
            self._audit("auth_fail", path=path)
            self._json(401, {"error": "authentication required"})
            return (None, None)
        self._audit("denied_404", uid=p.uid, rid=rid)     # probing signal (missing or foreign owner)
        self._json(404, {"error": "not found"})           # 404 (not 403): ownership must not be probeable
        return (None, None)

    def _authz_read(self, rid):
        """Read-only authorization gate, independent of self.command (#379). _authz derives
        can_read/can_write from the HTTP verb, which is right for every core arm but wrong for
        git-upload-pack: that request is a POST (pack negotiation) yet it is a CLONE — a read, not
        a write. Byte-identical 401/404 shape to _authz's own GET branch; used by git_history's
        injected `authorize` callable so a repo can never be materialized or served to a caller
        who could not can_read the review through the normal API."""
        app = self.server.app
        p = self._principal()
        if app.policy.can_read(p, rid):
            return True
        if p.is_anonymous:
            self._audit("auth_fail", path=urlparse(self.path).path)
            self._json(401, {"error": "authentication required"})
            return False
        self._audit("denied_404", uid=p.uid, rid=rid)
        self._json(404, {"error": "not found"})
        return False

    def _visible_comments(self, rid, arr, with_names=False):
        """The one seam every comment-returning response arm routes through (#368): closes the
        public-link identity leak (GET /api/reviews/{rid}/comments returning a commenter's raw
        uid/email to an anonymous caller) without special-casing each arm. `entitled` is
        AccessPolicy.can_write on THIS rid — owner-only on every tier, so it is cheap (a pure
        predicate, no extra I/O) and already the exact test _authz used to admit a can_write
        caller; re-deriving it here for a can_read/can_comment arm (GET, POST /comments,
        .../reply) is what lets a comment-share grantee or a public reader still be denied raw
        identity even though they were let in to read/comment.

        with_names=True runs #309's with_author_names FIRST (name_for needs the RAW uid to
        resolve a display name) on the two GET arms only, so the public/no-account
        `redact_identity` never sees a name it would have to invent. The create/reply/resolve/
        reopen POST arms return the single comment just acted on and today carry no `name` key
        at all; adding one here would be an unrelated, unrequested payload change (and
        golden_transcript.sh drift) for a #309 feature this ticket does not extend, so they skip
        straight to redact_identity."""
        app = self.server.app
        p = self._principal()
        entitled = app.policy.can_write(p, rid)
        if with_names:
            arr = app.comments.with_author_names(arr, app.users)
        return app.comments.redact_identity(arr, p.uid, entitled)

    def _visible_meta(self, rid, m):
        """Read-time projection (#368 follow-up): GET /api/reviews/{rid} and .../feedback both
        return dict(meta.json) WHOLESALE (ReviewService.summary/feedback), which carries `owner`
        -- the document owner's raw uid -- unconditionally, to any caller who can_read the
        document. That includes an anonymous public-link reader: confirmed live,
        `curl .../api/reviews/{rid}` with no cookie and no token returned `"owner":
        "google:..."` on a public production review. Same defect class as the comments leak,
        same fix: `owner` is visible only to the ENTITLED caller -- app.policy.can_write, the
        EXACT SAME predicate _visible_comments already uses (one notion of "entitled" for the
        whole module; a document has exactly one owner, so unlike a comment thread there is no
        separate "is this caller's own value" branch to add -- can_write on a hosted tier already
        IS "are you this document's owner").

        Every OTHER meta.json key was audited and is not a second raw identity:
          - source_updated_by is #289's role literal ("reviewer" or absent -> default "agent"),
            never a uid -- verified against every writer (reviews.py put_source), not assumed.
          - agent_status.owner / agent_status.message (handoff.py) are CALLER-SUPPLIED free text
            (MCP hand_back/ping_working's "owner" is documented as "YOUR opaque session id",
            never derived from the authenticated principal) -- the same "a chosen label is not
            the leak" reasoning #309's display name already established for this ticket, so
            deliberately NOT redacted here.
          - every *_updated/created/revision key is a timestamp/counter, never identity.
        The plain GET /api/reviews list is unaffected: it 401s anonymous outright (_require_user)
        and scope_list() filters every row to the caller's OWN uid, so a returned row's `owner`
        is always the caller's own value already. ?scope=shared is a hand-whitelisted response
        (_shared_reviews) that never included `owner` even before #368 (#284 D3).

        Pure: returns a new dict when redacting, `m` itself (no copy) when entitled."""
        if self.server.app.policy.can_write(self._principal(), rid):
            return m
        m2 = dict(m)
        m2["owner"] = None
        return m2

    def _base(self):
        if PUBLIC_BASE:
            return PUBLIC_BASE
        host = self.headers.get("Host") or f"localhost:{PORT}"
        return f"http://{host}"

    def _wait(self, query, uid=None):
        """Long-poll for baton flips NEWER than a required ?since= cursor (MR-054).

        Edge-triggered, not level: returns only reviews matching the ?turn= filter whose
        turn_updated > since. A review already at turn==agent with turn_updated <= since does NOT
        return (the call blocks up to the bounded timeout), so the steady state of an agent working
        never busy-loops the watcher. Missing since defaults to now() (block for the next flip, the
        safer degrade); since=0 is the explicit backlog opt-in.

        Parks on app.store.lock.wait(timeout), which RELEASES app.store.lock while blocked, so a concurrent writer is
        never deadlocked behind a parked waiter. On wake it re-runs the predicate scan under the lock
        (notify_all wakes every waiter and wait() can wake spuriously). The scan is O(all-reviews),
        but at this scale (a handful of reviews, one operator) that is trivially cheap, and re-scanning
        is correct under rapid flips where carrying only the single last-changed rid could miss an edge.
        """
        app = self.server.app
        qs = parse_qs(query)
        turn_q = qs.get("turn", [""])[0]
        since_raw = qs.get("since", [None])[0]
        # Missing since => now (block for the next flip), NOT since=0 (the explicit backlog opt-in).
        since = time.time() if since_raw is None else app.store.to_float(since_raw, 0.0)
        client_timeout = app.store.to_float(qs.get("timeout", [None])[0], WAIT_TIMEOUT_S)
        timeout = max(0.0, min(client_timeout, WAIT_TIMEOUT_S))
        deadline = time.time() + timeout

        def matches(m):
            return ((not turn_q or m.get("turn") == turn_q)
                    and m.get("turn_updated", 0) > since)

        def changed_rows():
            return [r for r in app.reviews.list_reviews(uid) if matches(r)]

        with app.store.lock:
            rows = changed_rows()                       # baseline scan, once on entry
            while not rows:
                remaining = deadline - time.time()
                if remaining <= 0:
                    return self._json(200, {"reviews": [], "timeout": True})
                app.store.lock.wait(remaining)   # releases app.store.lock while parked
                rows = changed_rows()   # re-scan on wake; cheap at this scale and correct under rapid flips
        return self._json(200, {"reviews": rows})

    def _shared_reviews(self, p):
        """GET /api/reviews?scope=shared (#284 Part 2) — every document NAMED-shared to caller p.
        Hosted-only (getattr(app,"shares",None)); a tier with no ShareStore has nothing that could
        ever be "shared", so it answers the empty list rather than falling through to the owned
        scope (a different question the caller did not ask).

        Deliberately does NOT go through app.policy.scope_list or app.reviews.list_reviews: this is
        a different membership test entirely (ShareStore.for_subject, exactly subject=='user:<uid>'),
        so a public-only share can never surface here by construction, and neither can super_read
        (never consulted) or another user's owned/shared documents. The row is a WHITELIST, not
        summary() — never project/source_path/session/owner, the fields custody.py notes a grantee
        can already read per-document via GET /api/reviews/{id}; listing them is new, the fields
        are not (see custody.py's scope_list docstring, #284 D1)."""
        app = self.server.app
        shares = getattr(app, "shares", None)
        if shares is None:
            return self._json(200, {"reviews": []})
        out = []
        for row in shares.for_subject(p.uid):
            rid = row["rid"]
            if not app.reviews.exists(rid):
                continue                                   # a revoked/deleted doc's stray row, skip
            meta = app.reviews.meta(rid)
            # #284 D3 (owner decision, final — distinct from sharing.py's unrelated pre-existing
            # "D3" naming, the public-is-view-only rule): the "from" disclosure is the owner's
            # LOCAL-PART only, never the full address — computed HERE so the full email never
            # reaches the grantee's browser at all (not merely hidden by the UI). "" (unknown)
            # passes through unchanged, matching #267: never invented, and the caller must say so.
            owner_email = app.users.email_for(meta.get("owner", ""))
            out.append({
                "id": rid,
                "title": meta.get("title", ""),
                "kind": meta.get("kind", "markdown"),
                "created": meta.get("created", 0),
                "source_updated": meta.get("source_updated", 0),
                "feedback_updated": meta.get("feedback_updated", 0),
                "right": row["right"],
                "from_email": owner_email.split("@", 1)[0] if owner_email else "",
            })
        out.sort(key=lambda r: max(r.get("created", 0), r.get("source_updated", 0),
                                    r.get("feedback_updated", 0)), reverse=True)
        return self._json(200, {"reviews": out})

    def log_message(self, *a):
        pass

    # ---- verbs ----
    def do_OPTIONS(self):
        self._send(204)

    def do_GET(self):
        self.route("GET")

    def do_HEAD(self):
        # #75: run the GET route (so status/headers/Content-Length match GET exactly); _send drops the
        # body for HEAD. Fixes `curl -sI` probes that got 501 from the stdlib handler's missing do_HEAD.
        self.route("GET")

    def do_POST(self):
        self.route("POST")

    def do_PUT(self):
        self.route("PUT")

    def do_DELETE(self):
        self.route("DELETE")

    # ---- router ----
    def route(self, m):
        self._pcache = None            # reset the per-request principal memo (#110); handler is reused across keep-alive
        self._super_audited = set()    # per-request dedup for the #102 super-read audit (choke + per-arm call _authz)
        app = self.server.app
        path = urlparse(self.path).path
        if len(path) > 1:
            path = path.rstrip("/")

        if m in ("POST", "PUT") and int(self.headers.get("Content-Length", 0) or 0) > MAX_BODY:
            return self._json(413, {"error": "request body too large"})

        # Feature-module dispatch (MR-092): the first registered module to claim the request
        # handles it fully. Modules run before the core arms so a module may serve a core URL for
        # reviews it owns (e.g. GET /review/{id} when kind=latex); every flag off = empty list.
        for mod in app.modules:
            if mod.handle(self, m, path):
                return

        if path == "/healthz" and m == "GET":
            return self._json(200, {"ok": True})

        # The MCP wrapper serves its own source so an installed copy can self-update from the server
        # it talks to (#90/#71). Public code, unauthenticated like /static; on the hosted vhost the
        # wrapper's Bearer carries it through nginx. /version is the cheap staleness probe.
        if path in ("/install/version", "/install/wrapper") and m == "GET":
            from mcp import bundle
            if path == "/install/version":
                return self._json(200, {"wrapper_version": bundle.wrapper_version()})
            return self._json(200, bundle.wrapper_payload())

        if path in ("/", "/api") and m == "GET":
            descriptor = {
                "service": "mdreview",
                "dashboard": "GET / (HTML; this descriptor on Accept: application/json or GET /api)",
                "list_reviews": "GET /api/reviews",
                "post_a_review": "POST /api/reviews {markdown, title?, project?, source_path?, session?}",
                "collect_feedback": "GET /api/reviews/{id}/feedback",
            }
            if path == "/api" or "application/json" in self.headers.get("Accept", ""):
                return self._json(200, descriptor)
            return self._send(200, app.store.read_text(os.path.join(WEB_DIR, "dashboard.html")),
                              "text/html; charset=utf-8")

        # Account + per-user API tokens (cookie plane ONLY: an agent token can never mint/revoke a
        # token, defense-in-depth beyond the nginx routing).
        if path == "/account" and m == "GET":
            p = self._require_user()
            if p is None:
                return
            return self._send(200, app.store.read_text(os.path.join(WEB_DIR, "account.html")),
                              "text/html; charset=utf-8")
        if path == "/account/tokens":
            p = self._require_user()
            if p is None:
                return
            if p.plane == "token":
                return self._json(403, {"error": "tokens are managed from the browser, not via an API token"})
            if m == "GET":
                return self._json(200, {"tokens": app.users.list_tokens(p.uid), "base": self._base()})
            if m == "POST":
                if not self._csrf_ok():                       # #266: cross-site mint
                    return
                label = (self._body_json().get("label") or "").strip()
                with app.store.lock:
                    token = app.users.mint_token(p.uid, label)
                return self._json(201, {"token": token, "base": self._base()})
        mo = re.fullmatch(r"/account/tokens/([A-Za-z0-9]{4,40})", path)
        if mo and m == "DELETE":
            p = self._require_user()
            if p is None:
                return
            if p.plane == "token":
                return self._json(403, {"error": "tokens are managed from the browser"})
            if not self._csrf_ok():                           # #266: cross-site revoke
                return
            with app.store.lock:
                ok = app.users.revoke_token(p.uid, mo.group(1))
            return self._json(200 if ok else 404,
                              {"revoked": mo.group(1)} if ok else {"error": "no such token"})

        if path == "/api/reviews" and m == "GET":
            p = self._require_user()
            if p is None:
                return
            qs = parse_qs(urlparse(self.path).query)
            # #284 Part 2: ?scope=shared is a DISTINCT, explicit opt-in query — named shares granted
            # TO the caller, never the caller's owned list. It returns BEFORE the owned-list code
            # below, so the ?turn= filter (owned-only; shared rows carry no turn) and the badge
            # decoration (owned-only) never run for it, and an absent/other scope value falls
            # through to the untouched owned-list path (today's byte-identical behavior).
            if (qs.get("scope") or [""])[0] == "shared":
                return self._shared_reviews(p)
            reviews = app.reviews.list_reviews(app.policy.scope_list(p))
            # #284 Part 1: additive share-state badges on OWNED rows, hosted tier only (app.shares is
            # set only by build_hosted — getattr keeps the local/open tier byte-identical, same seam
            # the DELETE arm's delete_all_for cleanup already uses). ONE batched read, not 2N.
            shares = getattr(app, "shares", None)
            if shares is not None and reviews:
                counts = shares.counts_for([r.get("id") for r in reviews])
                for r in reviews:
                    c = counts.get(r.get("id"))
                    if not c:
                        continue
                    if c["public"]:
                        r["share_public"] = c["public"]
                    if c["named"]:
                        r["share_count"] = c["named"]
            # MR-054: optional ?turn= filter. Filtered in Python after list_reviews() (summary() is
            # where the turn default lands); an empty/absent value means no filter (return all),
            # preserving today's behavior. No new field, no cross-review aggregation.
            turn_q = qs.get("turn", [""])[0]
            if turn_q:
                reviews = [r for r in reviews if r.get("turn") == turn_q]
            return self._json(200, {"reviews": reviews})

        # MR-054: /wait long-poll. MUST precede the per-review RID arm — "wait" matches RID, so a
        # later placement would be shadowed into a review-id lookup (404). Collection-level: blocks
        # until a baton flips NEWER than the required ?since= cursor (an edge, not the steady-state
        # level of turn==agent), or a bounded timeout elapses.
        if path == "/api/reviews/wait" and m == "GET":
            p = self._require_user()
            if p is None:
                return
            return self._wait(urlparse(self.path).query, app.policy.scope_list(p))

        if path == "/api/reviews" and m == "POST":
            p = self._require_user()
            if p is None:
                return
            if self._disk_low():
                return self._json(507, {"error": "insufficient storage"})
            b = self._body_json()
            kind_explicit = bool(b.get("kind"))
            kind = b.get("kind", "markdown") or "markdown"
            if kind not in ("markdown", "latex"):
                return self._json(400, {"error": "kind must be 'markdown' or 'latex'"})
            # MR-100: creation-time LaTeX guard. When latex mode is enabled and the caller did NOT
            # pass kind explicitly, reject content that looks like a LaTeX paper rather than silently
            # storing it as a broken markdown review that never compiles. An explicit kind (markdown
            # or latex) always wins — kind="markdown" is the escape hatch for prose quoting LaTeX. A
            # markdown-only instance (flag off) keeps today's behavior.
            if ENABLE_LATEX and not kind_explicit and latexguard.looks_like_latex(
                    b.get("source_path", ""), b.get("markdown", "")):
                return self._json(400, {"error": "this looks like LaTeX; pass kind=\"latex\" to "
                                        "create a paper review (or kind=\"markdown\" to keep it as "
                                        "markdown)"})
            # A template id is validated inside the (flag-on) latex decorator, which raises a
            # ReviewWriteRejected subclass; core catches only that core-defined base type and never
            # imports the feature module, so the flag-off import graph and behavior are unchanged.
            #
            # #363: a latex create with no `markdown` and no `template` to seed one is ACCEPTED and
            # produces a review whose source is the empty string. That is deliberate, decided
            # against this exact ticket: _require_tex (latex_review/decorator.py) passes
            # allow_empty=True only on the create path, so starting a blank paper and filling it in
            # later stays legal, the same as a markdown create already permits an empty body
            # (hosted_boot_smoke.py's "A blank latex CREATE stays legal" case pins this). The same
            # guard rejects an empty PUT once a paper exists, so nothing already written can be
            # wiped this way. Consequence for a caller: a 201 here is not proof the request body
            # arrived. #355 hit exactly this: a fixture posted its content under "source" instead
            # of "markdown", the unknown key was silently ignored, and a 201 with an empty document
            # passed for "created". A caller that needs confirmation should read the source back
            # (GET .../source) rather than trust the status code alone.
            owner = app.policy.stamp_owner(p)
            try:
                rid = app.reviews.create(b.get("markdown", ""), b.get("title", ""),
                                      b.get("project", ""), b.get("source_path", ""),
                                      b.get("session", ""), owner=owner, kind=kind,
                                      template=b.get("template", ""))
            except ReviewWriteRejected as e:
                return self._json(e.status, e.payload or {"error": str(e)})
            # Custody audit (#110, identity-architecture.md §6): the ownership stamp is a core-side
            # custody event, logged through the SAME _audit() sink as denied_404 - who a review was
            # stamped to (empty owner = the local/open tier). Ids + outcomes only, never source/secret.
            self._audit("review_created", rid=rid, owner=owner or "")
            base = self._base()
            return self._json(201, {
                "id": rid,
                "review_url": f"{base}/review/{rid}",
                "feedback_url": f"{base}/api/reviews/{rid}/feedback",
                "source_url": f"{base}/api/reviews/{rid}/source",
                "status_url": f"{base}/api/reviews/{rid}/status",
            })

        # ---- central custody choke point (#110) ----
        # Every review-scoped path funnels through the injected AccessPolicy HERE, before its arm
        # runs, so a child resource added below (comment, asset, history, feedback, source, status,
        # handoff, viewer, ...) is gated by the SAME can_read/can_write as its PARENT review even if
        # its author forgets the per-arm _authz. This makes "a route forgot to check" structurally
        # impossible for the review-scoped surface, and is the #97 confinement rule: the document AND
        # its children route through one AccessPolicy, never re-deriving access. The per-arm _authz
        # calls below stay as defense-in-depth (memoized principal => one identity resolve).
        #
        # Scope: /api/reviews/{rid}... and /review/{rid}. The collection routes (/api/reviews,
        # /api/reviews/wait) and /account/* already returned above; non-review paths (/static, the
        # descriptor, unknown) do not match and fall through untouched. Latex /api/latex/{rid}/... is
        # gated inside the module (which runs BEFORE this router in the dispatch loop), so it never
        # reaches here. On allow we fall through to the matching arm; on deny _authz already wrote the
        # 401/404 and we return (an ungated child arm added below is still refused).
        scoped = (re.match(r"/api/reviews/" + RID + r"(?:/|$)", path)
                  or re.match(r"/review/" + RID + r"$", path))
        if scoped:
            _uid, _plane = self._authz(scoped.group(1))
            if _plane is None:
                return

        mo = re.fullmatch(r"/api/reviews/" + RID, path)
        if mo:
            rid = mo.group(1)
            uid, plane = self._authz(rid)
            if plane is None:
                return
            if m == "GET":
                return self._json(200, self._visible_meta(rid, app.reviews.summary(rid)))
            if m == "DELETE":
                with app.store.lock:
                    app.reviews.delete(rid)
                    # Drop any shares on a deleted document so no grant dangles. Hosted-only:
                    # app.shares exists solely on the hosted composition root, so getattr keeps the
                    # local/core path byte-identical (no attribute, no call).
                    shares = getattr(app, "shares", None)
                    if shares is not None:
                        shares.delete_all_for(rid)
                return self._json(200, {"deleted": rid})

        mo = re.fullmatch(r"/api/reviews/" + RID + r"/source", path)
        if mo:
            rid = mo.group(1)
            uid, plane = self._authz(rid)
            if plane is None:
                return
            if m == "GET":
                # #288: the edit-precondition token (revision) is issued atomically WITH the source
                # as an ETag — body and token read under the ONE lock, so no write can interleave
                # and hand the caller old text with a new token (the lost update the guard exists
                # to prevent). Fetching the token from a separate /status call is forbidden by
                # design for exactly that reason.
                with app.store.lock:
                    body = app.reviews.read_source(rid)
                    rev = app.reviews.meta(rid).get("revision", 0)
                return self._send(200, body, "text/markdown; charset=utf-8",
                                  extra_headers=NOINDEX + (("ETag", '"%d"' % rev),))
            if m == "PUT":
                # #289: CSRF keys on a VERIFIED SESSION, never on plane — plane "cookie" also
                # covers proxy-vouched callers, which carry no app cookie and must keep writing
                # untouched (epic #273 round-1 finding 1). The predicate lives in the hosted
                # composition as a bound app.check_csrf (build_hosted, next to app.sessions),
                # reached via getattr so the local tier's import graph is unchanged: no attribute,
                # no gate — the local tier has no cookie plane to protect. Checked before the body
                # is read, the same order as the /account/tokens arms (#266).
                check_csrf = getattr(app, "check_csrf", None)
                if check_csrf is not None and not check_csrf(self):
                    return self._json(403, {"error": "missing or invalid CSRF token"})
                b = self._body_json()
                # #288 precondition: If-Match (the browser path) or an expected_revision body key
                # (the MCP-wrapper path; an old server drops it, an old wrapper never sends it —
                # additive both ways). Absent = today's unconditional write. The compare itself
                # happens in put_source, inside the same store.lock as the write.
                expected = self.headers.get("If-Match")
                if expected is not None:
                    expected = expected.strip().strip('"')
                    if expected == "*":            # RFC 9110: If-Match: * = "exists", no precondition
                        expected = None
                else:
                    expected = b.get("expected_revision")
                if expected is not None:
                    try:
                        expected = int(expected)
                    except (TypeError, ValueError):
                        return self._json(400, {"error": "If-Match / expected_revision must be "
                                                         "the integer revision from GET /source"})
                # #289 attribution: derived from the authenticated plane, never a spoofable body
                # field (the comment-role precedent below). Cookie plane (session or proxy-vouched
                # human) -> reviewer; on the local plane the viewer identifies itself with an
                # X-Mdreview-Client: viewer header (spoofable only by the local operator, who is
                # both parties — epic #273 named risk 2); everything else (bearer token, local
                # wrapper) is an agent write.
                reviewer = (plane == "cookie"
                            or (plane == "local"
                                and self.headers.get("X-Mdreview-Client", "") == "viewer"))
                # Same seam the POST arm uses at :438 — a feature module rejects the write by
                # raising the core-defined base type, and core renders it without importing the
                # module. #188: the latex decorator raises when a latex review's body is not TeX.
                # #288: a stale expected_revision raises the same base type with status 409.
                try:
                    with app.store.lock:
                        app.reviews.put_source(rid, b.get("markdown", ""),
                                               expected_revision=expected,
                                               updated_by="reviewer" if reviewer else "agent")
                except ReviewWriteRejected as e:
                    return self._json(e.status, e.payload or {"error": str(e)})
                return self._json(200, app.reviews.meta(rid))

        mo = re.fullmatch(r"/api/reviews/" + RID + r"/feedback", path)
        if mo:
            rid = mo.group(1)
            uid, plane = self._authz(rid)
            if plane is None:
                return
            if m == "GET":
                # comment-aware: meta + feedback.md + a union of on-disk notes and a read-time
                # projection of comments (ReviewService.feedback delegates the projection to
                # CommentService). notes.json on disk is never rewritten here. #368 follow-up:
                # feedback() also returns dict(meta) wholesale, so it carries the same `owner`
                # leak GET /api/reviews/{rid} did -- same _visible_meta redaction.
                return self._json(200, self._visible_meta(rid, app.reviews.feedback(rid)))
            if m == "POST":
                # Retired (MR-046). The viewer wrote notes/feedback here until MR-036; it authors
                # comments now (POST /comments). The write is gone — return an explicit 410 (not a
                # silent 404 fall-through) so any straggler caller on this no-auth surface gets a
                # clear "use comments" signal. No write, no bump: feedback_updated has no writer
                # anymore. The GET arm above is unchanged — feedback.md/notes.json stay read-live.
                return self._json(410, {"error": "gone, use comments"})

        mo = re.fullmatch(r"/api/reviews/" + RID + r"/status", path)
        if mo and m == "GET":
            rid = mo.group(1)
            uid, plane = self._authz(rid)
            if plane is None:
                return
            # #187: summary(), not meta() - the poll now reports the derived status too (additive),
            # so a manually resolved review reads "resolved" here as well as on the list/summary.
            mt = app.reviews.summary(rid)
            return self._json(200, {
                "status": mt.get("status"),
                "source_updated": mt.get("source_updated", 0),
                "feedback_updated": mt.get("feedback_updated", 0),
                "comments_updated": mt.get("comments_updated", 0),
                # turn baton (MR-051): additive; absent on legacy reviews -> defaults below
                "turn": mt.get("turn", "reviewer"),
                "turn_updated": mt.get("turn_updated", 0),
                "handoff": mt.get("handoff"),
                "agent_status": mt.get("agent_status"),
                # #288: revision explicitly defaulted (legacy reviews predate the key), and
                # can_edit derived from the SAME custody can_write the PUT arm enforces — true for
                # the owner and the local tier (owner by construction), false for a share/comment
                # grantee or an anonymous public-share reader. This key is unconditional, so every
                # /status row in both golden-transcript tiers drifts; re-blessed under #288
                # (epic #273 named risk 3, accepted at G1 sign-off).
                "revision": mt.get("revision", 0),
                "can_edit": bool(app.policy.can_write(self._principal(), rid)),
                # #320: can_comment from the SAME custody can_comment the POST /comments arm
                # enforces, for the same reason can_edit exists — the viewer had no way to know a
                # reader may not comment, so it offered the composer to a view-only grantee and to a
                # public-link visitor, then reported the server's correct 404 as "Could not save
                # comment". True for the owner, a "comment"-share grantee and the local tier; false
                # for a view-only grantee, a public-link reader, and anonymous.
                "can_comment": bool(app.policy.can_comment(self._principal(), rid)),
                # #289: who authored the current draft. Lifecycle: a reviewer write SETS the meta
                # key, an agent write DELETES it, readers default "agent" — so this echo is the
                # documented default, not a new persisted field. /status is already exempt from
                # the byte-identical contract (#288's unconditional can_edit); every other
                # response stays untouched for an all-agent-plane review.
                "source_updated_by": mt.get("source_updated_by", "agent"),
            })

        # ---- manual resolve (#187): a human sign-off, APPROVAL-CLASS, cookie plane ONLY ----------
        # product.md: "whatever a human can do in the viewer, an agent can do over MCP, except
        # approve" - and a manual resolve IS an approval, not inbox tidying (owner decision,
        # 2026-07-29, recorded on #187). So this route DELIBERATELY DIVERGES from the documented
        # sharing posture (SharingModule's "authenticated via the session OR the agent token"): the
        # bearer-token plane is REFUSED here, even for a token that owns the review and can write
        # everywhere else, because an agent must never be able to mark its own work done. No MCP
        # tool exists for this route, in any plane - do not "fix" the 403 below back to parity.
        # Owner-only via the same custody can_write choke as every workflow write; CSRF-checked in
        # the shape SharingModule._owner / LatexModule._recompile use (app.sessions exists only on
        # the hosted composition; the local tier has no cookie plane, so the getattr gate is
        # correctly absent there, and the transitional proxy plane carries no app cookie).
        mo = re.fullmatch(r"/api/reviews/" + RID + r"/resolve", path)
        if mo and m == "POST":
            rid = mo.group(1)
            uid, plane = self._authz(rid)
            if plane is None:
                return
            if plane == "token":
                self._audit("resolve_token_refused", uid=uid, rid=rid)
                return self._json(403, {"error": "manual resolve is a human sign-off; "
                                        "it cannot be performed with an API token"})
            sessions = getattr(app, "sessions", None)
            if sessions is not None:
                from mdreview.hosted.sessions import SessionService
                cookie = SessionService.read_cookie(self)
                sess = sessions.verify(cookie) if cookie else None
                if sess and not sessions.check_csrf(sess, self.headers.get("X-CSRF-Token", "")):
                    return self._json(403, {"error": "missing or invalid CSRF token"})
            resolved = self._body_json().get("resolved")
            if not isinstance(resolved, bool):
                return self._json(400, {"error": "resolved must be true or false"})
            with app.store.lock:
                app.reviews.set_resolved(rid, resolved)
            self._audit("review_resolved" if resolved else "review_unresolved", uid=uid, rid=rid)
            return self._json(200, app.reviews.summary(rid))

        # Turn baton handoff (MR-051/054/055). The guarded read-decide-write of the turn/lease state
        # lives in HandoffService.apply, called under the one lock so the write and the /wait wake are
        # atomic (no flip is missed); the arm just frames the request.
        mo = re.fullmatch(r"/api/reviews/" + RID + r"/handoff", path)
        if mo and m == "POST":
            rid = mo.group(1)
            uid, plane = self._authz(rid)
            if plane is None:
                return
            with app.store.lock:
                err = app.handoff.apply(rid, self._body_json())
            if err:
                return self._json(*err)
            return self._json(200, app.reviews.meta(rid))

        mo = re.fullmatch(r"/api/reviews/" + RID + r"/history", path)
        if mo and m == "GET":
            rid = mo.group(1)
            uid, plane = self._authz(rid)
            if plane is None:
                return
            return self._json(200, {"rounds": app.reviews.history(rid)})

        mo = re.fullmatch(r"/api/reviews/" + RID + r"/history/(\d+)", path)
        if mo and m == "GET":
            rid, n = mo.group(1), mo.group(2)
            uid, plane = self._authz(rid)
            if plane is None:
                return
            out = app.reviews.history_round(rid, n)
            if out is None:
                return self._json(404, {"error": "no such round"})
            return self._json(200, out)

        mo = re.fullmatch(r"/api/reviews/" + RID + r"/git_url", path)
        if mo and m == "GET" and ENABLE_GIT_HISTORY:
            rid = mo.group(1)
            uid, plane = self._authz(rid)      # GET -> can_read, exactly what a clone needs
            if plane is None:
                return
            return self._json(200, {"git_url": "%s/git/%s.git" % (self._base(), rid)})

        mo = re.fullmatch(r"/api/reviews/" + RID + r"/assets", path)
        if mo:
            rid = mo.group(1)
            uid, plane = self._authz(rid)
            if plane is None:
                return
            base = self._base()
            if m == "GET":
                out = []
                for e in app.assets.list(rid):
                    d = dict(e)
                    d["url"] = f"{base}/api/reviews/{rid}/asset/{e['stored']}"
                    out.append(d)
                return self._json(200, {"assets": out})
            if m == "POST":
                if self._disk_low():
                    return self._json(507, {"error": "insufficient storage"})
                b = self._body_json()
                name = b.get("name", "")
                c64 = b.get("content_b64")
                if not name or not c64:
                    return self._json(400, {"error": "name and content_b64 required"})
                try:
                    data = base64.b64decode(c64, validate=True)
                except (ValueError, TypeError):
                    return self._json(400, {"error": "content_b64 is not valid base64"})
                with app.store.lock:
                    entry = app.assets.attach(rid, name, data)
                return self._json(201, {
                    "name": entry["name"], "stored": entry["stored"],
                    "url": f"{base}/api/reviews/{rid}/asset/{entry['stored']}",
                    "bytes": entry["bytes"], "ctype": entry["ctype"],
                })

        mo = re.fullmatch(r"/api/reviews/" + RID + r"/comments", path)
        if mo:
            rid = mo.group(1)
            # A POST here posts a note, gated by can_comment (owner OR a comment-share grantee on the
            # hosted tier), NOT can_write — _authz derives that from the path (COMMENT_POST_RE). GET
            # stays can_read, so a view-share grantee can read the thread. Byte-identical on owner tiers.
            uid, plane = self._authz(rid)
            if plane is None:
                return
            if m == "GET":
                q = parse_qs(urlparse(self.path).query)
                status = (q.get("status") or ["all"])[0] or "all"
                # #309: an ADDITIVE read-time projection (thread[].name), never a stored write —
                # see CommentService.with_author_names for what it does and does not touch. #368:
                # the SAME response also drops the raw create-time uid from a reader who is not
                # its author and not the owner — see _visible_comments / redact_identity.
                comments = self._visible_comments(rid, app.comments.list(rid, status), with_names=True)
                return self._json(200, {"comments": comments})
            if m == "POST":
                b = self._body_json()
                # Attribution from the authenticated plane, not spoofable body fields: a human on the
                # cookie plane is a reviewer; an agent on the token plane is an agent. Local/dev keeps
                # the body values for back-compat.
                if plane == "cookie":
                    author, role = uid, "reviewer"
                elif plane == "token":
                    author, role = uid, "agent"
                else:
                    author, role = b.get("author"), b.get("role", "reviewer")
                with app.store.lock:
                    c = app.comments.create(rid, b.get("anchor") or {}, b.get("text", ""),
                                         author, role)
                    app.reviews.bump(rid, "comments_updated")
                # #368: harmless for THIS response (the caller always is the entry's own author —
                # you cannot learn anyone else's identity from your own create call) but routed
                # through the same seam as every other comment-returning arm on principle, so a
                # future change here never becomes the one arm that forgets it.
                return self._json(201, self._visible_comments(rid, [c])[0])

        mo = re.fullmatch(r"/api/reviews/" + RID + r"/comments/(c[A-Za-z0-9]{10})", path)
        if mo and m in ("GET", "DELETE"):
            rid, cid = mo.group(1), mo.group(2)
            uid, plane = self._authz(rid)
            if plane is None:
                return
            if m == "GET":
                c = app.comments.get(rid, cid)
                if not c:
                    return self._json(404, {"error": "no such comment"})
                return self._json(200, self._visible_comments(rid, [c], with_names=True)[0])
            # DELETE: hard-remove a comment (junk cleanup), distinct from resolve, which only hides it.
            with app.store.lock:
                if not app.comments.delete(rid, cid):
                    return self._json(404, {"error": "no such comment"})
                app.reviews.bump(rid, "comments_updated")
            return self._json(200, {"deleted": cid})

        mo = re.fullmatch(
            r"/api/reviews/" + RID + r"/comments/(c[A-Za-z0-9]{10})/(reply|resolve|reopen)", path)
        if mo and m == "POST":
            rid, cid, action = mo.group(1), mo.group(2), mo.group(3)
            # A reply is commenting (can_comment: owner OR comment-share grantee); resolve/reopen are
            # workflow transitions and stay owner-only (can_write). _authz derives which from the path
            # (COMMENT_POST_RE matches .../reply, not .../resolve|reopen). Byte-identical on owner tiers.
            uid, plane = self._authz(rid)
            if plane is None:
                return
            b = self._body_json()
            if action == "reply":
                # Attribution from the authenticated plane, not the spoofable body role (mirrors POST /comments).
                by = "reviewer" if plane == "cookie" else "agent" if plane == "token" else b.get("role", "reviewer")
                text = b.get("text", "")
            elif action == "resolve":
                # #287 D2: plane-derived, same disambiguator as the #289 source-PUT arm (:645) —
                # resolve is can_write like that arm, not can_comment like reply above, so its
                # local-plane ambiguity gets the header check rather than reply's spoofable body
                # field. Both viewers already send X-Mdreview-Client: viewer on their PUT /source
                # (editguard.js); resolveComment() sends it too so a human's click on the local
                # (single-operator, no REQUIRE_AUTH) tier is not misattributed to the agent.
                by = ("reviewer" if plane == "cookie"
                      or (plane == "local" and self.headers.get("X-Mdreview-Client", "") == "viewer")
                      else "agent")
                text = b.get("justification")                        # justification optional
            else:                                                    # reopen
                by, text = "reviewer", b.get("text")                 # reviewer reply optional
            with app.store.lock:
                code, payload = app.comments.apply_transition(rid, cid, action, by, text)
                if code == 200:
                    app.reviews.bump(rid, "comments_updated")
            # #368: a reply is can_comment (owner OR a comment-share grantee), and apply_transition
            # returns the WHOLE updated comment — including thread[0]/created_by/status_history[0]
            # from whoever originally opened it, which may not be this caller. Route the 200 through
            # the same redaction as every GET (a 409/404/400 error body has no "thread" to redact,
            # and is unaffected by the guard inside redact_identity/with_author_names either way).
            if code == 200:
                payload = self._visible_comments(rid, [payload])[0]
            return self._json(code, payload)

        mo = re.fullmatch(r"/api/reviews/" + RID + r"/asset/([A-Za-z0-9._-]+)", path)
        if mo and m == "GET":
            rid, stored = mo.group(1), mo.group(2)
            uid, plane = self._authz(rid)
            if plane is None:
                return
            # resolve via the manifest only; never path-join the request segment
            entry = app.assets.find(rid, stored)
            if not entry:
                return self._send(404, "asset not found", "text/plain")
            p = app.assets.path(rid, stored)
            if not os.path.isfile(p):
                return self._send(404, "asset not found", "text/plain")
            return self._send(200, app.store.read_bytes(p), entry.get("ctype") or app.store.ctype_for(stored))

        mo = re.fullmatch(r"/review/" + RID, path)
        if mo and m == "GET":
            rid = mo.group(1)
            uid, plane = self._authz(rid)      # non-owner (or unauth) gets 404, no viewer shell leak
            if plane is None:
                return
            return self._send(200, app.store.read_text(os.path.join(WEB_DIR, "viewer.html")),
                              "text/html; charset=utf-8", extra_headers=NOINDEX)

        mo = re.fullmatch(r"/static/([A-Za-z0-9._-]+)", path)
        if mo and m == "GET":
            fn = mo.group(1)
            p = os.path.join(WEB_DIR, "static", fn)
            if os.path.isfile(p):
                # binary read: KaTeX ships .woff2 fonts + .css that the utf-8 _read crashes on
                return self._send(200, app.store.read_bytes(p), app.store.ctype_for(fn))
            return self._send(404, "not found", "text/plain")

        self._json(404, {"error": "no route", "method": m, "path": path})


def main():
    app = Services(Store(DATA_DIR))
    print(f"mdreview-service listening on :{PORT}  data={DATA_DIR}", flush=True)
    MdreviewServer(("0.0.0.0", PORT), H, app).serve_forever()


if __name__ == "__main__":
    main()
