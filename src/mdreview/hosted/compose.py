"""The hosted composition root (#67 priority 7 / custody Decision 1 — the fail-closed-BUILD core of
#109).

`build_hosted` is reached ONLY by `python -m mdreview.hosted` (the hosted Docker image's CMD). The
selection of the real owner-only policy is therefore a property of the BUILD, not of a runtime flag:
there is no env var in this path that yields OpenPolicy. A hosted build is consequently INCAPABLE of
serving with the ownership check off — the #97 root fault (MDREVIEW_REQUIRE_AUTH defaulting open
behind a secret-only guard) is not a reachable state here. This deliberately does NOT reintroduce a
"hosted defaults off" flag; the worst case for a misconfigured hosted build is refuse-to-boot, never
serve-open. The LOCAL tier (`python -m mdreview`) is untouched: it keeps OperatorIdentity/OpenPolicy
and its open, no-accounts promise by design.

The boot guards here are unconditional (independent of REQUIRE_AUTH): a hosted build refuses to start
without the session secret (and the token pepper; and the proxy secret while the transitional plane
is enabled); without MDREVIEW_OWNER_EMAIL (#67 H1 — so ownership is pinned to a configured email and
no stranger self-crowns); and without a resolvable email backend (#67 H2 — a real sender, or the dev
stub only behind an explicit MDREVIEW_ALLOW_STUB_EMAIL opt-in that logs a loud warning). config.py
additionally requires the session secret whenever REQUIRE_AUTH is on, so the existing transitional
path is covered too.
"""
import os
import sys
from urllib.parse import urlparse

from mdreview import config
from mdreview.server import Services
from mdreview.hosted.adminroutes import AdminModule
from mdreview.hosted.authroutes import AuthModule
from mdreview.hosted.custody import CustodyPolicy
from mdreview.hosted.identity import AccountService, HostedIdentity
from mdreview.hosted.identity_store import IdentityStore
from mdreview.hosted.oauth import OAuthModule
from mdreview.hosted.oauth_store import OAuthStore
from mdreview.hosted.magiclink import MagicLinkService, SmtpEmailSender, StubEmailSender
from mdreview.hosted.sessions import SessionService
from mdreview.hosted.shares import ShareStore
from mdreview.hosted.sharing import SharingModule


def _env_int(name, default):
    try:
        return int(os.environ.get(name, "") or default)
    except ValueError:
        return default


def _truthy(value):
    return (value or "").strip().lower() in ("1", "true", "yes")


def _warn_stub_email():
    """A LOUD startup banner (stderr): the stub writes magic-link SIGN-IN TOKENS to the logs in
    plaintext, so anyone who can read the logs can complete a login. Dev / bring-up ONLY."""
    bar = "!" * 74
    for line in ("", bar,
                 "WARNING  MDREVIEW_ALLOW_STUB_EMAIL is set: using the STUB email backend.",
                 "WARNING  Magic-link sign-in TOKENS are written to the LOGS in plaintext.",
                 "WARNING  Anyone who can read the logs can complete a login. DEV / BRING-UP ONLY.",
                 "WARNING  Set MDREVIEW_SMTP_HOST (real delivery) before any production use.",
                 bar, ""):
        print(line, file=sys.stderr, flush=True)


