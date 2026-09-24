"""The /auth/* routes, dispatched through the core's feature-module seam (the same `handle(h, m,
path) -> bool` protocol the latex module uses), so the core router needs ZERO changes to gain native
auth. The module owns its own responses (it sets the session cookie directly on the handler, which is
a BaseHTTPRequestHandler).

Routes:
  POST /auth/magic-link  {email}     issue a login link. CONSTANT response (enumeration-safe): the
                                     caller cannot tell whether the address exists or was throttled.
  GET  /auth/redeem?token=..         a confirm PAGE (no consumption). A bare GET (mail-scanner
                                     prefetch) therefore cannot burn the single-use token.
  POST /auth/redeem  token=..        confirm: verify + consume the token, link/create the account,
                                     mint the session, Set-Cookie, 303 -> /.
  GET  /auth/session                 the SPA's identity + CSRF token; slides the session's lifetime.
  POST /auth/logout                  clear the session (CSRF-checked).
  POST /auth/profile  {name}         set/clear the caller's display name (#309, CSRF-checked).
                                     "" (after trim) clears it -> every renderer falls back to email.
"""
import html
import json
import re
import smtplib
import sys
from urllib.parse import parse_qs, unquote, urlparse

RETURN_COOKIE = "mdr_return"   # set by oauth.OAuthModule on an anonymous /oauth/authorize (#395)
_SEC_HEADERS = (("Cache-Control", "no-store"), ("X-Content-Type-Options", "nosniff"))

# Shared styling for the two server-rendered auth pages (confirm / invalid link), so this interstitial
# matches the app's sign-in screen (same tokens, light+dark). Presentation only — #131.
_AUTH_CSS = (
    ":root{--bg:#fafafc;--panel:#fff;--text:#181a20;--muted:#5c6270;--rule:#e6e7ee;--link:#2f6fed}"
    "@media(prefers-color-scheme:dark){:root{--bg:#0f1014;--panel:#17181d;--text:#e9eaf0;"
    "--muted:#9a9fb0;--rule:#26282f;--link:#7ba6f5}}"
    "*{box-sizing:border-box}body{margin:0;min-height:100vh;display:flex;align-items:center;"
    "justify-content:center;padding:24px;background:var(--bg);color:var(--text);"
    "font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;font-size:14px;line-height:1.5}"
    ".card{width:100%;max-width:400px;background:var(--panel);border:1px solid var(--rule);"
    "border-radius:16px;padding:34px 32px;text-align:center;box-shadow:0 10px 44px rgba(20,22,40,.07)}"
    ".brand{display:flex;align-items:center;justify-content:center;gap:10px;font-size:16px;"
    "font-weight:600;margin:0 0 22px}.brand .logo{width:30px;height:30px;border-radius:8px;"
    "background:linear-gradient(135deg,#2f6fed,#7c4dff);color:#fff;display:flex;align-items:center;"
    "justify-content:center;font-size:12px;font-weight:700}"
    "h1{font-size:21px;font-weight:700;letter-spacing:-.3px;margin:0 0 7px}p{color:var(--muted);margin:0 0 20px}"
    "button.primary{width:100%;font:inherit;font-size:14px;font-weight:600;padding:11px 16px;"
    "border-radius:10px;border:none;background:linear-gradient(135deg,#5b46e6,#7c4dff);color:#fff;cursor:pointer}"
    "button.primary:hover{opacity:.92}.fine{color:var(--muted);font-size:12px;margin:16px 0 0}a{color:var(--link)}")


