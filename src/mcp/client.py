"""The HTTP side: the mdreview service client and the tool-name -> (method, path, body) routing.

route() is pure mapping (no I/O); http() performs the request. A tool failure (bad id, service down,
non-2xx) becomes a ToolError the dispatcher turns into an isError result, NOT a JSON-RPC protocol
error. The static schema surface is in tools.py; the JSON-RPC plumbing in __main__.py.
"""
import os
import json
import base64
import urllib.request
import urllib.error
import webbrowser

BASE = os.environ.get("MDREVIEW_BASE", "http://localhost:8137").rstrip("/")
# Opt-in (MDREVIEW_OPEN_BROWSER): the local stdio wrapper opens a freshly created review_url in the
# user's default browser, so an agent's create_review pops the page open instead of only printing a
# link the human must copy. Off by default (CI/headless); affects create_review only.
OPEN_IN_BROWSER = os.environ.get("MDREVIEW_OPEN_BROWSER", "").lower() in ("1", "true", "yes")
# Per-user API token for a hosted, multi-user mdreview (Phase 1). Minted in the dashboard's /account
# page; set once in the MCP server env. Sent as a Bearer header so agent-created reviews are scoped
# to that user. Unset for a local/single-user instance (which needs no auth).
TOKEN = os.environ.get("MDREVIEW_TOKEN", "").strip()

# The backend is always the local mdreview service (loopback). urllib.urlopen otherwise honors the
# macOS system HTTP proxy, so when a proxy is configured but down, every call fails with a bogus
# "Connection refused" against the proxy, not the service. An empty ProxyHandler forces a direct
# connection. ponytail: proxy is never right for a loopback backend, so bypass it unconditionally.
_opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))


class ToolError(Exception):
    """A tool ran and failed (bad id, service down, non-2xx) -> isError result, not a protocol error."""


class ServiceClient:
    """One mdreview service, reached over HTTP as one caller.

    The stdio wrapper builds one from MDREVIEW_BASE/MDREVIEW_TOKEN. The service's own /mcp endpoint
    (#395) builds one per request against itself on loopback, carrying the caller's Bearer, and sets
    local_files=False because there `attach_asset(path=...)` would read the SERVER's disk."""

    def __init__(self, base, token="", local_files=True):
        self.base = base.rstrip("/")
        self.token = token
        self.local_files = local_files

    def request(self, method, path, body=None):
        """(body_text, response_headers) — the raw exchange. The headers exist for
        get_source_with_revision, which needs the ETag from the SAME response as the body (#288)."""
        url = self.base + path
        data = json.dumps(body).encode("utf-8") if body is not None else None
        req = urllib.request.Request(url, data=data, method=method)
        if data is not None:
            req.add_header("Content-Type", "application/json")
        if self.token:
            req.add_header("Authorization", "Bearer " + self.token)
        try:
            with _opener.open(req, timeout=30) as r:
                return r.read().decode("utf-8"), r.headers
        except urllib.error.HTTPError as e:
            detail = e.read().decode("utf-8", "replace")
            raise ToolError("HTTP %s from %s %s: %s" % (e.code, method, path, detail))
        except urllib.error.URLError as e:
            raise ToolError("cannot reach mdreview at %s (%s)" % (self.base, e.reason))

    def get_source_with_revision(self, review_id):
        """The opt-in get_source envelope (#288): {"source": <raw document>, "revision": N}.

        The revision comes from the ETag of the SAME GET /source response as the body — one read, one
        token, so a concurrent write can never leave the caller holding old text with a new token. The
        default get_source result stays the raw document verbatim; this envelope exists only behind the
        explicit with_revision flag (a default-on envelope would break every existing caller).
        revision is null against a pre-#288 server that sends no ETag."""
        text, headers = self.request("GET", "/api/reviews/%s/source" % review_id, None)
        etag = (headers.get("ETag") or "").strip().strip('"')
        try:
            revision = int(etag)
        except ValueError:
            revision = None
        return json.dumps({"source": text, "revision": revision})

    def call_tool(self, name, args):
        """Run one HTTP-backed tool; the result text. KeyError = missing required arg; ToolError = the
        tool ran and failed."""
        method, path, body = route(name, args, self.local_files)
        if name == "get_source" and args.get("with_revision"):
            return self.get_source_with_revision(args["id"])
        return self.request(method, path, body)[0]


# Module-level forms for the stdio wrapper and the tests. They read BASE/TOKEN at call time, so a
# caller that repoints client.BASE (tests/revision_precondition_selfcheck.py) keeps working.
def _default():
    return ServiceClient(BASE, TOKEN)


def http(method, path, body=None):
    return _default().request(method, path, body)[0]


def get_source_with_revision(review_id):
    return _default().get_source_with_revision(review_id)