def select_email_sender(env=None):
    """Choose the magic-link EmailSender from the environment (#67 H2 — the stub must NEVER be the
    silent default that leaks login tokens to logs). Fixed order:
      1. MDREVIEW_SMTP_HOST set          -> a real SmtpEmailSender (the production delivery path).
      2. else MDREVIEW_ALLOW_STUB_EMAIL  -> the StubEmailSender, behind a LOUD startup warning (dev).
      3. else                            -> REFUSE TO BOOT: no real sender and no explicit stub opt-in
                                            must never silently print sign-in tokens to the logs.
    Pure + env-injectable so the boot decision is unit-testable without a subprocess."""
    env = os.environ if env is None else env
    host = (env.get("MDREVIEW_SMTP_HOST") or "").strip()
    if host:
        from_addr = (env.get("MDREVIEW_SMTP_FROM") or env.get("MDREVIEW_SMTP_USER") or "").strip()
        if not from_addr:
            raise SystemExit("hosted SMTP email requires MDREVIEW_SMTP_FROM (or MDREVIEW_SMTP_USER)")
        try:
            port = int((env.get("MDREVIEW_SMTP_PORT") or "587").strip())
        except ValueError:
            raise SystemExit("MDREVIEW_SMTP_PORT must be an integer")
        return SmtpEmailSender(host, port, env.get("MDREVIEW_SMTP_USER", ""),
                               env.get("MDREVIEW_SMTP_PASSWORD", ""), from_addr,
                               use_starttls=_truthy(env.get("MDREVIEW_SMTP_STARTTLS", "1")))
    if _truthy(env.get("MDREVIEW_ALLOW_STUB_EMAIL")):
        _warn_stub_email()
        return StubEmailSender()
    raise SystemExit(
        "hosted build has no email sender configured: set MDREVIEW_SMTP_HOST (+ MDREVIEW_SMTP_FROM) "
        "for real magic-link delivery, or set MDREVIEW_ALLOW_STUB_EMAIL=1 to use the dev stub that "
        "writes sign-in tokens to the logs. Refusing to boot rather than silently leak login tokens.")


def canonical_base():
    """The VERIFIED base the magic-link is built from — NOT the raw MDREVIEW_PUBLIC_BASE echoed into
    a link unchecked, and never the client-supplied Host header. Must be an absolute https:// URL with
    a host; otherwise the hosted build refuses to boot (a bogus base would send login links to the
    wrong, possibly attacker-influenced, origin)."""
    base = (config.PUBLIC_BASE or "").rstrip("/")
    parsed = urlparse(base)
    if parsed.scheme != "https" or not parsed.netloc:
        raise SystemExit(
            "hosted build requires MDREVIEW_PUBLIC_BASE to be an absolute https:// URL "
            "(got %r); magic-link URLs are built from it" % base)
    return base