class AuthModule:
    def __init__(self, store, users, sessions, magic, accounts, identity_store):
        self.store = store
        self.users = users
        self.sessions = sessions
        self.magic = magic
        self.accounts = accounts
        self.id_store = identity_store

    # ---- low-level response (the core _send hardcodes its headers; we need Set-Cookie) ----
    @staticmethod
    def _respond(h, code, body, ctype="application/json", cookies=None, location=None):
        if isinstance(body, str):
            body = body.encode("utf-8")
        h.send_response(code)
        h.send_header("Content-Type", ctype)
        h.send_header("Content-Length", str(len(body)))
        for k, v in _SEC_HEADERS:
            h.send_header(k, v)
        if location:
            h.send_header("Location", location)
        for c in (cookies or []):
            h.send_header("Set-Cookie", c)
        h.end_headers()
        if body:
            h.wfile.write(body)

    def _json(self, h, code, obj, cookies=None):
        self._respond(h, code, json.dumps(obj), "application/json", cookies=cookies)

    @staticmethod
    def _shell(title, inner):
        """Wrap an auth interstitial (confirm / invalid) in the shared branded, theme-aware card."""
        return ("<!doctype html><html lang=en><head><meta charset=utf-8>"
                "<meta name=viewport content='width=device-width,initial-scale=1'>"
                "<title>" + title + "</title><style>" + _AUTH_CSS + "</style></head>"
                "<body><div class=card>"
                "<div class=brand><span class=logo>md</span><span>mdreview</span></div>"
                + inner + "</div></body></html>")

    @staticmethod
    def _client_ip(h):
        return h.headers.get("X-Real-IP") or h.client_address[0]

    @staticmethod
    def _form(h):
        n = int(h.headers.get("Content-Length", 0) or 0)
        raw = h.rfile.read(n).decode("utf-8", "replace") if n else ""
        return {k: v[0] for k, v in parse_qs(raw).items()}

    # ---- dispatch ----
    def handle(self, h, m, path):
        if not path.startswith("/auth/"):
            return False
        if path == "/auth/magic-link" and m == "POST":
            return self._magic_link(h)
        if path == "/auth/redeem" and m == "GET":
            return self._redeem_page(h)
        if path == "/auth/redeem" and m == "POST":
            return self._redeem(h)
        if path == "/auth/session" and m == "GET":
            return self._session(h)
        if path == "/auth/logout" and m == "POST":
            return self._logout(h)
        if path == "/auth/profile" and m == "POST":
            return self._set_profile(h)
        # #223 per-device session management. Owner-scoped: every handler resolves the caller's own
        # session first and passes its uid into the store, so a jti belonging to another account
        # cannot be read or revoked even if guessed.
        if path == "/auth/sessions" and m == "GET":
            return self._sessions_list(h)
        if path == "/auth/sessions" and m == "DELETE":
            return self._sessions_revoke_others(h)
        m_sess = re.match(r"^/auth/sessions/([A-Za-z0-9_-]+)$", path)
        if m_sess and m == "DELETE":
            return self._sessions_revoke_one(h, m_sess.group(1))
        return False

    # ---- POST /auth/magic-link ----
    def _magic_link(self, h):
        email = (h._body_json().get("email") or "").strip()
        # issue() applies the abuse limits internally and never sends when throttled; we ignore its
        # status and ALWAYS return the same body, so a caller cannot enumerate addresses or probe the
        # rate state. The write path (send_log, audit) is serialized under the store lock.
        try:
            with self.store.lock:
                self.magic.issue(email, self._client_ip(h))
        except (smtplib.SMTPException, OSError) as exc:
            # An SMTP rejection or socket failure is an expected external outcome, not a server
            # error. Letting it escape kills the request thread (the caller sees an aborted
            # connection / nginx 502), and worse, makes a rejected recipient distinguishable from
            # an accepted one — the enumeration signal this route's constant response exists to
            # deny (#302). Record it server-side only and fall through to the one constant body.
            # Deliberately NOT a bare except: an unexpected bug should still surface loudly.
            print("magic-link send failed (%s): %s" % (type(exc).__name__, exc),
                  file=sys.stderr, flush=True)
            self.id_store.audit("magiclink_send_failed", email=email, ip=self._client_ip(h),
                                detail=type(exc).__name__)
        self._json(h, 200, {"ok": True,
                            "message": "If that address can sign in, a link is on its way."})
        return True

    # ---- GET /auth/redeem (confirm page; does NOT consume) ----
    def _redeem_page(self, h):
        token = parse_qs(urlparse(h.path).query).get("token", [""])[0]
        safe = html.escape(token)
        page = self._shell("Confirm sign-in",
            "<h1>Confirm your sign-in</h1>"
            "<p>Click the button to finish signing in to mdreview.</p>"
            "<form method='POST' action='/auth/redeem'>"
            "<input type='hidden' name='token' value='" + safe + "'>"
            "<button class='primary' type='submit'>Confirm sign-in</button></form>"
            "<p class='fine'>This link is valid once and expires shortly.</p>")
        self._respond(h, 200, page, "text/html; charset=utf-8")
        return True

    # ---- POST /auth/redeem (verify + consume + mint session) ----
    def _redeem(self, h):
        token = self._form(h).get("token", "")
        email = self.magic.redeem(token)               # verifies MAC, expiry, and single-use nonce
        if not email:
            self._respond(h, 400,
                          self._shell("Link invalid",
                              "<h1>This link is invalid or expired</h1>"
                              "<p>Request a fresh sign-in link to continue.</p>"
                              "<a href='/'>Back to sign in</a>"),
                          "text/html; charset=utf-8")
            return True
        with self.store.lock:
            uid, created = self.accounts.resolve_verified_email(email)
            if created:
                self.id_store.audit("account_created", uid=uid, email=email, ip=self._client_ip(h))
        cookie_value, _csrf = self.sessions.mint(
            uid, email, ip=self._client_ip(h), user_agent=h.headers.get("User-Agent", ""))
        self.id_store.audit("login", uid=uid, email=email, ip=self._client_ip(h), detail="magic-link")
        cookies = [self.sessions.set_cookie_header(cookie_value)]
        back = self._return_to(h)
        if back != "/":
            cookies.append("%s=; Max-Age=0; Path=/; HttpOnly; Secure; SameSite=Lax" % RETURN_COOKIE)
        self._respond(h, 303, b"", "text/plain", cookies=cookies, location=back)
        return True

    @staticmethod
    def _return_to(h):
        """Where sign-in lands: "/" unless an OAuth authorize request sent the browser here first
        (#395). Only a same-origin /oauth/authorize path is honoured, so the cookie can never turn
        sign-in into an open redirect."""
        for part in (h.headers.get("Cookie") or "").split(";"):
            k, _, v = part.strip().partition("=")
            if k == RETURN_COOKIE:
                path = unquote(v)
                if re.fullmatch(r"/oauth/authorize\?[^\s\\]{1,2000}", path):
                    return path
        return "/"

    # ---- GET /auth/session (identity + CSRF; slides the lifetime) ----
    def _session(self, h):
        cookie = self.sessions.read_cookie(h)
        sess = self.sessions.verify(cookie) if cookie else None
        if not sess or not self.users.is_active(sess.uid):
            self._json(h, 200, {"authenticated": False})
            return True
        cookies = None
        if sess.should_refresh(self.sessions.refresh_after_s):
            fresh_value, fresh_csrf = self.sessions.mint(
                sess.uid, sess.email, ip=self._client_ip(h),
                user_agent=h.headers.get("User-Agent", ""))
            # The superseded row is revoked, not left live. A sliding re-issue must not double the
            # device count every 15 days, and the old cookie is being replaced in the browser
            # anyway, so keeping it valid would only widen the window on a stolen copy.
            if sess.jti:
                self.id_store.session_revoke(sess.jti, uid=sess.uid)
            cookies = [self.sessions.set_cookie_header(fresh_value)]
            csrf = fresh_csrf
        else:
            csrf = sess.csrf
            # Cheap liveness bookkeeping for the device list, throttled inside the store.
            if sess.jti:
                self.id_store.session_touch(sess.jti)
        self._json(h, 200, {"authenticated": True, "uid": sess.uid, "email": sess.email,
                            "is_admin": self.users.is_admin(sess.uid), "csrf": csrf,
                            "name": self.users.name_for(sess.uid)}, cookies=cookies)
        return True

    # ---- POST /auth/profile (#309: set/clear the display name; cookie-plane, CSRF-checked) ----
    def _set_profile(self, h):
        sess = self._require_session(h, csrf_required=True)
        if not sess:
            return True
        body = h._body_json()
        name = body.get("name") if isinstance(body, dict) else None
        with self.store.lock:
            result = self.users.set_name(sess.uid, name if isinstance(name, str) else "")
        if result == "toolong":
            self._json(h, 400, {"error": "display name must be %d characters or fewer" %
                                self.users.MAX_NAME_LEN})
            return True
        if result == "invalid":
            self._json(h, 400, {"error": "display name cannot contain control characters"})
            return True
        if result == "missing_user":
            self._json(h, 404, {"error": "no such user"})
            return True
        stored = self.users.name_for(sess.uid)
        self.id_store.audit("profile_name_set", uid=sess.uid, email=sess.email,
                            ip=self._client_ip(h), detail="cleared" if not stored else "set")
        self._json(h, 200, {"ok": True, "name": stored})
        return True

    # ---- #223 per-device sessions ----
    def _require_session(self, h, csrf_required):
        """The caller's own verified session, or None (having already answered 401/403).
        Every /auth/sessions handler goes through this, so 'owner-scoped' is one code path rather
        than a rule each handler has to remember."""
        cookie = self.sessions.read_cookie(h)
        sess = self.sessions.verify(cookie) if cookie else None
        if not sess or not self.users.is_active(sess.uid):
            self._json(h, 401, {"error": "not signed in"})
            return None
        if csrf_required and not self.sessions.check_csrf(sess, h.headers.get("X-CSRF-Token", "")):
            self._json(h, 403, {"error": "missing or invalid CSRF token"})
            return None
        return sess

    def _sessions_list(self, h):
        sess = self._require_session(h, csrf_required=False)
        if not sess:
            return True
        rows = self.id_store.sessions_for(sess.uid)
        out = []
        for r in rows:
            out.append({"jti": r["jti"], "created": r["created"], "last_seen": r["last_seen"],
                        "ip": r["ip"] or "", "user_agent": r["user_agent"] or "",
                        "current": r["jti"] == sess.jti})
        # `grandfathered` tells the UI to say the list may be incomplete rather than silently
        # showing a short list. A session minted before #223 has no row and cannot appear here.
        self._json(h, 200, {"sessions": out, "grandfathered": not sess.jti})
        return True

    def _sessions_revoke_one(self, h, jti):
        sess = self._require_session(h, csrf_required=True)
        if not sess:
            return True
        # uid-scoped: revoking another account's session is not possible even with a valid jti.
        ok = self.id_store.session_revoke(jti, uid=sess.uid)
        if ok:
            self.id_store.audit("session_revoked", uid=sess.uid, email=sess.email,
                                ip=self._client_ip(h),
                                detail="self" if jti == sess.jti else "other-device")
        self._json(h, 200 if ok else 404,
                   {"ok": ok, "current": jti == sess.jti} if ok else {"error": "no such session"})
        return True

    def _sessions_revoke_others(self, h):
        sess = self._require_session(h, csrf_required=True)
        if not sess:
            return True
        n = self.id_store.session_revoke_all(sess.uid, except_jti=sess.jti or None)
        self.id_store.audit("sessions_revoked_others", uid=sess.uid, email=sess.email,
                            ip=self._client_ip(h), detail="count=%d" % n)
        self._json(h, 200, {"ok": True, "revoked": n})
        return True

    # ---- POST /auth/logout (CSRF-checked) ----
    def _logout(self, h):
        cookie = self.sessions.read_cookie(h)
        sess = self.sessions.verify(cookie) if cookie else None
        if sess and not self.sessions.check_csrf(sess, h.headers.get("X-CSRF-Token", "")):
            # A valid session present but no matching CSRF token -> refuse (a cross-site forced logout
            # is still state-changing). No session -> nothing to protect, fall through and clear.
            self._json(h, 403, {"error": "missing or invalid CSRF token"})
            return True
        if sess:
            # #223: actually END the session, do not merely clear the cookie. Before this, a signed
            # cookie stayed cryptographically valid after "sign out", so a captured copy kept working.
            if sess.jti:
                self.id_store.session_revoke(sess.jti, uid=sess.uid)
            self.id_store.audit("logout", uid=sess.uid, email=sess.email, ip=self._client_ip(h))
        self._respond(h, 200, json.dumps({"ok": True}), "application/json",
                      cookies=[self.sessions.clear_cookie_header()])
        return True