def route(name, args, local_files=True):
    """Map a tool name + args onto (http_method, path, body). KeyError -> missing required arg.
    local_files=False (the remote /mcp endpoint) refuses attach_asset's `path`, which reads a file."""
    if name == "create_review":
        body = {k: args[k] for k in ("markdown", "title", "project", "session", "source_path", "kind", "template") if k in args}
        body.setdefault("markdown", args["markdown"])  # KeyError if absent -> -32602
        return "POST", "/api/reviews", body
    if name == "list_reviews":
        return "GET", "/api/reviews", None
    if name == "get_review":
        return "GET", "/api/reviews/%s" % args["id"], None
    if name == "get_source":
        return "GET", "/api/reviews/%s/source" % args["id"], None
    if name == "get_feedback":
        return "GET", "/api/reviews/%s/feedback" % args["id"], None
    if name == "get_status":
        return "GET", "/api/reviews/%s/status" % args["id"], None
    if name == "update_source":
        body = {"markdown": args["markdown"]}
        # #288: optional optimistic-concurrency precondition. Omitted = today's unconditional
        # write, byte-identical body (the no-break contract for existing callers). Sent as a body
        # key so route() stays a pure (method, path, body) mapping; an old server drops it.
        if args.get("expected_revision") is not None:
            body["expected_revision"] = args["expected_revision"]
        return "PUT", "/api/reviews/%s/source" % args["id"], body
    if name == "get_history":
        if args.get("round") is not None:
            return "GET", "/api/reviews/%s/history/%s" % (args["id"], args["round"]), None
        return "GET", "/api/reviews/%s/history" % args["id"], None
    if name == "get_git_url":
        return "GET", "/api/reviews/%s/git_url" % args["id"], None
    if name == "delete_review":
        return "DELETE", "/api/reviews/%s" % args["id"], None
    if name == "attach_asset":
        b64 = args.get("content_b64")
        if not b64 and args.get("path"):
            if not local_files:
                raise ToolError("attach_asset over the remote endpoint needs `content_b64`: `path` "
                                "names a file on your machine, which this server cannot read")
            # read + encode locally so the bytes never pass through the agent's context
            try:
                with open(os.path.expanduser(args["path"]), "rb") as f:
                    b64 = base64.b64encode(f.read()).decode("ascii")
            except OSError as e:
                raise ToolError("attach_asset cannot read path %r: %s" % (args["path"], e))
        if not b64:
            raise ToolError("attach_asset needs `path` (preferred) or `content_b64`")
        return "POST", "/api/reviews/%s/assets" % args["id"], {"name": args["name"], "content_b64": b64}
    if name == "list_assets":
        return "GET", "/api/reviews/%s/assets" % args["id"], None
    if name == "create_comment":
        anchor = {"quoted_text": args["quoted_text"]} if args.get("quoted_text") else {}
        return "POST", "/api/reviews/%s/comments" % args["document_id"], \
            {"anchor": anchor, "text": args["text"], "role": args.get("role", "agent")}
    if name == "list_comments":
        # MCP default is open (per the brief); the HTTP route's own default is all.
        status = args.get("status", "open")
        return "GET", "/api/reviews/%s/comments?status=%s" % (args["document_id"], status), None
    if name == "get_comment":
        return "GET", "/api/reviews/%s/comments/%s" % (args["document_id"], args["comment_id"]), None
    if name == "delete_comment":
        return "DELETE", "/api/reviews/%s/comments/%s" % (args["document_id"], args["comment_id"]), None
    if name == "reply_to_comment":
        return "POST", "/api/reviews/%s/comments/%s/reply" % (args["document_id"], args["comment_id"]), \
            {"text": args["text"], "role": "agent"}
    if name == "resolve_comment":
        body = {}
        if args.get("justification") is not None:
            body["justification"] = args["justification"]
        return "POST", "/api/reviews/%s/comments/%s/resolve" % (args["document_id"], args["comment_id"]), body
    if name == "hand_back":
        return "POST", "/api/reviews/%s/handoff" % args["document_id"], \
            {"to": "reviewer", "state": args.get("state", "done"), "message": args["message"]}
    if name == "ping_working":
        body = {"state": "working", "owner": args["owner"]}
        if args.get("message") is not None:
            body["message"] = args["message"]
        return "POST", "/api/reviews/%s/handoff" % args["document_id"], body
    return None  # unreachable (caller checks TOOL_NAMES first)


def open_review(text):
    """Open a freshly created review_url in the local default browser (opt-in, MDREVIEW_OPEN_BROWSER).

    Best-effort: swallows everything so it never raises into the JSON-RPC stream, and runs in the
    local stdio wrapper so it reaches the user's own machine.
    ponytail: assumes the launcher writes nothing to stdout (true for macOS `open`, our target). On a
    console-browser/Linux setup, redirect fd 1 to devnull around the call so a spawned browser can't
    corrupt the stdio protocol channel."""
    try:
        url = json.loads(text).get("review_url")
        if url:
            webbrowser.open(url)
    except Exception:
        pass