def build_hosted(store):
    # --- unconditional fail-closed boot guards (properties of the hosted build) ---
    if not config.SESSION_SECRET:
        raise SystemExit("hosted build requires MDREVIEW_SESSION_SECRET (session + magic-link signing)")
    if not config.TOKEN_PEPPER:
        raise SystemExit("hosted build requires MDREVIEW_TOKEN_PEPPER (agent-token digests)")
    if not config.OWNER_EMAIL:
        raise SystemExit("hosted build requires MDREVIEW_OWNER_EMAIL (the verified email crowned "
                         "owner+admin). Without it no account is owner and, under open membership, a "
                         "stranger could otherwise self-crown (#67 H1).")
    allow_proxy_plane = _truthy(os.environ.get("MDREVIEW_ALLOW_PROXY_PLANE", "1"))
    if allow_proxy_plane and not config.PROXY_SECRET:
        raise SystemExit("hosted build with the transitional proxy plane on requires MDREVIEW_PROXY_SECRET "
                         "(or set MDREVIEW_ALLOW_PROXY_PLANE=0 to retire it)")
    link_base = canonical_base()
    # H2: SELECT the email backend from config (refuses to boot if no real sender and no stub opt-in);
    # NEVER hard-wire the stub, which would print magic-link tokens to the logs on a prod build.
    email_sender = select_email_sender()

    # --- core services (reviews/comments/assets/handoff/users/modules), then OVERRIDE the seam ---
    app = Services(store)

    id_store = IdentityStore(os.path.join(config.DATA_DIR, "identity.db"))
    sessions = SessionService(
        config.SESSION_SECRET,
        ttl_s=_env_int("MDREVIEW_SESSION_TTL_S", 43200),
        # #223: wiring the identity store is what makes a session individually revocable. Without
        # it SessionService stays pure-crypto and every cookie is unrevocable-but-valid.
        records=id_store,
        secure=True)
    magic = MagicLinkService(
        config.SESSION_SECRET, id_store, email_sender, link_base,
        ttl_s=_env_int("MDREVIEW_MAGICLINK_TTL_S", 900),
        max_per_address=_env_int("MDREVIEW_MAGICLINK_MAX_PER_ADDRESS", 3),
        max_per_ip=_env_int("MDREVIEW_MAGICLINK_MAX_PER_IP", 10),
        global_daily_budget=_env_int("MDREVIEW_MAGICLINK_DAILY_BUDGET", 500))
    accounts = AccountService(app.users, id_store)
    shares = ShareStore(os.path.join(config.DATA_DIR, "shares.db"))
    app.shares = shares                         # reached by the delete-review cleanup + SharingModule

    # The authoritative, UNCONDITIONAL selection: the hosted resolver + the ONE composed CustodyPolicy
    # carrying all three Confinement arms. CustodyPolicy IS the custody Confinement contract —
    # OWNER-ONLY for every write/delete (owner isolation is NOT weakened by either extension) —
    # EXTENDED with (a) the owner-granted share exception the invariant names: a public share grants
    # view to anyone incl. anonymous (#101), a named share grants view|view+comment to a grantee (#68);
    # and (b) the audited, off-by-default, cookie-plane admin super-READ exception (#102). Bound to the
    # FINAL app.reviews (the latex wrapper when enabled); shares consulted via the injected ShareStore.
    app.identity = HostedIdentity(app.users, store, sessions, config.PROXY_SECRET, allow_proxy_plane)
    # #250: the latex module's POST /recompile needs the CSRF check on the cookie plane. Exposed on
    # the app (like app.shares) and read by LatexModule via getattr at request time, so latex_review
    # keeps zero hosted imports and the plain local tier (no app.sessions) skips the gate — it has
    # no cookie plane to protect.
    app.sessions = sessions

    # #289: the session-keyed CSRF predicate for core write arms (PUT /source), bound HERE so core
    # never imports mdreview.hosted (the arm reaches it via getattr; absent on the local tier =
    # no gate, correctly — there is no cookie plane to protect). The shape is SharingModule._owner
    # (mutating=True) / the #250 recompile gate: read the cookie, VERIFY the session, and only then
    # demand X-CSRF-Token. A caller with no verified session (proxy-vouched header, bearer token)
    # passes untouched — plane == "cookie" is NOT the predicate "a session was verified"; it also
    # covers proxy-vouched and anonymous callers (epic #273 round-1 finding 1). Returns True to
    # proceed; the caller writes the 403.
    def check_csrf(h):
        cookie = SessionService.read_cookie(h)
        sess = sessions.verify(cookie) if cookie else None
        if sess and not sessions.check_csrf(sess, h.headers.get("X-CSRF-Token", "")):
            return False
        return True
    app.check_csrf = check_csrf
    app.policy = CustodyPolicy(app.reviews, shares)

    # Feature modules run (in order) BEFORE the core review arms and each owns its own auth. Their
    # prefixes are disjoint (/auth/*, /admin/*, and the sharing /api/reviews/{rid}/public|shares owner
    # routes), so relative order is immaterial. SharingModule and AdminModule are BOTH wired — sharing
    # widens read/comment via owner-granted shares, admin adds the audited super-read + user-mgmt.
    app.modules.append(AuthModule(store, app.users, sessions, magic, accounts, id_store))
    app.modules.append(AdminModule(store, app.users, id_store, sessions))
    app.modules.append(SharingModule(app.reviews, shares, sessions, app.users))
    # #395: the OAuth server a remote MCP client (claude.ai connector) signs in through. Hosted only;
    # the local tier is authless, so its /mcp never answers 401.
    app.modules.append(OAuthModule(link_base, store, app.users,
                                   OAuthStore(os.path.join(config.DATA_DIR, "oauth.db")), id_store))
    return app
