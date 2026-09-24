"""The OAuth 2.1 authorization server a remote MCP client signs in through (#395), dispatched through
the core feature-module seam. Hosted tier only: the local tier is authless and has no /mcp 401.

  GET  /.well-known/oauth-protected-resource[/mcp]  RFC 9728 metadata for the /mcp resource.
  GET  /.well-known/oauth-authorization-server      RFC 8414 metadata; issuer = the public base.
  POST /oauth/register     RFC 7591 dynamic registration. Public clients only (no secret), and only
                           redirect URIs on ALLOWED_REDIRECTS, so a consent screen can never hand a
                           code to a site that merely registered itself.
  GET  /oauth/authorize    validates the request, then shows a consent page to the signed-in user.
  POST /oauth/authorize    the consent decision; approve -> 302 to the client with a code.
  POST /oauth/token        authorization_code (PKCE S256) and refresh_token (rotating) grants.

The access token IS an mdr_ token (UserService.mint_token with a 1h expiry), so /mcp, /api/* and the
/account list-and-revoke all treat it like any other token. Revoking it on /account also kills the
connector: its refresh token names the access token's tok_id, and a refresh whose tok_id is gone is
refused. CIMD is deliberately not advertised (client_id_metadata_document_supported absent), so
clients use registration and this server never fetches a client-supplied URL.

Consent is protected by the pending row: the request is re-validated and parked server-side under
an unguessable nonce bound to the uid that saw the page, and the form carries only that nonce, so a
forged POST would need a nonce only the victim's own page ever showed. The POST must also come from
this origin (Origin, else Sec-Fetch-Site), and the page refuses to be framed. There is deliberately
no consent cookie: one cookie per browser meant a second consent load (a retried popup, an old tab)
silently invalidated the first page's Allow button.
"""
import base64
import hashlib
import hmac
import html
import json
import re
from urllib.parse import parse_qs, quote, urlencode, urlparse

from mdreview.hosted.authroutes import RETURN_COOKIE, AuthModule   # _redeem honours RETURN_COOKIE

ALLOWED_REDIRECTS = ("https://claude.ai/api/mcp/auth_callback",
                     "https://claude.com/api/mcp/auth_callback")
_LOOPBACK_HOSTS = ("localhost", "127.0.0.1")

ACCESS_TTL_S = 3600
REFRESH_TTL_S = 30 * 86400
CODE_TTL_S = 600
CONSENT_TTL_S = 600

_FRAME_HEADERS = (("Content-Security-Policy", "frame-ancestors 'none'"), ("X-Frame-Options", "DENY"))


def canonical_resource(url):
    """RFC 8707 canonical form: lowercase scheme and host, no default port, no trailing slash."""
    p = urlparse(url or "")
    netloc = p.hostname or ""
    if p.port and p.port != {"https": 443, "http": 80}.get(p.scheme.lower()):
        netloc += ":%d" % p.port
    return "%s://%s%s" % (p.scheme.lower(), netloc.lower(), p.path.rstrip("/"))


def redirect_allowed(uri):
    if uri in ALLOWED_REDIRECTS:
        return True
    p = urlparse(uri)
    return p.scheme == "http" and p.hostname in _LOOPBACK_HOSTS and not p.query and not p.fragment


def redirect_matches(registered, uri):
    """Exact match, except a loopback URI may differ in port (RFC 8252 section 7.3)."""
    if uri in registered:
        return True
    p = urlparse(uri)
    if p.scheme != "http" or p.hostname not in _LOOPBACK_HOSTS:
        return False
    return any(urlparse(r).hostname == p.hostname and urlparse(r).path == p.path
               and urlparse(r).scheme == "http" for r in registered)


def pkce_ok(verifier, challenge):
    if not verifier or not re.fullmatch(r"[A-Za-z0-9._~-]{43,128}", verifier):
        return False
    computed = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode("ascii")).digest())
    return hmac.compare_digest(computed.rstrip(b"=").decode("ascii"), challenge or "")


