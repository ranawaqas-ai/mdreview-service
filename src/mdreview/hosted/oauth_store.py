"""OAuth authorization-server state on `sqlite3` (#395): registered clients, pending consents,
authorization codes and refresh tokens. Storage only; the protocol rules live in oauth.OAuthModule.

One file, <DATA_DIR>/oauth.db, kept apart from identity.db so the identity schema is untouched.
Codes and refresh tokens are stored as SHA-256 digests (they are high-entropy random values, so a
plain hash is enough; a leaked db file yields nothing redeemable). Single use is enforced by a
DELETE whose rowcount decides the winner, so two concurrent redemptions cannot both succeed.

Same threading rule as IdentityStore: a fresh short-lived connection per call.
"""
import hashlib
import json
import os
import secrets
import sqlite3
import time

UNUSED_CLIENT_TTL_S = 3600      # a registration never used for a grant is pruned after this
MAX_UNUSED_CLIENTS = 1000       # past this, the oldest unused registration is evicted


def digest(value):
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


class OAuthStore:
    def __init__(self, db_path):
        self.db_path = db_path
        os.makedirs(os.path.dirname(db_path) or ".", exist_ok=True)
        with self._connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS clients (
                    client_id     TEXT PRIMARY KEY,
                    client_name   TEXT NOT NULL,
                    redirect_uris TEXT NOT NULL,
                    created       REAL NOT NULL,
                    used          INTEGER NOT NULL DEFAULT 0
                );
                CREATE TABLE IF NOT EXISTS pending (
                    nonce        TEXT PRIMARY KEY,
                    uid          TEXT NOT NULL,
                    client_id    TEXT NOT NULL,
                    redirect_uri TEXT NOT NULL,
                    challenge    TEXT NOT NULL,
                    state        TEXT NOT NULL,
                    resource     TEXT NOT NULL,
                    expires      REAL NOT NULL
                );
                CREATE TABLE IF NOT EXISTS codes (
                    code_hash    TEXT PRIMARY KEY,
                    uid          TEXT NOT NULL,
                    client_id    TEXT NOT NULL,
                    redirect_uri TEXT NOT NULL,
                    challenge    TEXT NOT NULL,
                    resource     TEXT NOT NULL,
                    expires      REAL NOT NULL
                );
                CREATE TABLE IF NOT EXISTS refresh (
                    token_hash TEXT PRIMARY KEY,
                    uid        TEXT NOT NULL,
                    client_id  TEXT NOT NULL,
                    tok_id     TEXT NOT NULL,
                    resource   TEXT NOT NULL,
                    expires    REAL NOT NULL
                );
                """
            )

    def _connect(self):
        conn = sqlite3.connect(self.db_path, timeout=10)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=5000")
        return conn

    # ---- clients (dynamic registration) ----
    def register_client(self, client_name, redirect_uris):
        """A new client_id. Past the cap, the OLDEST unused registration is evicted rather than the
        new one refused, so a registration flood cannot lock real connectors out; a client that has
        completed a grant (used=1) is never evicted."""
        now = time.time()
        with self._connect() as conn:
            conn.execute("DELETE FROM clients WHERE used=0 AND created < ?", (now - UNUSED_CLIENT_TTL_S,))
            conn.execute("DELETE FROM clients WHERE client_id IN (SELECT client_id FROM clients "
                         "WHERE used=0 ORDER BY created DESC LIMIT -1 OFFSET ?)",
                         (MAX_UNUSED_CLIENTS - 1,))
            client_id = "mdrc_" + secrets.token_urlsafe(16)
            conn.execute("INSERT INTO clients (client_id, client_name, redirect_uris, created) "
                         "VALUES (?, ?, ?, ?)", (client_id, client_name, json.dumps(redirect_uris), now))
        return client_id

    def get_client(self, client_id):
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM clients WHERE client_id=?", (client_id,)).fetchone()
        if not row:
            return None
        client = dict(row)
        client["redirect_uris"] = json.loads(client["redirect_uris"])
        return client

    def mark_used(self, client_id):
        with self._connect() as conn:
            conn.execute("UPDATE clients SET used=1 WHERE client_id=?", (client_id,))

    # ---- pending consents (the authorize GET -> POST handoff) ----
    def put_pending(self, uid, client_id, redirect_uri, challenge, state, resource, ttl_s):
        nonce = secrets.token_urlsafe(24)
        now = time.time()
        with self._connect() as conn:
            conn.execute("DELETE FROM pending WHERE expires < ?", (now,))
            conn.execute("INSERT INTO pending VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                         (nonce, uid, client_id, redirect_uri, challenge, state, resource, now + ttl_s))
        return nonce

    def take_pending(self, nonce):
        """Claim a pending consent exactly once; None if unknown, expired or already taken."""
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM pending WHERE nonce=? AND expires >= ?",
                               (nonce, time.time())).fetchone()
            if not row or conn.execute("DELETE FROM pending WHERE nonce=?", (nonce,)).rowcount != 1:
                return None
        return dict(row)

    # ---- authorization codes ----
    def put_code(self, uid, client_id, redirect_uri, challenge, resource, ttl_s):
        code = secrets.token_urlsafe(32)
        now = time.time()
        with self._connect() as conn:
            conn.execute("DELETE FROM codes WHERE expires < ?", (now,))
            conn.execute("INSERT INTO codes VALUES (?, ?, ?, ?, ?, ?, ?)",
                         (digest(code), uid, client_id, redirect_uri, challenge, resource, now + ttl_s))
        return code

    def take_code(self, code):
        """Redeem a code exactly once; None if unknown, expired or already redeemed."""
        h = digest(code)
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM codes WHERE code_hash=? AND expires >= ?",
                               (h, time.time())).fetchone()
            if not row or conn.execute("DELETE FROM codes WHERE code_hash=?", (h,)).rowcount != 1:
                return None
        return dict(row)

    # ---- refresh tokens ----
    def put_refresh(self, uid, client_id, tok_id, resource, ttl_s):
        token = "mdrr_" + secrets.token_urlsafe(32)
        now = time.time()
        with self._connect() as conn:
            conn.execute("DELETE FROM refresh WHERE expires < ?", (now,))
            conn.execute("INSERT INTO refresh VALUES (?, ?, ?, ?, ?, ?)",
                         (digest(token), uid, client_id, tok_id, resource, now + ttl_s))
        return token

    def take_refresh(self, token):
        """Redeem a refresh token exactly once (rotation); None if unknown, expired or spent."""
        h = digest(token)
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM refresh WHERE token_hash=? AND expires >= ?",
                               (h, time.time())).fetchone()
            if not row or conn.execute("DELETE FROM refresh WHERE token_hash=?", (h,)).rowcount != 1:
                return None
        return dict(row)
