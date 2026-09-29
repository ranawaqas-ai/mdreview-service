"""Reviewer sign-in for the directory submission: one URL and one access code, no inbox.

Anthropic's directory reviewers must connect to mdreview and run its tools against a populated
account, but normal sign-in is an emailed magic link only the account holder can read. This module
adds GET/POST /auth/demo, which trades a shared access code for an ordinary session on one fixed
demo account.

The module exists only when MDREVIEW_DEMO_LOGIN_KEY is set (compose.py adds it before AuthModule,
which claims every /auth/* path). With the variable unset the route is whatever AuthModule answers
for an unknown path, a 404; there is no on/off branch in a request handler.

Security shape:
  * The code is read from the POST form body only, never from the URL, so it cannot land in a
    proxy access log, browser history or a Referer header.
  * Comparison is hmac.compare_digest over SHA-256 digests of both sides (fixed length, no
    early exit on a length mismatch).
  * Every wrong or malformed code gets the same 403 page, byte for byte.
  * Per client IP, MAX_FAILS failures inside WINDOW_S lock that IP out of the route for the rest of
    the window, even for the right code. There is deliberately no global lockout, so a stranger
    cannot lock reviewers out from a different address.
  * The code never appears in a response, a Location header, a log line or an audit row. Audit
    rows carry the IP and user agent only.
  * The demo account is an ordinary email: account. It holds no admin or super-read right.
"""
import hashlib
import hmac
import threading
import time
from urllib.parse import parse_qs

from mdreview.hosted.authroutes import RETURN_COOKIE, AuthModule

DEMO_EMAIL = "mdreview-demo@mdreview.space"
MIN_KEY_LEN = 32
MAX_FAILS = 5
WINDOW_S = 600
_MAX_BODY = 4096
_MAX_UA = 200
_MAX_IP = 64                 # X-Real-IP is client-supplied text; bound what we key and store on
_MAX_TRACKED = 10000         # most IPs held in the failure counter; the oldest is dropped past this
_PRUNE_EVERY_S = 30


def require_key_strength(key):
    """Boot guard. Never echoes the key, only its length."""
    if key != key.strip():
        raise SystemExit("MDREVIEW_DEMO_LOGIN_KEY has leading or trailing whitespace; remove it "
                         "(a stray newline from an .env edit is the usual cause). Unset it to "
                         "disable /auth/demo.")
    if len(key) < MIN_KEY_LEN:
        raise SystemExit("MDREVIEW_DEMO_LOGIN_KEY is set but only %d characters long; it must be at "
                         "least %d (generate one with: python3 -c \"import secrets; "
                         "print(secrets.token_urlsafe(32))\"). Unset it to disable /auth/demo."
                         % (len(key), MIN_KEY_LEN))


def _digest(text):
    return hashlib.sha256(text.encode("utf-8", "replace")).digest()


class DemoLoginModule:
    def __init__(self, store, accounts, sessions, identity_store, key, clock=time.time):
        self.store = store
        self.accounts = accounts
        self.sessions = sessions
        self.id_store = identity_store
        self._key_digest = _digest(key)
        self._clock = clock
        self._fails = {}                       # ip -> [timestamps of failures still in the window]
        self._last_prune = 0.0
        self._lock = threading.Lock()

    # ---- dispatch ----
    def handle(self, h, m, path):
        if path != "/auth/demo":
            return False
        if m == "GET":
            self._respond_page(h, 200)
            return True
        if m == "POST":
            return self._login(h)
        return False

    # ---- pages ----
    def _respond_page(self, h, code, notice=""):
        page = AuthModule._shell(
            "Reviewer access",
            "<h1>Reviewer access</h1>"
            "<p>Enter the access code you were given to open the demo account.</p>"
            + notice +
            "<form method='POST' action='/auth/demo'>"
            "<input type='password' name='code' autocomplete='off' autofocus required "
            "aria-label='Access code' placeholder='Access code' "
            "style='width:100%;font:inherit;padding:10px 12px;margin:0 0 14px;border-radius:10px;"
            "border:1px solid var(--rule);background:var(--bg);color:var(--text)'>"
            "<button class='primary' type='submit'>Continue</button></form>")
        AuthModule._respond(h, code, page, "text/html; charset=utf-8")

    def _deny(self, h):
        self._respond_page(h, 403, "<p>That code was not accepted.</p>")
        return True

    # ---- throttle (in memory, per client IP, pruned as entries age) ----
    def _prune(self, now):
        """Drop aged entries. Runs at most every _PRUNE_EVERY_S; per-IP reads filter for themselves."""
        if now - self._last_prune < _PRUNE_EVERY_S:
            return
        self._last_prune = now
        cutoff = now - WINDOW_S
        for ip in list(self._fails):
            kept = [t for t in self._fails[ip] if t > cutoff]
            if kept:
                self._fails[ip] = kept
            else:
                del self._fails[ip]

    def _recent(self, ip, now):
        cutoff = now - WINDOW_S
        return [t for t in self._fails.get(ip, ()) if t > cutoff]

    def _locked_out(self, ip, now):
        with self._lock:
            self._prune(now)
            return len(self._recent(ip, now)) >= MAX_FAILS

    def _record_fail(self, ip, now):
        """Returns True when this failure is the one that trips the lockout."""
        with self._lock:
            self._prune(now)
            hits = self._recent(ip, now)
            hits.append(now)
            self._fails.pop(ip, None)
            self._fails[ip] = hits             # re-insert so dict order is oldest-activity first
            while len(self._fails) > _MAX_TRACKED:
                del self._fails[next(iter(self._fails))]
            return len(hits) == MAX_FAILS

    # ---- POST /auth/demo ----
    def _read_code(self, h):
        try:
            n = int(h.headers.get("Content-Length", 0) or 0)
        except ValueError:
            return ""
        if n <= 0 or n > _MAX_BODY:
            return ""
        raw = h.rfile.read(n).decode("utf-8", "replace")
        values = parse_qs(raw).get("code")
        return values[0] if values else ""

    def _login(self, h):
        ip = AuthModule._client_ip(h)[:_MAX_IP]
        ua = (h.headers.get("User-Agent", "") or "")[:_MAX_UA]
        now = self._clock()
        code = self._read_code(h)
        if self._locked_out(ip, now):
            # Answered before comparing anything, so a locked IP learns nothing and cannot grow
            # the audit table with further attempts.
            return self._deny(h)
        if not code or not hmac.compare_digest(_digest(code), self._key_digest):
            tripped = self._record_fail(ip, now)
            self.id_store.audit("demo_login_fail", ip=ip,
                                detail=("lockout; ua=" if tripped else "ua=") + ua)
            return self._deny(h)
        with self.store.lock:
            uid, created = self.accounts.resolve_verified_email(DEMO_EMAIL)
            if created:
                self.id_store.audit("account_created", uid=uid, email=DEMO_EMAIL, ip=ip)
        cookie_value, _csrf = self.sessions.mint(uid, DEMO_EMAIL, ip=ip, user_agent=ua)
        self.id_store.audit("demo_login", uid=uid, email=DEMO_EMAIL, ip=ip, detail="ua=" + ua)
        cookies = [self.sessions.set_cookie_header(cookie_value)]
        back = AuthModule._return_to(h)
        if back != "/":
            cookies.append("%s=; Max-Age=0; Path=/; HttpOnly; Secure; SameSite=Lax" % RETURN_COOKIE)
        AuthModule._respond(h, 303, b"", "text/plain", cookies=cookies, location=back)
        return True
