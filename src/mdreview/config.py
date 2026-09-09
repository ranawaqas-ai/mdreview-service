"""Environment-derived configuration, read once at import. No behaviour, just values.

Extracted verbatim from app.py (MR-080). The only change is WEB_DIR's anchor depth: living at
src/mdreview/config.py it walks up three dirs (config.py -> mdreview -> src -> repo root) to reach
/web, where the relocated frontend lives.
"""
import os

# ponytail: repo-root anchor; MDREVIEW_WEB_DIR overrides in container/tests.
# src/mdreview/config.py -> up 3 (config -> mdreview -> src -> repo root), then /web/app.
WEB_DIR = os.environ.get("MDREVIEW_WEB_DIR") or os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "web", "app")
DATA_DIR = os.environ.get("MDREVIEW_DATA", "/data")
PORT = int(os.environ.get("PORT", "8080"))
PUBLIC_BASE = os.environ.get("MDREVIEW_PUBLIC_BASE", "").rstrip("/")

# --- Phase 1 multi-user auth (hosted). OFF by default so local/dev stays open + single-user. ---
# When ON, every request must resolve to a user: browser via a trusted proxy header, agent via a
# per-user Bearer token. Resolution lives behind the injected IdentityProvider (mdreview.access).
REQUIRE_AUTH = os.environ.get("MDREVIEW_REQUIRE_AUTH", "").lower() in ("1", "true", "yes")
# Shared secret proving a request came THROUGH nginx (the cookie/browser plane). nginx sets the
# X-Mdreview-Proxy header to this value; the app trusts the vouched identity header only on a match.
PROXY_SECRET = os.environ.get("MDREVIEW_PROXY_SECRET", "")
# HMAC pepper for API-token digests (env only, never in the data volume), so a users.json leak alone
# cannot forge a token.
TOKEN_PEPPER = os.environ.get("MDREVIEW_TOKEN_PEPPER", "")
# HMAC key for the app-owned session cookie AND the magic-link tokens (#67 D2, native auth). Env
# only, never on the data volume: it signs the browser session and every single-use login token, so
# a leak of the data volume alone cannot forge a session or a magic link. The hosted composition root
# (mdreview.hosted) additionally refuses to boot without it, independent of REQUIRE_AUTH, so the
# fail-closed guarantee is a property of the hosted BUILD, not of this flag (see hosted/compose.py).
SESSION_SECRET = os.environ.get("MDREVIEW_SESSION_SECRET", "")
# The single account crowned owner (=> admin) on the HOSTED build: the user whose VERIFIED email
# equals this, case-insensitively. NEVER the first registrant (#67 H1 — under open membership that
# let a stranger self-crown). Unset => NO account is owner, so nobody can self-crown; the hosted
# composition root additionally REFUSES TO BOOT without it (see hosted/compose.py), so a hosted
# instance is never left unadministrable. The transitional `python -m mdreview` path does not require
# it (owner-ship is unused there today); set it before #102 admin lands, or no account is owner.
OWNER_EMAIL = (os.environ.get("MDREVIEW_OWNER_EMAIL", "") or "").strip().lower()

# Fail CLOSED: hmac.compare_digest("", "") returns True, so an empty PROXY_SECRET would trust any
# client-supplied identity header (impersonation), an empty pepper would make token digests
# forgeable, and an empty session key would make the session cookie / magic-link tokens forgeable.
# If auth is required, refuse to boot rather than run wide open. A non-empty default flag is not a
# non-empty secret. (Deploy note #67: a REQUIRE_AUTH=1 instance now ALSO needs MDREVIEW_SESSION_SECRET
# set before this rolls out, or it refuses to boot; set the secret with the rollout.)
if REQUIRE_AUTH and not (PROXY_SECRET and TOKEN_PEPPER and SESSION_SECRET):
    raise SystemExit(
        "MDREVIEW_REQUIRE_AUTH is on but MDREVIEW_PROXY_SECRET, MDREVIEW_TOKEN_PEPPER, "
        "and/or MDREVIEW_SESSION_SECRET is unset")

# --- Opt-in feature modules. OFF by default: the composition root registers nothing and the
# request path is byte-identical to a build without the module packages. ---
# ENABLE_LATEX wires src/latex_review (Overleaf-style paper review, MR-092+); the latex Docker
# image sets it, the slim image never does.
ENABLE_LATEX = os.environ.get("MDREVIEW_ENABLE_LATEX", "").lower() in ("1", "true", "yes")
# Template download-on-miss (MR-104). ON by default under the latex feature; an air-gapped operator
# sets it off, and build() then registers NO puller (bundled + cache only, zero network). The
# manifest defaults to the shipped registry.json; an operator may point it at their own file.
LATEX_TEMPLATE_DOWNLOAD = os.environ.get("MDREVIEW_LATEX_TEMPLATE_DOWNLOAD", "1").lower() in ("1", "true", "yes")
LATEX_TEMPLATE_REGISTRY = os.environ.get("MDREVIEW_LATEX_TEMPLATE_REGISTRY", "")

# ENABLE_GIT_HISTORY wires src/git_history (#379: a clonable git remote per review, materialized
# lazily from history/round-N/ on first clone request; never touches the write path). Same
# byte-identical-when-off contract as ENABLE_LATEX above.
ENABLE_GIT_HISTORY = os.environ.get("MDREVIEW_ENABLE_GIT_HISTORY", "").lower() in ("1", "true", "yes")
# Per-review bare-repo cache; fully derived from history/round-N/ + the live draft, so it is safe to
# delete at any time (a cold/evicted entry just gets rebuilt on the next clone request).
GIT_CACHE_DIR = os.environ.get("MDREVIEW_GIT_CACHE_DIR", "") or os.path.join(DATA_DIR, ".git-cache")
# Bounds the COLD materialize walk (a review with more historical rounds than this gets a shallow
# git history — only the most recent N rounds are ever committed — instead of a full round-0-to-now
# walk on one request). Once a repo has started building, new rounds append one at a time as they
# arrive; the cap only ever gates the initial cold build (see gitcache.py).
GIT_MATERIALIZE_MAX_ROUNDS = int(os.environ.get("MDREVIEW_GIT_MATERIALIZE_MAX_ROUNDS", "200"))

# DoS backstops (the fine-grained caps live in nginx; these guard the app even on the loopback path).
# MAX_BODY is a generous ceiling above the asset-upload cap; nginx enforces the tight per-route size.
MAX_BODY = int(os.environ.get("MDREVIEW_MAX_BODY", str(32 * 1024 * 1024)))
# Refuse creates/uploads when free space on the data volume drops below this (one rogue/leaked token
# must not be able to fill the shared volume and take every tenant down).
DISK_FLOOR = int(os.environ.get("MDREVIEW_DISK_FLOOR", str(200 * 1024 * 1024)))

os.makedirs(DATA_DIR, exist_ok=True)

# Bounded server-side timeout for the /wait long-poll (seconds); a client ?timeout= is capped to it.
WAIT_TIMEOUT_S = float(os.environ.get("MDREVIEW_WAIT_TIMEOUT_S", "25"))
# Lease staleness TTL in SECONDS (agent_status.at is epoch seconds, float, never milliseconds).
# A foreign /handoff {state:working} lease older than this is taken over (MR-055). MIRRORS the
# viewer's STALE_S in web/viewer.html: single source of truth, the two MUST move together (both seconds).
LEASE_TTL_S = float(os.environ.get("MDREVIEW_LEASE_TTL_S", "180"))
RID = r"([A-Za-z0-9]{4,40})"