class OAuthModule:
    def __init__(self, base, store, users, oauth_store, id_store):
        self.base = base.rstrip("/")
        self.resource = canonical_resource(self.base + "/mcp")
        self.store = store                  # the core Store: its lock guards users.json writes
        self.users = users
        self.db = oauth_store
        self.id_store = id_store

    def handle(self, h, m, path):
        if path in ("/.well-known/oauth-protected-resource", "/.well-known/oauth-protected-resource/mcp") \
                and m == "GET":
            return self._json(h, 200, {"resource": self.resource,
                                       "authorization_servers": [self.base],
                                       "bearer_methods_supported": ["header"]})
        if path == "/.well-known/oauth-authorization-server" and m == "GET":
            return self._json(h, 200, self._as_metadata())
        if path == "/oauth/register" and m == "POST":
            return self._register(h)
        if path == "/oauth/authorize" and m == "GET":
            return self._authorize_page(h)
        if path == "/oauth/authorize" and m == "POST":
            return self._consent(h)
        if path == "/oauth/token" and m == "POST":
            return self._token(h)
        return False

    def _as_metadata(self):
        return {
            "issuer": self.base,
            "authorization_endpoint": self.base + "/oauth/authorize",
            "token_endpoint": self.base + "/oauth/token",
            "registration_endpoint": self.base + "/oauth/register",
            "response_types_supported": ["code"],
            "grant_types_supported": ["authorization_code", "refresh_token"],
            "code_challenge_methods_supported": ["S256"],
            "token_endpoint_auth_methods_supported": ["none"],
        }

    # ---- POST /oauth/register ----
    def _register(self, h):
        try:
            body = json.loads(self._raw(h) or b"{}")
        except ValueError:
            body = None
        if not isinstance(body, dict):
            return self._json(h, 400, {"error": "invalid_client_metadata",
                                       "error_description": "expected a JSON object"})
        uris = body.get("redirect_uris")
        if not isinstance(uris, list) or not uris or not all(isinstance(u, str) for u in uris):
            return self._json(h, 400, {"error": "invalid_redirect_uri",
                                       "error_description": "redirect_uris must be a non-empty list"})
        bad = [u for u in uris if not redirect_allowed(u)]
        if bad:
            return self._json(h, 400, {"error": "invalid_redirect_uri",
                                       "error_description": "redirect URI not allowed: %s" % bad[0]})
        name = str(body.get("client_name") or "MCP client")[:80]
        client_id = self.db.register_client(name, uris)
        if client_id is None:
            return self._json(h, 503, {"error": "temporarily_unavailable"})
        return self._json(h, 201, {"client_id": client_id, "client_name": name, "redirect_uris": uris,
                                   "token_endpoint_auth_method": "none",
                                   "grant_types": ["authorization_code", "refresh_token"],
                                   "response_types": ["code"]})

    # ---- GET /oauth/authorize ----
    def _authorize_page(self, h):
        q = {k: v[0] for k, v in parse_qs(urlparse(h.path).query).items()}
        client = self.db.get_client(q.get("client_id", ""))
        redirect_uri = q.get("redirect_uri", "")
        # Until client and redirect are proven, errors go on a page: never redirect to an unvetted URI.
        if not client or not redirect_matches(client["redirect_uris"], redirect_uri):
            return self._page(h, 400, "Cannot sign in", "<h1>This sign-in request is not valid</h1>"
                              "<p>The application that sent you here is not registered with "
                              "mdreview, or it asked to return to an address it did not register.</p>")
        state = q.get("state", "")
        if q.get("response_type") != "code":
            return self._redirect_error(h, redirect_uri, state, "unsupported_response_type")
        if not q.get("code_challenge") or q.get("code_challenge_method") != "S256":
            return self._redirect_error(h, redirect_uri, state, "invalid_request",
                                        "PKCE with code_challenge_method=S256 is required")
        if q.get("resource") and canonical_resource(q["resource"]) != self.resource:
            return self._redirect_error(h, redirect_uri, state, "invalid_target")

        p = h._principal()
        if p.is_anonymous:
            # Staging-style app sign-in: remember where to come back to, then sign in. On prod the
            # proxy has already signed the browser in before this handler runs.
            return self._respond(h, 302, b"", location="/",
                                 cookies=["%s=%s; Max-Age=900; Path=/; HttpOnly; Secure; SameSite=Lax"
                                          % (RETURN_COOKIE, quote(h.path, safe=""))])
        if p.plane == "token":
            return self._page(h, 403, "Cannot sign in", "<h1>Sign in with a browser</h1>"
                              "<p>An API token cannot approve access for an application.</p>")

        nonce = self.db.put_pending(p.uid, client["client_id"], redirect_uri, q["code_challenge"],
                                    state, self.resource, CONSENT_TTL_S)
        who = html.escape(p.email or self.users.email_for(p.uid) or p.uid)
        app_name = html.escape(client["client_name"])
        dest = html.escape(urlparse(redirect_uri).netloc)
        inner = ("<h1>Allow %s to use mdreview?</h1>"
                 "<p>It will act as <b>%s</b>: list, read, create, edit and delete your reviews and "
                 "comments. You can revoke it any time on your Account page.</p>"
                 "<p class='fine'>You will be sent back to %s.</p>"
                 "<form method=post action='/oauth/authorize'>"
                 "<input type=hidden name=nonce value='%s'>"
                 "<button name=decision value=approve>Allow</button> "
                 "<button name=decision value=deny>Cancel</button></form>") % (app_name, who, dest, nonce)
        return self._page(h, 200, "Allow access", inner)

    # ---- POST /oauth/authorize ----
    def _consent(self, h):
        form = AuthModule._form(h)
        nonce = form.get("nonce", "")
        p = h._principal()
        if not nonce or p.is_anonymous or not self._same_origin(h):
            return self._page(h, 403, "Cannot sign in", "<h1>This approval could not be verified</h1>"
                              "<p>Start the connection again from the application.</p>")
        pending = self.db.take_pending(nonce)
        if not pending or pending["uid"] != p.uid:
            return self._page(h, 403, "Cannot sign in", "<h1>This approval has expired</h1>"
                              "<p>Start the connection again from the application.</p>")
        if form.get("decision") != "approve":
            return self._redirect_error(h, pending["redirect_uri"], pending["state"], "access_denied")
        code = self.db.put_code(p.uid, pending["client_id"], pending["redirect_uri"],
                                pending["challenge"], pending["resource"], CODE_TTL_S)
        self.db.mark_used(pending["client_id"])
        self.id_store.audit("oauth_grant", uid=p.uid, ip=AuthModule._client_ip(h),
                            detail=pending["client_id"])
        return self._respond(h, 302, b"", location=self._with_query(
            pending["redirect_uri"], {"code": code, "state": pending["state"]}))

    # ---- POST /oauth/token ----
    def _token(self, h):
        form = AuthModule._form(h)
        grant = form.get("grant_type")
        if grant == "authorization_code":
            return self._code_grant(h, form)
        if grant == "refresh_token":
            return self._refresh_grant(h, form)
        return self._json(h, 400, {"error": "unsupported_grant_type"})

    def _code_grant(self, h, form):
        row = self.db.take_code(form.get("code", ""))
        if not row or row["client_id"] != form.get("client_id") \
                or (form.get("redirect_uri") and row["redirect_uri"] != form["redirect_uri"]) \
                or (form.get("resource") and canonical_resource(form["resource"]) != row["resource"]) \
                or not pkce_ok(form.get("code_verifier", ""), row["challenge"]):
            return self._json(h, 400, {"error": "invalid_grant"})
        return self._issue(h, row["uid"], row["client_id"], row["resource"])

    def _refresh_grant(self, h, form):
        row = self.db.take_refresh(form.get("refresh_token", ""))
        if not row or row["client_id"] != form.get("client_id"):
            return self._json(h, 400, {"error": "invalid_grant"})
        with self.store.lock:
            # Revoked on /account (or by a ban) -> the grant is over; the rotation stops here.
            alive = self.users.revoke_token(row["uid"], row["tok_id"])
        if not alive or not self.users.is_active(row["uid"]):
            return self._json(h, 400, {"error": "invalid_grant"})
        return self._issue(h, row["uid"], row["client_id"], row["resource"])

    def _issue(self, h, uid, client_id, resource):
        client = self.db.get_client(client_id) or {}
        with self.store.lock:
            access = self.users.mint_token(uid, "OAuth: %s" % client.get("client_name", "MCP client"),
                                           ttl_s=ACCESS_TTL_S)
        refresh = self.db.put_refresh(uid, client_id, self.users.token_id(access), resource,
                                      REFRESH_TTL_S)
        return self._json(h, 200, {"access_token": access, "token_type": "Bearer",
                                   "expires_in": ACCESS_TTL_S, "refresh_token": refresh})

    # ---- responses ----
    @staticmethod
    def _respond(h, code, body, ctype="application/json", cookies=None, location=None):
        AuthModule._respond(h, code, body, ctype, cookies=cookies, location=location)
        return True

    def _json(self, h, code, obj):
        return self._respond(h, code, json.dumps(obj))

    def _page(self, h, code, title, inner, cookies=None):
        body = AuthModule._shell(title, inner).encode("utf-8")
        h.send_response(code)
        for k, v in (("Content-Type", "text/html; charset=utf-8"), ("Content-Length", str(len(body))),
                     ("Cache-Control", "no-store"), ("X-Content-Type-Options", "nosniff")) + _FRAME_HEADERS:
            h.send_header(k, v)
        for c in cookies or ():
            h.send_header("Set-Cookie", c)
        h.end_headers()
        h.wfile.write(body)
        return True

    def _redirect_error(self, h, redirect_uri, state, error, description=None, cookies=None):
        params = {"error": error, "state": state}
        if description:
            params["error_description"] = description
        return self._respond(h, 302, b"", location=self._with_query(redirect_uri, params), cookies=cookies)

    @staticmethod
    def _with_query(uri, params):
        return uri + ("&" if urlparse(uri).query else "?") + urlencode({k: v for k, v in params.items() if v})

    def _same_origin(self, h):
        """A browser form post names its origin; one from another site is refused. With neither
        header (not a browser) there is no victim session to ride, so the nonce check suffices."""
        origin = h.headers.get("Origin")
        if origin:
            own = urlparse(self.base)
            return origin == "%s://%s" % (own.scheme, own.netloc)
        site = h.headers.get("Sec-Fetch-Site")
        return site in (None, "same-origin")

    @staticmethod
    def _raw(h):
        n = int(h.headers.get("Content-Length", 0) or 0)
        return h.rfile.read(n) if n else b""
