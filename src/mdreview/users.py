"""Users + per-user API tokens (Phase 1 multi-user). One file <DATA_DIR>/users.json holds the
account map (canonical id provider:sub -> record) and hashed API-token digests. Stdlib only.

Auth model: oauth2-proxy enforces the invite allowlist BEFORE any request reaches the app, so a
request on the cookie plane is, by construction, an allowlisted user -> auto-provision on first
sign-in. The token plane is validated here against per-user HMAC digests (pepper from env, never on
disk), so a users.json leak alone cannot forge a token. Reads are lock-free (consistent with the
meta.json reads elsewhere); mutating methods assume the caller holds store.lock, like ReviewService.
"""
import hashlib
import hmac
import json
import os
import secrets
import time


class UserService:
    # #309: trimmed, 1-60 chars, display-only (no uniqueness claim). 60 keeps a name from wrapping
    # the .acct-row-value column (max-width:220px) or the comment-thread .gwho onto a second line at
    # any of the type sizes theme.css defines; it is not a database limit, it is a layout budget.
    MAX_NAME_LEN = 60

    def __init__(self, store, pepper, owner_email=""):
        self.store = store
        self._pepper = (pepper or "").encode()
        # The one email crowned owner (=> admin). Injected from config.OWNER_EMAIL, exactly as the
        # pepper is injected (the core reads no env directly). Empty => no owner (see _is_owner_email).
        self._owner_email = (owner_email or "").strip().lower()

    def _path(self):
        return os.path.join(self.store.data_dir, "users.json")

    def _load(self):
        return self.store.read_json(self._path(), {"users": {}, "tokens": {}})

    def _save(self, data):                       # caller holds store.lock
        self.store.write_text(self._path(), json.dumps(data))

    # ---- identity (cookie plane) ----
    @staticmethod
    def canonical(provider, sub):
        """The ONE user id, used identically by both planes. provider:sub, never sub alone (two
        providers can share a numeric sub). Returns None if either part is missing."""
        provider = (provider or "").strip()
        sub = (sub or "").strip()
        return "%s:%s" % (provider, sub) if provider and sub else None

    def email_for(self, uid):
        """uid -> the email we know for it, or "" when we know none (#262). Deliberately NOT
        best-effort-parsing the uid: magic-link uids happen to be `email:<address>` and would
        decode, but proxy-plane uids are `google:117...` and would yield a lie that looks like an
        address. A caller that gets "" must show the uid honestly, not invent a name."""
        if not uid:
            return ""
        return (self._load().get("users", {}).get(uid) or {}).get("email", "") or ""

    def _is_owner_email(self, email):
        """True IFF `email` equals the configured owner email, case-insensitively. An UNSET owner email
        matches no one, so an instance with no configured owner has no owner and no stranger can
        self-crown. This is the SOLE source of owner-ship: never first-registrant order (#67 H1)."""
        return bool(self._owner_email) and (email or "").strip().lower() == self._owner_email

    def ensure_user(self, uid, email):
        """Auto-provision a user on first sign-in (the proxy plane vetted them; the native plane proved
        inbox control). Idempotent; refreshes the stored email. is_owner is pinned to the configured
        MDREVIEW_OWNER_EMAIL — NEVER the first registrant (#67 H1) — and is RECONCILED on every call, so
        a legacy first-registrant crown is dropped and an owner-email match is honoured.

        is_admin is the EXPLICIT #102 grant (set_admin), owned by the admin surface, and is DELIBERATELY
        NOT reconciled here: the owner's admin-ness is DERIVED (is_admin() returns is_admin OR is_owner),
        so a de-crowned owner keeps no admin (they never held a stored is_admin, only is_owner), while a
        set_admin grant to a non-owner SURVIVES this re-sign-in instead of being clobbered back to the
        owner value. Reconciling is_admin to `owner` here — as the pre-#102 base did — would silently
        drop every non-owner admin grant on their next login, dropping the #102 control. super_read is
        never touched here (off by default; only set_super_read turns it on). Caller holds store.lock."""
        if not uid:
            return None
        data = self._load()
        u = data["users"].get(uid)
        owner = self._is_owner_email(email if u is None else (email or u.get("email")))
        if u is None:
            # is_admin starts False (no explicit #102 grant yet); the owner is admin via is_admin()'s
            # is_owner OR, so a later de-crown leaves no residual admin.
            data["users"][uid] = {"email": email or "", "status": "active",
                                  "is_owner": owner, "is_admin": False, "created": time.time()}
            self._save(data)
            return uid
        dirty = False
        if email and u.get("email") != email:
            u["email"] = email
            dirty = True
        if bool(u.get("is_owner")) != owner:            # H1: owner pinned to MDREVIEW_OWNER_EMAIL
            u["is_owner"] = owner
            dirty = True
        # NB: is_admin is NOT reconciled — it is the explicit #102 grant and must survive re-sign-in.
        if dirty:
            self._save(data)
        return uid

    # ---- display name (#309) ----
    # Absent means unset: ensure_user writes no "name" key, and no migration touches existing
    # records, so name_for() reading "" for a legacy/never-set user is the SAME state as a freshly
    # cleared one — one falsy value, not two. The RENDERER (account.js, viewer.html, the Account
    # page) owns the "" -> email fallback; this stays a plain field read so a caller who wants the
    # raw absent-vs-set fact can have it without the renderer's opinion baked in.
    def name_for(self, uid):
        if not uid:
            return ""
        return (self._load().get("users", {}).get(uid) or {}).get("name", "") or ""

    def set_name(self, uid, name):
        """Set (or CLEAR) uid's display name. `name` is trimmed; a trimmed-empty result clears the
        stored name rather than erroring, so "Skip" and "clear the field" are the same call (#309
        AC4: clearing falls back to email everywhere). Rejects control characters (incl. newlines/
        tabs) outright rather than silently stripping them — a name is single-line, rendered inline
        in a row value and a comment's .gwho, and a caller that thinks it sent "Ann\\nBan" should
        see the rejection, not a silently mangled "AnnBan". Length is checked AFTER trimming, so
        pure leading/trailing whitespace never counts against the 60. Returns "ok" / "invalid" /
        "toolong" / "missing_user"; the route picks the status code, this stays http-agnostic like
        the rest of the class. Caller holds store.lock."""
        data = self._load()
        u = data["users"].get(uid)
        if not u:
            return "missing_user"
        name = (name or "").strip()
        if any(ord(c) < 0x20 or ord(c) == 0x7f for c in name):
            return "invalid"
        if len(name) > self.MAX_NAME_LEN:
            return "toolong"
        if u.get("name", "") != name:
            u["name"] = name
            self._save(data)
        return "ok"

    def is_active(self, uid):
        u = self._load()["users"].get(uid)
        return bool(u) and u.get("status", "active") == "active"

    # ---- platform-admin role + capabilities (#102) ----
    # is_admin / super_read live on the SAME user record is_active gates every plane on, so a ban and
    # a capability check read one source of truth. is_admin is grantable (set_admin); the instance
    # owner (is_owner, the first user provisioned) is admin by construction so there is always at
    # least one admin without hand-editing the data volume. super_read is NEVER implied by admin and
    # NEVER seeded - it is off by default and only an explicit set_super_read turns it on (#102).
    def is_admin(self, uid):
        u = self._load()["users"].get(uid)
        return bool(u) and (bool(u.get("is_admin")) or bool(u.get("is_owner")))

    def can_super_read(self, uid):
        u = self._load()["users"].get(uid)
        return bool(u) and bool(u.get("super_read"))

    def session_ok(self, uid, iat):
        """Cookie-plane gate: active AND the session was minted at/after any admin session-revoke
        cutoff. `sessions_invalid_before` (0 by default) is bumped by revoke_sessions so a force-logout
        rejects every session issued before it WITHOUT banning the account (a distinct lever). A stale
        agent token is revoked by deleting it, not by this cutoff, so tokens are unaffected."""
        u = self._load()["users"].get(uid)
        if not u or u.get("status", "active") != "active":
            return False
        cutoff = u.get("sessions_invalid_before", 0) or 0
        return not cutoff or (iat or 0) >= cutoff

    def list_users(self):
        """Every account, for the admin surface. is_admin folds in the owner (matching is_admin())."""
        return sorted(
            [{"uid": uid, "email": r.get("email", ""), "status": r.get("status", "active"),
              "is_owner": bool(r.get("is_owner")),
              "is_admin": bool(r.get("is_admin")) or bool(r.get("is_owner")),
              "super_read": bool(r.get("super_read")), "created": r.get("created", 0)}
             for uid, r in self._load()["users"].items()],
            key=lambda u: u["created"], reverse=True)

    def set_status(self, uid, status):
        """Ban (status='banned') or reinstate (status='active') an account. A banned account fails
        is_active, so its sessions AND tokens are rejected at the next request. Caller holds store.lock;
        returns True iff the user exists."""
        data = self._load()
        u = data["users"].get(uid)
        if not u:
            return False
        u["status"] = status
        self._save(data)
        return True

    def set_admin(self, uid, value):
        """Grant/revoke the admin role. The owner's implicit admin (is_owner) is not stored here and
        cannot be revoked this way, which keeps at least one admin. Caller holds store.lock."""
        data = self._load()
        u = data["users"].get(uid)
        if not u:
            return False
        u["is_admin"] = bool(value)
        self._save(data)
        return True

    def set_super_read(self, uid, value):
        """Grant/revoke the audited document super-READ capability (#102). Off by default; this is the
        ONLY way it is turned on. Caller holds store.lock; returns True iff the user exists."""
        data = self._load()
        u = data["users"].get(uid)
        if not u:
            return False
        u["super_read"] = bool(value)
        self._save(data)
        return True

    def revoke_all_tokens(self, uid):
        """Delete every API token belonging to uid (moderation / lost-device). Caller holds store.lock;
        returns the count removed."""
        data = self._load()
        victims = [tid for tid, r in data["tokens"].items() if r.get("uid") == uid]
        for tid in victims:
            del data["tokens"][tid]
        if victims:
            self._save(data)
        return len(victims)

    def revoke_sessions(self, uid):
        """Force-logout: invalidate every session minted before now (see session_ok). Distinct from a
        ban (the account stays active) and from token revoke (tokens are unaffected). Caller holds
        store.lock; returns True iff the user exists."""
        data = self._load()
        u = data["users"].get(uid)
        if not u:
            return False
        u["sessions_invalid_before"] = time.time()
        self._save(data)
        return True

    def find_by_email(self, email):
        """The existing account id whose stored email matches (case-insensitive), or None. Read-only.
        Used by the hosted native-auth linking (#67 D1) to ADOPT an existing federated owner's durable
        provider:sub when they first verify the same email via magic-link, so their reviews (keyed on
        that sub) do not fragment. A no-op for anyone who never uses the native path."""
        e = (email or "").strip().lower()
        if not e:
            return None
        for uid, rec in self._load()["users"].items():
            if (rec.get("email") or "").strip().lower() == e:
                return uid
        return None

    # ---- tokens (agent plane) ----
    def _digest(self, secret):
        return hmac.new(self._pepper, secret.encode(), hashlib.sha256).hexdigest()

    def mint_token(self, uid, label="", ttl_s=None):
        """Return the plaintext token (shown ONCE). Store only its HMAC digest. Caller holds
        store.lock. Format: mdr_<tok_id>_<secret> (tok_id is the public handle for O(1) lookup +
        revoke; the secret is never stored). ttl_s gives the token an expiry (OAuth access tokens,
        #395); without it the token lives until revoked, as every token minted on /account does."""
        tok_id = secrets.token_hex(4)
        secret = secrets.token_urlsafe(32)
        now = time.time()
        data = self._load()
        # Expired rows can never resolve again; dropping them here keeps the file bounded.
        data["tokens"] = {k: r for k, r in data["tokens"].items() if not self._expired(r, now)}
        rec = {"uid": uid, "hash": self._digest(secret), "label": label or "", "created": now}
        if ttl_s:
            rec["expires"] = now + ttl_s
        data["tokens"][tok_id] = rec
        self._save(data)
        return "mdr_%s_%s" % (tok_id, secret)

    @staticmethod
    def token_id(token):
        """The public tok_id of a plaintext mdr_ token, or None."""
        parts = (token or "").split("_", 2)
        return parts[1] if len(parts) == 3 and parts[0] == "mdr" else None

    @staticmethod
    def _expired(rec, now):
        return bool(rec.get("expires")) and rec["expires"] <= now

    def resolve(self, authorization):
        """Authorization header value -> uid or None. Constant-time digest compare; unknown/forged
        tokens resolve to None (the caller turns that into 401)."""
        if not authorization or not authorization.startswith("Bearer "):
            return None
        parts = authorization[7:].strip().split("_", 2)     # ["mdr", tok_id, secret]
        if len(parts) != 3 or parts[0] != "mdr":
            return None
        rec = self._load()["tokens"].get(parts[1])
        if not rec or not hmac.compare_digest(rec.get("hash", ""), self._digest(parts[2])):
            return None
        if self._expired(rec, time.time()):
            return None
        return rec.get("uid")

    def list_tokens(self, uid):
        toks = self._load()["tokens"]
        return sorted(
            [{"tok_id": tid, "label": r.get("label", ""), "created": r.get("created", 0),
              "expires": r.get("expires")}
             for tid, r in toks.items() if r.get("uid") == uid and not self._expired(r, time.time())],
            key=lambda t: t["created"], reverse=True)

    def revoke_token(self, uid, tok_id):
        """Remove a token IFF it belongs to uid (so one user cannot revoke another's). Caller holds
        store.lock. Returns True if a token was removed."""
        data = self._load()
        r = data["tokens"].get(tok_id)
        if not r or r.get("uid") != uid:
            return False
        del data["tokens"][tok_id]
        self._save(data)
        return True
