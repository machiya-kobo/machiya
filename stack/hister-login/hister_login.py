"""hister-login (machiya-kobo/machiya stack/hister-login): Hister's users as the one sign-in for every Machiya room.

Hister keeps its session cookie (`hister`) on its own host. This helper sits on that host too (Tailscale Serve sends
it /machiya/… and /api/oauth/callback; everything else goes to Hister) and turns a Hister session into an opaque id,
its own host-only cookie `__Host-machiya_sso=mhs_…` (0.3.0; before, `machiya_sso` on the tailnet's shared domain,
which HISTER_LOGIN_LEGACY still sets during the move). Each room gets a cookie of its own: the room sends the browser
here with a state, the helper sends it back to the room's /machiya/callback with a one-time code (60 s, one use,
bound to that room's origin and the browser's state), and the room trades it at POST /v1/redeem for a room session
(`mhr_…`) that only that room accepts. The rooms ask about a credential over the internal network (/v1/check, naming
themselves in X-Machiya-Room); the helper asks Hister's GET /api/profile and caches the answer for 30 s. Signing out
anywhere ends the Hister session, every id on it and every room session made from them. Headless callers get room
tokens (`mht_…`, scoped to rooms; the sessions page or `token mint`) instead of Hister's raw token. Hister itself is
not patched.

Two ports:
  public   (HISTER_LOGIN_PORT, 8080): GET /machiya/signin, GET /api/oauth/callback (a pass-through shim),
           POST /machiya/signout, GET/POST /machiya/sessions (sessions, the rooms each opened, room tokens),
           POST /machiya/api/app-session, GET /machiya/healthz
           (the probe's: 200 while the helper and its state work, with Hister's state as "hister"; 503 only when the
           helper's own state fails), GET/PUT /machiya/api/prefs (0.2.0: the account's settings, for Shiori's apps,
           extensions and hosted pages), /machiya/static/…; on a HISTER_LOGIN_PROXIED_ORIGINS host (the hosted pages,
           through their nginx with their Host) only GET /machiya/start, /machiya/callback, /machiya/signed-out,
           /machiya/api/prefs, /machiya/static/… and POST /machiya/signout, as that host's room
  internal (HISTER_LOGIN_INTERNAL_PORT, 8081; never routed by Serve): GET /v1/check, POST /v1/redeem (0.3.0),
           POST /v1/signout,
           GET /v1/nginx (nginx auth_request), GET /healthz (the rooms': 503 unless the helper AND Hister are fine),
           GET/PUT /v1/prefs (0.2.0: a room's /api/prefs, forwarded with the caller's own credential)

State: one SQLite file (HISTER_LOGIN_DB). An id is stored only as its SHA-256; the Hister session it maps to is
stored raw (the helper must present it to Hister). The settings that follow each Hister user are a second file
(HISTER_LOGIN_PREFS_DB, prefs.sqlite3 beside it; docs/contracts/prefs.md), keyed by hi:<sha256(username)>, so the
sessions file stays as small and as sensitive as it is. Standard library only, plus the vendored vaultkit.

    python3 hister_login.py                                   serve
    python3 hister_login.py prefs import --user NAME FILE…    seed NAME's settings from the rooms' old prefs files
    python3 hister_login.py prefs show --user NAME            what NAME's account holds
    python3 hister_login.py prefs delete --user NAME          forget NAME's settings (an account removed)
    python3 hister_login.py token mint --user NAME --label L --rooms kura,konbini [--days N] [--out FILE]
    python3 hister_login.py token add --user NAME --label L --rooms … --from-file FILE    (a value made elsewhere)
    python3 hister_login.py token list | token revoke ID      room tokens for headless callers (0.3.0)
"""
import argparse
import base64
import hashlib
import html
import http.client
import http.server
import json
import os
import re
import secrets
import signal
import socketserver
import sqlite3
import sys
import threading
import time
from urllib.parse import parse_qs, quote, urlencode, urlsplit

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

from vaultkit import histerauth, prefs as vprefs, shell, signin as vsignin   # noqa: E402

VERSION = "0.4.2"
SID_PREFIX = histerauth.SID_PREFIX
SID_RE = histerauth.SID_RE
HISTER_SESSION_RE = re.compile(r"[A-Za-z0-9_-]{43}\Z")        # Hister's: 32 random bytes, base64url
SSO, RETURN_COOKIE, HISTER_COOKIE = histerauth.SSO_COOKIE, "machiya_return", "hister"    # SSO: the default name
COOKIE_NAME_RE = re.compile(r"[A-Za-z0-9_-]{1,60}\Z")
VERIFY_TTL = 30                 # an id checked with Hister this recently is answered from the row
TOKEN_TTL_OK, TOKEN_TTL_OUT = 30, 5
SESSION_DAYS, CAP_DAYS = 30, 180
OUT_MAX_AGE = histerauth.OUT_MAX_AGE    # the signed-out marker (<sign-in cookie>_out): until the next sign-in
FAILED_MAX_AGE = 600                    # after a failed automatic round trip: the page, not another try
HEALTH_TTL = 10
HISTER_TIMEOUT = 2.0
MAX_BODY = 4096
RETURN_MAX_AGE = 600
LOG = lambda *a: print(*a, file=sys.stderr, flush=True)        # noqa: E731


def now():
    return int(time.time())


def sha(value):
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def b64e(raw):
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


def b64d(text):
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


# -- settings --------------------------------------------------------------------------------------------------------

class Settings:
    def __init__(self, env=None):
        env = os.environ if env is None else env
        self.hister_url = (env.get("HISTER_LOGIN_HISTER_URL") or "http://hister:4433").strip().rstrip("/")
        self.public_url = (env.get("HISTER_LOGIN_PUBLIC_URL") or "").strip().rstrip("/")
        if not re.fullmatch(r"https?://[A-Za-z0-9.-]+(:[0-9]+)?", self.public_url):
            raise SystemExit("hister-login: HISTER_LOGIN_PUBLIC_URL must be Hister's public address, "
                             "https://hister.example.ts.net (no path)")
        self.secure = self.public_url.startswith("https://")
        # MACHIYA_SSO_COOKIE: the sign-in cookie's name (vaultkit.histerauth reads the same setting in the rooms)
        self.sso = (env.get("MACHIYA_SSO_COOKIE") or "").strip() or SSO
        if not COOKIE_NAME_RE.match(self.sso):
            raise SystemExit("hister-login: MACHIYA_SSO_COOKIE must be a cookie name (letters, digits, _ and -)")
        # 0.3.0: the helper's own cookies are host-only (__Host- over https): its session never leaves Hister's host
        self.prefix = histerauth.HOST_PREFIX if self.secure else ""
        self.own = self.prefix + self.sso               # the helper's browser session (__Host-machiya_sso)
        self.out = self.prefix + self.sso + "_out"      # the signed-out marker: no automatic sign-in
        self.legacy_out = self.sso + "_out"             # (0.2.x) the marker on the shared domain
        self.return_cookie = self.prefix + RETURN_COOKIE
        self.origin = histerauth.origin_of(self.public_url)
        raw = env.get("HISTER_LOGIN_LEGACY")
        legacy = {"domain-cookie", "hister-token"} if raw is None else \
            {x.strip().lower() for x in raw.split(",") if x.strip()} - {"none"}
        if legacy - {"domain-cookie", "hister-token"}:
            raise SystemExit("hister-login: HISTER_LOGIN_LEGACY is a list of domain-cookie and hister-token, or none")
        self.legacy = legacy
        self.proxied = []                               # Shiori's hosted pages: their nginx sends /machiya/… here
        for o in (env.get("HISTER_LOGIN_PROXIED_ORIGINS") or "").split(","):
            if o.strip():
                norm = histerauth.origin_of(o.strip())
                if not norm or urlsplit(o.strip()).path not in ("", "/") or norm == self.origin:
                    raise SystemExit("hister-login: HISTER_LOGIN_PROXIED_ORIGINS is a list of https://host[:port], "
                                     "not %r" % o)
                self.proxied.append(norm)
        self.proxied_cookie = self.prefix + self.sso + "_shiori"   # the hosted pages' room cookie, on their hosts
        provider = (env.get("MACHIYA_SIGNIN_PROVIDER") or "").strip().lower()
        self.auto_provider = provider if histerauth.PROVIDER_RE.match(provider or "-") else ""
        u = urlsplit(self.public_url)
        own = u.hostname + ("" if u.port in (None, 443) else ":%d" % u.port)
        hosts = env.get("HISTER_LOGIN_RETURN_HOSTS")
        if hosts is None or not hosts.strip():          # default: the https rooms in MACHIYA_ROOMS, minus searxng
            hosts = []
            for key, url in shell.rooms(env).items():
                p = urlsplit(url)
                if key != "searxng" and p.scheme == "https" and p.hostname:
                    hosts.append(p.hostname + ("" if p.port in (None, 443) else ":%d" % p.port))
        else:
            hosts = [h.strip() for h in hosts.split(",") if h.strip()]
        for o in self.proxied:
            p = urlsplit(o)
            hosts.append(p.hostname + ("" if p.port in (None, 443) else ":%d" % p.port))
        self.return_hosts = sorted(set(hosts) | {own})
        # the rooms by name (token scopes: "kura" -> its origin), from MACHIYA_ROOMS (machiya = landing) and
        # HISTER_LOGIN_TOKEN_SERVICES (machiya-mcp, smallweb: services with a gate of their own that take room tokens)
        self.rooms = {k: o for k, o in token_rooms(env).items() if o != self.origin}
        self.app_schemes = [s.strip().lower() for s in (env.get("HISTER_LOGIN_APP_SCHEMES") or "shiori").split(",")
                            if s.strip()]
        self.cookie_domain = (env.get("MACHIYA_COOKIE_DOMAIN") or "").strip().lstrip(".")
        if "domain-cookie" in self.legacy and not self.cookie_domain:
            LOG("hister-login: MACHIYA_COOKIE_DOMAIN is unset: the legacy machiya_sso stays on this host")
        self.db = (env.get("HISTER_LOGIN_DB") or "/data/hister-login.sqlite3").strip()
        self.prefs_db = prefs_path(env)
        self.bind = (env.get("HISTER_LOGIN_BIND") or "0.0.0.0").strip()
        self.port = int(env.get("HISTER_LOGIN_PORT") or 8080)
        self.internal_port = int(env.get("HISTER_LOGIN_INTERNAL_PORT") or 8081)
        self.providers = [p.strip().lower() for p in (env.get("HISTER_LOGIN_PROVIDERS") or "").split(",")
                          if p.strip()]
        self.oidc_label = (env.get("HISTER_LOGIN_OIDC_LABEL") or "Tailscale").strip()


def token_rooms(env):
    """{name: origin} a room token may be scoped to: MACHIYA_ROOMS (minus hister, searxng and shiori: Shiori is a
    client) and HISTER_LOGIN_TOKEN_SERVICES (name=url, comma-separated: machiya-mcp, smallweb)."""
    out = {}
    for key, url in shell.rooms(env).items():
        o = histerauth.origin_of(url)
        if o and key not in ("hister", "searxng", "shiori"):
            out[key] = o
    for item in (env.get("HISTER_LOGIN_TOKEN_SERVICES") or "").split(","):
        key, _, url = item.strip().partition("=")
        o = histerauth.origin_of(url.strip())
        if key.strip() and o:
            out[key.strip()] = o
    return out


def prefs_path(env):
    """HISTER_LOGIN_PREFS_DB, else prefs.sqlite3 beside HISTER_LOGIN_DB."""
    db = (env.get("HISTER_LOGIN_DB") or "/data/hister-login.sqlite3").strip()
    return (env.get("HISTER_LOGIN_PREFS_DB") or "").strip() or os.path.join(os.path.dirname(os.path.abspath(db)),
                                                                            "prefs.sqlite3")


def user_key(username):
    """The account's key in the prefs file: hi:<sha256(Hister username)[:32]>, the rooms' own form (histerauth's
    uid_for without a fallback login), so Kura's old rows carry over as they are."""
    return "hi:" + hashlib.sha256(username.encode("utf-8")).hexdigest()[:32]


# -- Hister ----------------------------------------------------------------------------------------------------------

class Hister:
    """The few calls the helper makes. Every one has a 2 s timeout; OSError means "no answer"."""

    def __init__(self, base, timeout=HISTER_TIMEOUT):
        u = urlsplit(base)
        self.host, self.port, self.timeout = u.hostname, u.port, timeout
        self.cls = http.client.HTTPSConnection if u.scheme == "https" else http.client.HTTPConnection
        self.health_at, self.health_state = 0.0, "down"
        self.lock = threading.Lock()

    def request(self, method, path, headers=None, body=None):
        """-> (status, [(header, value)], body). OSError when Hister didn't answer."""
        conn = self.cls(self.host, self.port, timeout=self.timeout)
        try:
            conn.request(method, path, body=body, headers=dict(headers or {}))
            resp = conn.getresponse()
            return resp.status, resp.getheaders(), resp.read(1 << 20)
        except (http.client.HTTPException, ValueError) as e:
            raise OSError(str(e))
        finally:
            conn.close()

    def profile(self, session=None, token=None):
        """-> ("ok", username, user_id) | ("out",) | ("off",) | ("down",). A bare 200 is never "signed in": with
        user_handling off Hister answers 200 with an empty body to anyone."""
        headers = {"Accept": "application/json"}
        if session is not None:
            headers["Cookie"] = "%s=%s" % (HISTER_COOKIE, session)
        if token is not None:
            headers["X-Access-Token"] = token
        try:
            status, _, body = self.request("GET", "/api/profile", headers)
        except OSError:
            return ("down",)
        if status in (401, 403):
            return ("out",)
        if status != 200:
            return ("down",)
        try:
            data = json.loads(body) if body.strip() else None
        except ValueError:
            data = None
        if isinstance(data, dict) and isinstance(data.get("username"), str) and data["username"]:
            uid = data.get("user_id")
            return ("ok", data["username"], uid if isinstance(uid, int) else 0)
        return ("off",)

    def logout(self, session):
        """POST /api/logout with that session -> (ended, Set-Cookie values). Ended is also True when Hister no longer
        knows the session (403): either way it is over."""
        try:
            status, headers, _ = self.request("POST", "/api/logout", {
                "Cookie": "%s=%s" % (HISTER_COOKIE, session), "Origin": "hister://", "Content-Length": "0"}, b"")
        except OSError:
            return False, []
        return status in (200, 204, 401, 403), [v for k, v in headers if k.lower() == "set-cookie"]

    def health(self):
        """"ok" | "user-handling-off" | "down", cached HEALTH_TTL s: Hister's /health, then an anonymous /api/profile
        (403 when users are on, 200 to anyone when they're off; neither creates a session row)."""
        with self.lock:
            if time.monotonic() - self.health_at < HEALTH_TTL:
                return self.health_state
        try:
            status, _, _ = self.request("GET", "/health")
            state = "down"
            if status == 200:
                state = {"out": "ok", "off": "user-handling-off"}.get(self.profile()[0], "down")
        except OSError:
            state = "down"
        with self.lock:
            if state != self.health_state:
                LOG("hister-login: Hister is %s" % state)
            self.health_at, self.health_state = time.monotonic(), state
        return state


# -- state -----------------------------------------------------------------------------------------------------------

SCHEMA = """
CREATE TABLE IF NOT EXISTS sessions (
  sid_hash TEXT PRIMARY KEY,
  hister_session TEXT NOT NULL,
  hister_hash TEXT NOT NULL,
  username TEXT NOT NULL DEFAULT '',
  user_id INTEGER NOT NULL DEFAULT 0,
  kind TEXT NOT NULL CHECK (kind IN ('browser', 'app')),
  label TEXT NOT NULL DEFAULT '',
  created_at INTEGER NOT NULL,
  last_seen INTEGER NOT NULL,
  verified_at INTEGER NOT NULL,
  expires_at INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS sessions_hister ON sessions (hister_hash);
CREATE TABLE IF NOT EXISTS pending_logout (
  hister_hash TEXT PRIMARY KEY,
  hister_session TEXT NOT NULL,
  since INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS room_sessions (
  rsid_hash TEXT PRIMARY KEY,
  parent TEXT NOT NULL,
  audience TEXT NOT NULL,
  created_at INTEGER NOT NULL,
  last_seen INTEGER NOT NULL,
  expires_at INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS room_sessions_parent ON room_sessions (parent);
CREATE TABLE IF NOT EXISTS codes (
  code_hash TEXT PRIMARY KEY,
  parent TEXT NOT NULL,
  audience TEXT NOT NULL,
  state TEXT NOT NULL,
  return_url TEXT NOT NULL,
  expires_at INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS tokens (
  token_hash TEXT PRIMARY KEY,
  username TEXT NOT NULL,
  user_id INTEGER NOT NULL DEFAULT 0,
  label TEXT NOT NULL,
  audiences TEXT NOT NULL,
  created_at INTEGER NOT NULL,
  last_used INTEGER NOT NULL DEFAULT 0,
  expires_at INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS ended (
  sid_hash TEXT PRIMARY KEY,
  at INTEGER NOT NULL
);
"""
CODE_TTL = 60                           # a code's life: the browser is on its way back already
ROOM_SEEN_EVERY = 60


class Store:
    """The sessions table (an id's hash -> a Hister session) and the Hister sign-outs still to make."""

    def __init__(self, path):
        self.path = path
        if path != ":memory:":
            folder = os.path.dirname(os.path.abspath(path))
            os.makedirs(folder, mode=0o700, exist_ok=True)
            if not os.path.exists(path):
                os.close(os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600))
            os.chmod(path, 0o600)
        self.db = sqlite3.connect(path, check_same_thread=False, isolation_level=None)
        self.db.row_factory = sqlite3.Row
        self.lock = threading.Lock()
        with self.lock:
            # WAL lets the token CLI read while the server runs. Haiku's SQLite can't share WAL between
            # processes ("locking protocol" when the CLI opens a WAL file), so there it keeps the rollback journal.
            if path != ":memory:" and not sys.platform.startswith("haiku"):
                self.db.execute("PRAGMA journal_mode=WAL")
            self.db.execute("PRAGMA busy_timeout=3000")
            self.db.executescript(SCHEMA)
            self.db.execute("PRAGMA user_version=2")    # 2 (0.3.0): room sessions, codes, room tokens, ended

    def q(self, sql, args=()):
        with self.lock:
            return self.db.execute(sql, args).fetchall()

    def create(self, hister_session, username, user_id, kind, label):
        sid = SID_PREFIX + b64e(secrets.token_bytes(32))
        t = now()
        self.q("INSERT INTO sessions VALUES (?,?,?,?,?,?,?,?,?,?,?)",
               (sha(sid), hister_session, sha(hister_session), username, user_id, kind, label[:80], t, t, t,
                t + SESSION_DAYS * 86400))
        return sid

    def get(self, sid):
        """The row of a live id, or None (an expired row is deleted on sight)."""
        if not isinstance(sid, str) or not SID_RE.match(sid):
            return None
        rows = self.q("SELECT rowid, * FROM sessions WHERE sid_hash = ?", (sha(sid),))
        if not rows:
            return None
        row = dict(rows[0])
        if row["expires_at"] <= now():
            self.drop_sid(row["sid_hash"])
            return None
        return row

    def verified(self, row, username, user_id):
        t = now()
        expires = min(t + SESSION_DAYS * 86400, row["created_at"] + CAP_DAYS * 86400)
        self.q("UPDATE sessions SET username = ?, user_id = ?, verified_at = ?, last_seen = ?, expires_at = ? "
               "WHERE sid_hash = ?", (username, user_id, t, t, expires, row["sid_hash"]))
        row.update(username=username, user_id=user_id, verified_at=t, last_seen=t, expires_at=expires)

    def seen(self, row):
        if now() - row["last_seen"] >= 60:
            self.q("UPDATE sessions SET last_seen = ? WHERE sid_hash = ?", (now(), row["sid_hash"]))

    def drop_hister(self, hister_hash, ended=False):
        """Delete every id on one Hister session, with every room session and unused code made from them; `ended`: a
        deliberate sign-out, remembered (the ended table) so the browser isn't signed straight back in. -> how many
        ids."""
        with self.lock:
            hashes = [r[0] for r in self.db.execute("SELECT sid_hash FROM sessions WHERE hister_hash = ?",
                                                     (hister_hash,)).fetchall()]
            self._drop(hashes, ended)
            return len(hashes)

    def drop_sid(self, sid_hash, ended=False):
        with self.lock:
            self._drop([sid_hash], ended)

    def _drop(self, hashes, ended):
        """(lock held) the ids, their room sessions and codes; tombstones for a deliberate sign-out."""
        for h in hashes:
            self.db.execute("DELETE FROM room_sessions WHERE parent = ?", (h,))
            self.db.execute("DELETE FROM codes WHERE parent = ?", (h,))
            self.db.execute("DELETE FROM sessions WHERE sid_hash = ?", (h,))
            if ended:
                self.db.execute("INSERT OR REPLACE INTO ended VALUES (?, ?)", (h, now()))

    def by_hash(self, sid_hash):
        rows = self.q("SELECT rowid, * FROM sessions WHERE sid_hash = ?", (sid_hash,))
        if not rows:
            return None
        row = dict(rows[0])
        if row["expires_at"] <= now():
            self.drop_sid(row["sid_hash"])
            return None
        return row

    def was_ended(self, sid):
        """This id was signed out on purpose (within OUT_MAX_AGE)."""
        if not isinstance(sid, str) or not SID_RE.match(sid):
            return False
        return bool(self.q("SELECT 1 FROM ended WHERE sid_hash = ? AND at > ?", (sha(sid), now() - OUT_MAX_AGE)))

    # room sessions (0.3.0)

    def create_room(self, parent, audience):
        rsid = histerauth.ROOM_PREFIX + b64e(secrets.token_bytes(32))
        t = now()
        expires = parent["created_at"] + CAP_DAYS * 86400
        self.q("INSERT INTO room_sessions VALUES (?,?,?,?,?,?)", (sha(rsid), parent["sid_hash"], audience, t, t,
                                                                   expires))
        return rsid, max(expires - t, 60)

    def get_room(self, rsid):
        if not isinstance(rsid, str) or not histerauth.RSID_RE.match(rsid):
            return None
        rows = self.q("SELECT * FROM room_sessions WHERE rsid_hash = ?", (sha(rsid),))
        if not rows:
            return None
        row = dict(rows[0])
        if row["expires_at"] <= now():
            self.q("DELETE FROM room_sessions WHERE rsid_hash = ?", (row["rsid_hash"],))
            return None
        if now() - row["last_seen"] >= ROOM_SEEN_EVERY:
            self.q("UPDATE room_sessions SET last_seen = ? WHERE rsid_hash = ?", (now(), row["rsid_hash"]))
        return row

    def rooms_of(self, parent_hash):
        return [r[0] for r in self.q("SELECT DISTINCT audience FROM room_sessions WHERE parent = ? AND expires_at > ? "
                                     "ORDER BY audience", (parent_hash, now()))]

    # codes (0.3.0): one use, CODE_TTL seconds

    def create_code(self, parent_hash, audience, state, return_url):
        code = histerauth.CODE_PREFIX + b64e(secrets.token_bytes(32))
        self.q("INSERT INTO codes VALUES (?,?,?,?,?,?)", (sha(code), parent_hash, audience, state, return_url,
                                                           now() + CODE_TTL))
        return code

    def take_code(self, code):
        """The code's row, deleted in the same breath (a second redeem finds nothing); None when unknown or expired."""
        if not isinstance(code, str) or not histerauth.CODE_RE.match(code):
            return None
        with self.lock:
            row = self.db.execute("SELECT * FROM codes WHERE code_hash = ?", (sha(code),)).fetchone()
            if row is None:
                return None
            self.db.execute("DELETE FROM codes WHERE code_hash = ?", (row["code_hash"],))
        row = dict(row)
        return row if row["expires_at"] > now() else None

    # room tokens (0.3.0): headless callers; only the hash is kept

    def add_token(self, value, username, label, audiences, days=0, user_id=0):
        t = now()
        self.q("INSERT INTO tokens VALUES (?,?,?,?,?,?,?,?)", (sha(value), username, user_id, label[:80],
                                                               ",".join(audiences), t, 0,
                                                               t + days * 86400 if days else 0))
        return sha(value)[:12]

    def get_token(self, value):
        if not isinstance(value, str) or not histerauth.RTOKEN_RE.match(value):
            return None
        rows = self.q("SELECT * FROM tokens WHERE token_hash = ?", (sha(value),))
        if not rows:
            return None
        row = dict(rows[0])
        if row["expires_at"] and row["expires_at"] <= now():
            return None
        if now() - row["last_used"] >= ROOM_SEEN_EVERY:
            self.q("UPDATE tokens SET last_used = ? WHERE token_hash = ?", (now(), row["token_hash"]))
        row["audiences"] = [a for a in row["audiences"].split(",") if a]
        return row

    def tokens_of(self, username=None):
        sql, args = "SELECT * FROM tokens", ()
        if username is not None:
            sql, args = sql + " WHERE username = ?", (username,)
        out = []
        for r in self.q(sql + " ORDER BY created_at", args):
            r = dict(r)
            r["id"], r["audiences"] = r["token_hash"][:12], [a for a in r["audiences"].split(",") if a]
            out.append(r)
        return out

    def revoke_token(self, token_id, username=None):
        """Revoke by the id (the first 12 hex of the hash, as listed); -> how many (0 or 1)."""
        if not re.fullmatch(r"[0-9a-f]{12}", token_id or ""):
            return 0
        sql, args = "DELETE FROM tokens WHERE substr(token_hash, 1, 12) = ?", (token_id,)
        if username is not None:
            sql, args = sql + " AND username = ?", args + (username,)
        with self.lock:
            return self.db.execute(sql, args).rowcount

    def of_user(self, user_id):
        return [dict(r) for r in self.q("SELECT rowid, * FROM sessions WHERE user_id = ? AND expires_at > ? "
                                        "ORDER BY last_seen DESC", (user_id, now()))]

    def by_rowid(self, rowid, user_id):
        rows = self.q("SELECT rowid, * FROM sessions WHERE rowid = ? AND user_id = ?", (rowid, user_id))
        return dict(rows[0]) if rows else None

    def cleanup(self):
        """Expired ids (with what was made from them), room sessions, codes, tokens and old tombstones."""
        t = now()
        with self.lock:
            n = self.db.execute("DELETE FROM sessions WHERE expires_at <= ?", (t,)).rowcount
            self.db.execute("DELETE FROM room_sessions WHERE expires_at <= ? OR parent NOT IN "
                            "(SELECT sid_hash FROM sessions)", (t,))
            self.db.execute("DELETE FROM codes WHERE expires_at <= ? OR parent NOT IN (SELECT sid_hash FROM sessions)",
                            (t,))
            self.db.execute("DELETE FROM tokens WHERE expires_at > 0 AND expires_at <= ?", (t,))
            self.db.execute("DELETE FROM ended WHERE at <= ?", (t - OUT_MAX_AGE,))
            return n

    def count(self):
        return self.q("SELECT count(*) FROM sessions")[0][0]

    def pending_add(self, hister_session):
        self.q("INSERT OR IGNORE INTO pending_logout VALUES (?,?,?)", (sha(hister_session), hister_session, now()))

    def pending(self):
        return [dict(r) for r in self.q("SELECT * FROM pending_logout")]

    def pending_done(self, hister_hash):
        self.q("DELETE FROM pending_logout WHERE hister_hash = ?", (hister_hash,))


# -- the core --------------------------------------------------------------------------------------------------------

class Login:
    def __init__(self, settings, store=None, hister=None, prefs=None):
        self.s = settings
        self.store = store or Store(settings.db)
        self.hister = hister or Hister(settings.hister_url)
        self.prefs = prefs or vprefs.Store(settings.prefs_db)
        self.tokens = {}                # sha(token) -> (expires, outcome)
        self.tokens_lock = threading.Lock()
        self.legacy_said = {}

    def who(self, kind, value, rooms=None):
        """A credential -> ("ok", username) | ("out", reason) | ("off",) | ("down",): the same checks as /v1/check
        (resolve)."""
        answer = self.resolve(kind, value, rooms)
        return ("ok", answer[1]["username"]) if answer[0] == "ok" else answer

    def resolve(self, kind, value, rooms=None):
        """One credential -> ("ok", info) | ("out", reason) | ("off",) | ("down",). info: username, user_id, kind
        (room | token | app | browser), room (a room session's origin), label, row (the helper session, when one).
        rooms: None for the helper's own callers (apps, its pages, the public prefs); else the asking room's origins
        (X-Machiya-Room; [] from a room too old to say), and then:
          - a room session (mhr_) must be one of those origins' (else "wrong-room"), and its helper session alive;
          - a room token (mht_) must name the room's own origin (the first);
          - a browser's helper id (the old shared-domain cookie) and Hister's raw token only while
            HISTER_LOGIN_LEGACY allows them (else "legacy-off"); an app's id (mhs_, kind app) in every room."""
        if kind == "sid" and isinstance(value, str) and value.startswith(histerauth.ROOM_PREFIX):
            kind = "room"
        if kind == "sid" and isinstance(value, str) and value.startswith(histerauth.RTOKEN_PREFIX):
            kind = "rtoken"
        if kind == "room":
            room = self.store.get_room(value)
            if room is None:
                return ("out", "signed-out")
            if rooms is not None and room["audience"] not in rooms:
                return ("out", "wrong-room")
            parent = self.store.by_hash(room["parent"])
            if parent is None:
                return ("out", "signed-out")
            outcome, row = self.check_row(parent)
            if outcome != "ok":
                return (outcome, "signed-out") if outcome == "out" else (outcome,)
            return ("ok", {"username": row["username"], "user_id": row["user_id"], "kind": "room",
                           "room": room["audience"], "row": row, "via": "session"})
        if kind == "rtoken":
            tok = self.store.get_token(value)
            if tok is None:
                return ("out", "signed-out")
            if rooms is not None and (not rooms or rooms[0] not in tok["audiences"]):
                return ("out", "wrong-room")
            return ("ok", {"username": tok["username"], "user_id": tok["user_id"], "kind": "token",
                           "label": tok["label"], "via": "token"})
        if kind == "sid":
            outcome, row = self.check_sid(value)
            if outcome != "ok":
                return (outcome, "signed-out") if outcome == "out" else (outcome,)
            if rooms is not None and row["kind"] == "browser" and "domain-cookie" not in self.s.legacy:
                return ("out", "legacy-off")
            if rooms is not None and row["kind"] == "browser":
                self.legacy_seen("the shared-domain cookie", rooms)
            return ("ok", {"username": row["username"], "user_id": row["user_id"], "kind": row["kind"], "row": row,
                           "via": "session"})
        if rooms is not None and "hister-token" not in self.s.legacy:
            return ("out", "legacy-off")
        answer = self.check_token(value)
        if answer[0] != "ok":
            return (answer[0], "signed-out") if answer[0] == "out" else answer
        if rooms is not None:
            self.legacy_seen("Hister's raw token", rooms)
        return ("ok", {"username": answer[1], "user_id": answer[2], "kind": "hister-token", "via": "token"})

    def legacy_seen(self, what, rooms):
        """Say (at most hourly per room and kind) that a room still got a legacy credential: the gate before
        HISTER_LOGIN_LEGACY=none is a quiet log."""
        key = (what, rooms[0] if rooms else "an old room")
        with self.tokens_lock:
            last = self.legacy_said.get(key, 0)
            if time.monotonic() - last < 3600:
                return
            self.legacy_said[key] = time.monotonic()
        LOG("hister-login: legacy: %s from %s (HISTER_LOGIN_LEGACY)" % key)

    def shared_prefs(self, username):
        """The account's Shared settings for /v1/check (a fresh browser's first render); {} when the file fails."""
        try:
            return vprefs.shared_only(self.prefs.snapshot(user_key(username))[1])
        except (sqlite3.Error, OSError, vprefs.PrefsError):
            return {}

    # checks

    def check_sid(self, sid):
        """-> (outcome, row): outcome "ok" | "out" | "off" | "down"."""
        row = self.store.get(sid)
        if row is None:
            return "out", None
        return self.check_row(row)

    def check_row(self, row):
        """A live helper session's row, checked with Hister when it was last checked VERIFY_TTL s ago or more."""
        if now() - row["verified_at"] < VERIFY_TTL:
            self.store.seen(row)
            return "ok", row
        answer = self.hister.profile(session=row["hister_session"])
        if answer[0] == "ok":
            self.store.verified(row, answer[1], answer[2])
            return "ok", row
        if answer[0] == "out":          # signed out at Hister (its UI, another sign-in, expiry): every id on it goes
            n = self.store.drop_hister(row["hister_hash"])
            LOG("hister-login: a Hister session ended; dropped %d id(s)" % n)
            return "out", None
        if answer[0] == "off":
            LOG("hister-login: HISTER USER HANDLING IS OFF: /api/profile answers anyone; nobody is signed in")
        return answer[0], None

    def check_token(self, token):
        key = sha(token)
        with self.tokens_lock:
            hit = self.tokens.get(key)
            if hit and hit[0] > time.monotonic():
                return hit[1]
        answer = self.hister.profile(token=token)
        ttl = {"ok": TOKEN_TTL_OK, "out": TOKEN_TTL_OUT}.get(answer[0])
        if ttl:
            with self.tokens_lock:
                if len(self.tokens) > 4096:
                    self.tokens.clear()
                self.tokens[key] = (time.monotonic() + ttl, answer)
        return answer

    def end_hister(self, hister_session, ended=True):
        """Sign one Hister session out and drop every id on it, and every room session and code made from them; with
        `ended` (a sign-out someone asked for) the ids are remembered, so a browser that comes back with one sees the
        page, not an automatic sign-in. -> Set-Cookie values from Hister's logout. When Hister can't be reached the
        ids still go (the rooms see 401 at once) and the logout is retried later."""
        n = self.store.drop_hister(sha(hister_session), ended=ended)
        ended, cookies = self.hister.logout(hister_session)
        if not ended:
            self.store.pending_add(hister_session)
            LOG("hister-login: Hister unreachable at sign-out; dropped %d id(s), Hister's logout queued" % n)
        return cookies

    def retry_pending(self):
        for p in self.store.pending():
            ended, _ = self.hister.logout(p["hister_session"])
            if ended:
                self.store.pending_done(p["hister_hash"])

    # cookies

    def cookie(self, name, value, max_age, domain=False, path="/"):
        attrs = ["%s=%s" % (name, value), "Path=" + path, "HttpOnly", "SameSite=Lax", "Max-Age=%d" % max_age]
        if self.s.secure:
            attrs.append("Secure")
        if domain and self.s.cookie_domain:
            attrs.append("Domain=" + self.s.cookie_domain)
        return "; ".join(attrs)

    def sso_cookie(self, sid):
        """The helper's own session cookie: host-only (__Host-machiya_sso), plus, while HISTER_LOGIN_LEGACY has
        domain-cookie, the old copy on the shared domain for rooms that haven't moved to their own cookie yet."""
        out = [self.cookie(self.s.own, sid, CAP_DAYS * 86400)]
        if "domain-cookie" in self.s.legacy:
            out.append(self.cookie(self.s.sso, sid, CAP_DAYS * 86400, domain=True))
        return out

    def sso_clear(self):
        """Drop the helper's session cookie, and the legacy shared-domain one wherever it may be (always: harmless)."""
        out = [self.cookie(self.s.own, "", 0)]
        if self.s.cookie_domain:
            out.append(self.cookie(self.s.sso, "", 0, domain=True))
        if self.s.own != self.s.sso:
            out.append(self.cookie(self.s.sso, "", 0))
        return out

    def own_sid(self, headers):
        """The browser's helper id: the host-only cookie, else (legacy) the shared-domain one."""
        sid = sso_value(headers, self.s.own)
        if sid is None and "domain-cookie" in self.s.legacy and self.s.own != self.s.sso:
            sid = sso_value(headers, self.s.sso)
        return sid

    def signed_out(self):
        """A deliberate sign-out: the marker that stops the automatic sign-in (?provider=) until the next sign-in.
        Host-only (0.3.0): the rooms keep their own, and the helper remembers ended ids itself (the ended table)."""
        return [self.cookie(self.s.out, "1", OUT_MAX_AGE)]

    def marker_clear(self):
        out = [self.cookie(self.s.out, "", 0)]
        if self.s.cookie_domain:
            out.append(self.cookie(self.s.legacy_out, "", 0, domain=True))
        return out

    def marked(self, headers):
        """The browser signed out on purpose, or the last automatic round trip failed: show the page. Also when its
        own cookie names an id that was signed out on purpose (a room's Sign Out can't set this host's marker)."""
        names = [self.s.out] + ([self.s.legacy_out] if "domain-cookie" in self.s.legacy else [])
        if any(histerauth.HisterAuth.cookie_values(headers.get("Cookie"), n) for n in names):
            return True
        return self.store.was_ended(sso_value(headers, self.s.own) or sso_value(headers, self.s.sso))

    def return_cookie(self, ret, app, state=""):
        data = {"r": ret, "a": 1 if app else 0}
        if state:
            data["s"] = state
        value = b64e(json.dumps(data, separators=(",", ":")).encode())
        return self.cookie(self.s.return_cookie, value, RETURN_MAX_AGE)

    def return_clear(self):
        return self.cookie(self.s.return_cookie, "", 0)

    def read_return(self, cookie_header):
        """The remembered (return, app, state) from the return cookie, checked again now; (None, False, "") if none
        holds."""
        for value in histerauth.HisterAuth.cookie_values(cookie_header, self.s.return_cookie)[:2]:
            try:
                data = json.loads(b64d(value))
                app = data.get("a") == 1
                ret = self.safe(data.get("r"), app)
                state = data.get("s") if isinstance(data.get("s"), str) and histerauth.NONCE_RE.match(data["s"]) \
                    else ""
                if ret:
                    return ret, app, state
            except (ValueError, TypeError, AttributeError, UnicodeError):
                continue
        return None, False, ""

    def safe(self, ret, app=False):
        if app:     # 0.2.1 (sweep LEAD-3): exactly <scheme>://signed-in (HisterKit's callbackURL), no other path
            return ret if isinstance(ret, str) and ret in ("%s://signed-in" % s for s in self.s.app_schemes) else None
        return histerauth.safe_return(ret, self.s.return_hosts, ())

    def finish(self, hister_session, answer, ret, app, headers, label="", state=""):
        """A good Hister session: an id for it, and where to go. -> (location, cookies). For a room's address with a
        state (0.3.0) the way back is that room's /machiya/callback with a one-time code; without a state it is the
        address itself (the room then makes its own trip; a proxied origin goes through its /machiya/start)."""
        username, user_id = answer[1], answer[2]
        if app:
            sid = self.store.create(hister_session, username, user_id, "app", label or "Shiori app")
            return ret + "#" + urlencode({"sid": sid, "hister": hister_session}), []
        cookies, row = [], None
        names = [self.s.own] + ([self.s.sso] if "domain-cookie" in self.s.legacy and self.s.own != self.s.sso
                                else [])
        have = {n: histerauth.HisterAuth.cookie_values(headers.get("Cookie"), n)[:4] for n in names}
        for value in [v for n in names for v in have[n]]:
            r = self.store.get(value)
            if r and r["hister_hash"] == sha(hister_session) and r["kind"] == "browser":
                row, sid = r, value     # this browser already holds an id for this session
                break
        if row is None:
            sid = self.store.create(hister_session, username, user_id, "browser", device_label(headers))
            row = self.store.get(sid)
            cookies += self.sso_cookie(sid)
        else:
            if sid not in have[self.s.own]:
                cookies.append(self.cookie(self.s.own, sid, CAP_DAYS * 86400))     # moved to the host-only cookie
            if "domain-cookie" in self.s.legacy and self.s.own != self.s.sso and sid not in have.get(self.s.sso, []):
                cookies.append(self.cookie(self.s.sso, sid, CAP_DAYS * 86400, domain=True))
        if self.marked(headers):                # signed in again: the automatic sign-in is back
            cookies += self.marker_clear()
        return self.way_back(row, ret, state), cookies

    def way_back(self, row, ret, state):
        """Where a signed-in browser goes: a room's callback with a code (a state came with it), a proxied origin's
        start (no state: the hosted pages make their trip there), the address itself, or Hister's front page."""
        if not ret:
            return self.s.public_url + "/"
        origin = histerauth.origin_of(ret)
        if not origin or origin == self.s.origin:
            return ret
        if state:
            code = self.store.create_code(row["sid_hash"], origin, state, ret)
            return origin + histerauth.CALLBACK_PATH + "?" + urlencode({"code": code})
        if origin in self.s.proxied:
            return origin + "/machiya/start?" + urlencode({"return": ret})
        return ret

    def redeem(self, code, room, nonce):
        """A room trades a code (POST /v1/redeem, or the proxied origins' callback here): -> (status, JSON). The code
        is gone after this, good or not. It must be the asking room's (its origin), carry this browser's nonce (its
        SHA-256 is the state the trip started with), and its helper session must still be alive."""
        row = self.store.take_code(code)
        if row is None or not room or row["audience"] != room or not isinstance(nonce, str) \
                or not histerauth.NONCE_RE.match(nonce) \
                or not secrets.compare_digest(histerauth.state_hash(nonce), row["state"]):
            return 401, {"reason": "bad-code"}
        parent = self.store.by_hash(row["parent"])
        if parent is None:
            return 401, {"reason": "bad-code"}
        outcome, parent = self.check_row(parent)
        if outcome == "out" or parent is None and outcome == "ok":
            return 401, {"reason": "bad-code"}
        if outcome != "ok":
            return 503, {"reason": "user-handling-off" if outcome == "off" else "hister-unavailable"}
        rsid, max_age = self.store.create_room(parent, room)
        return 200, {"session": rsid, "return": row["return_url"], "username": parent["username"],
                     "user_id": parent["user_id"], "max_age": max_age, "prefs": self.shared_prefs(parent["username"])}


def device_label(headers):
    ua = headers.get("User-Agent") or ""
    browser = next((b for k, b in (("Edg/", "Edge"), ("Firefox/", "Firefox"), ("Chrome/", "Chrome"),
                                   ("Safari/", "Safari")) if k in ua), "Browser")
    device = next((d for k, d in (("iPhone", "iPhone"), ("iPad", "iPad"), ("Android", "Android"),
                                  ("Macintosh", "Mac"), ("Windows", "Windows"), ("Linux", "Linux")) if k in ua), "")
    return browser + (" on " + device if device else "")


def hister_cookie(headers):
    for value in histerauth.HisterAuth.cookie_values(headers.get("Cookie"), HISTER_COOKIE)[:4]:
        if HISTER_SESSION_RE.match(value):
            return value
    return None


def sso_value(headers, name=SSO):
    for value in histerauth.HisterAuth.cookie_values(headers.get("Cookie"), name)[:4]:
        if SID_RE.match(value):
            return value
    return None


# -- pages -----------------------------------------------------------------------------------------------------------

PAGE_HEADERS = [
    ("Content-Type", "text/html; charset=utf-8"), ("Cache-Control", "no-store"), ("X-Frame-Options", "DENY"),
    ("X-Content-Type-Options", "nosniff"), ("Referrer-Policy", "same-origin"),
    ("Content-Security-Policy", "default-src 'self'; img-src 'self' data:; style-src 'self'; script-src 'self'; "
                                "connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; object-src 'none'"),
]
JSON_HEADERS = [("Content-Type", "application/json"), ("Cache-Control", "no-store"),
                ("X-Content-Type-Options", "nosniff")]
STATIC = {
    "machiya.css": (os.path.join(shell.UI_DIR, "machiya.css"), "text/css; charset=utf-8"),
    "machiya.js": (os.path.join(shell.UI_DIR, "machiya.js"), "text/javascript; charset=utf-8"),
    "signin.js": (os.path.join(HERE, "static", "signin.js"), "text/javascript; charset=utf-8"),
    "icons/hister.svg": (os.path.join(HERE, "static", "hister.svg"), "image/svg+xml"),
}
e = shell.e


def render(headers, title, body, scripts=()):
    ctx = shell.prefs(headers.get("Cookie"))
    page = shell.page(ctx, "machiya", title, shell.header("machiya", [], "", {}, settings=False)
                      + body, links={}, manifest=False, scripts=scripts)
    return page.replace('"/static/', '"/machiya/static/').encode()


def signin_page(s, headers, ret, app, error="", state=""):
    nxt = "/machiya/signin?" + urlencode([("return", ret or "")] + ([("state", state)] if state else [])
                                         + ([("app", "1")] if app else []))
    oauth = ""
    if "oidc" in s.providers:
        oauth = ('<p class="actions"><a class="button" href="/api/oauth?provider=oidc">Sign in with %s</a></p>'
                 % e(s.oidc_label))
    alert = '<p class="signin-error" role="alert" data-error%s>%s</p>' % ("" if error else " hidden", e(error))
    target = signin_target(s, ret, app)
    heading = "Sign In to %s" % target if target else "Sign In"
    # No <form>, as Hister's own sign-in page: the script reads the fields (ids, no names) on the button's click or
    # Enter and sends the password to Hister's /api/login, so nothing on this page can post anything to the helper,
    # and a password manager's auto-submit (a form.submit() from its own script world, which no page script sees)
    # has no form to submit: it clicks the button or presses Enter, as on Hister's page.
    body = (
        '<main class="signin"><h1>%s</h1>%s'
        '<div class="group" id="hister-signin" data-next="%s">'
        '<label class="item"><span>Name</span><input id="signin-username" required maxlength="64" autofocus '
        'autocomplete="username" autocapitalize="none" autocorrect="off" spellcheck="false"></label>'
        '<label class="item"><span>Password</span><input id="signin-password" type="password" required '
        'maxlength="1024" autocomplete="current-password"></label>'
        '<button type="button">Sign In</button></div>'
        '<div class="empty">%s</div>'
        '<p class="footnote">One sign-in for Hister and every Machiya app on this device, until you sign out.</p>'
        '<noscript><p class="signin-error">The password form needs JavaScript.</p></noscript>'
        '</main>'
    ) % (e(heading), alert, e(nxt), oauth)
    return render(headers, heading + " · Machiya", body, ["/static/signin.js?v=%s" % VERSION])


def signin_target(s, ret, app):
    """The app a sign-in returns to, by name, for the heading: an app's scheme (shiori:) is Shiori; else the room or
    hosted pages the return address belongs to; '' when unknown or none."""
    if not ret:
        return ""
    if app:
        return "Shiori"
    origin = histerauth.origin_of(ret)
    if not origin:
        return ""
    for key, o in s.rooms.items():
        if o == origin:
            return ROOM_NAMES.get(key, key.capitalize())
    if origin in s.proxied:
        return "Shiori"
    return ""


def confirm_page(headers, ret, provider=""):
    """The app sign-in asked for by a navigation that wasn't the app's own web session (0.2.1, sweep LEAD-3): one
    same-origin POST finishes it; nothing is created or remembered until then."""
    fields = [("return", ret), ("app", "1")] + ([("provider", provider)] if provider else [])
    body = (
        '<main class="signin"><h1>Sign In to the App?</h1>'
        '<p>An app on this device (%s) wants to sign in to Hister and Machiya as you. '
        'Continue only if you just started this.</p>'
        '<form class="group" method="post" action="/machiya/signin">%s<button type="submit">Continue</button></form>'
        '<p class="footnote">Didn\'t start a sign-in? Close this page.</p></main>'
    ) % (e(ret.split(":", 1)[0]), "".join('<input type="hidden" name="%s" value="%s">' % (e(k), e(v)) for k, v in fields))
    return render(headers, "Sign In to the App? · Machiya", body)


def message_page(headers, heading, text, actions=()):
    return render(headers, heading + " · Machiya", shell.message(heading, text, actions))


ROOM_NAMES = {"kura": "Kura", "niwa": "Niwa", "konbini": "Konbini", "machiya": "Machiya", "shiori": "Shiori",
              "machiya-mcp": "machiya-mcp", "smallweb": "smallweb"}


def room_name(s, origin):
    """An origin as the sessions page names it: the room (from MACHIYA_ROOMS), the hosted pages, or its host."""
    for key, o in s.rooms.items():
        if o == origin:
            return ROOM_NAMES.get(key, key.capitalize())
    if origin in s.proxied:
        return "Shiori (%s)" % urlsplit(origin).hostname.split(".")[0]
    return urlsplit(origin).netloc or origin


def sessions_page(s, headers, me, rows, done="", tokens=(), new_token=None):
    items = []
    for r in rows:
        this = r["sid_hash"] == me["sid_hash"]
        when = time.strftime("%Y-%m-%d %H:%M", time.gmtime(r["last_seen"]))
        rooms = ", ".join(room_name(s, o) for o in r.get("rooms") or ())
        items.append(
            '<div class="item"><span>%s<br><small class="value">%s · last seen %s UTC%s%s</small></span>'
            '<form method="post" action="/machiya/sessions"><input type="hidden" name="id" value="%d">'
            '<button type="submit" name="do" value="one">Sign&nbsp;Out</button></form></div>'
            % (e(r["label"] or r["kind"]), "app" if r["kind"] == "app" else "browser", e(when),
               " · this device" if this else "", (" · " + e(rooms)) if rooms else "", r["rowid"]))
    note = '<div class="machiya-banner" role="status">%s</div>' % e(done) if done else ""
    toks = []
    for t in tokens:
        used = time.strftime("%Y-%m-%d", time.gmtime(t["last_used"])) if t["last_used"] else "never"
        toks.append(
            '<div class="item"><span>%s<br><small class="value">%s · made %s · last used %s · id %s</small></span>'
            '<form method="post" action="/machiya/sessions"><input type="hidden" name="token" value="%s">'
            '<button type="submit" name="do" value="token-revoke">Revoke</button></form></div>'
            % (e(t["label"]), e(", ".join(room_name(s, o) for o in t["audiences"])),
               e(time.strftime("%Y-%m-%d", time.gmtime(t["created_at"]))), e(used), e(t["id"]), e(t["id"])))
    shown = ""
    if new_token and new_token.get("value"):
        shown = ('<div class="machiya-banner" role="status">New token for %s. Copy it now: it isn&#x27;t shown '
                 'again.<br><code class="token-value">%s</code></div>' % (e(new_token["label"]), e(new_token["value"])))
    elif new_token and new_token.get("error"):
        shown = '<p class="signin-error" role="alert">%s</p>' % e(new_token["error"])
    boxes = "".join('<label class="item"><span>%s</span><input type="checkbox" name="room" value="%s"></label>'
                    % (e(ROOM_NAMES.get(k, k.capitalize())), e(k)) for k in sorted(s.rooms))
    body = (
        '<main class="settings sessions"><h1 class="sechead">Signed In as %s</h1>%s'
        '<h2>Sessions</h2><div class="group">%s<div class="item"><span>All Sessions</span>'
        '<form method="post" action="/machiya/sessions"><button type="submit" name="do" value="all">'
        'Sign&nbsp;Out Everywhere</button></form></div></div>'
        '<p class="footnote">Signing out ends a session in Hister and every room within a minute.</p>'
        '<h2>Room Tokens</h2>%s<div class="group">%s</div>'
        '<p class="footnote">For scripts and agents. Each opens only the rooms it names, never Hister.</p>'
        '<form class="group" method="post" action="/machiya/sessions">'
        '<label class="item"><span>Name</span><input name="label" required maxlength="80" '
        'placeholder="pm on the laptop"></label>%s'
        '<div class="item"><span></span><button type="submit" name="do" value="token-new">New Token</button></div>'
        '</form>'
        '</main>'
    ) % (e(me["username"]), note, "".join(items) or '<div class="item"><span>None</span></div>', shown,
         "".join(toks) or '<div class="item"><span>None</span></div>', boxes)
    return render(headers, "Sessions · Machiya", body)


# -- HTTP ------------------------------------------------------------------------------------------------------------

class Handler(http.server.BaseHTTPRequestHandler):
    server_version = "hister-login/" + VERSION
    protocol_version = "HTTP/1.1"
    login = None                        # set on the subclass
    _body_read = False                  # this request's body was read in full (reset per request)

    def handle_one_request(self):
        """Every request starts afresh (0.1.2). HTTP/1.1 keeps a connection, and one handler, for many requests, and
        Tailscale Serve sends different people's requests down the same connection: nothing from the last request may
        decide this one. A body this request didn't read would be parsed as the NEXT request on the connection (one
        smuggled past Serve, with headers Serve never saw), so the connection closes instead."""
        self._body_read = False
        super().handle_one_request()
        if not self.close_connection and self.unread_body():
            self.close_connection = True

    def unread_body(self):
        headers = getattr(self, "headers", None)
        if headers is None or self._body_read:
            return False
        lengths = headers.get_all("Content-Length") or []
        return headers.get("Transfer-Encoding") is not None or any(v.strip() != "0" for v in lengths)

    def end_headers(self):
        if not self.close_connection and self.unread_body():
            self.send_header("Connection", "close")     # sets close_connection: the unread bytes go with it
        super().end_headers()

    def log_message(self, fmt, *args):          # the path only: queries carry return addresses and OAuth codes
        LOG("%s %s %s" % (self.command, urlsplit(self.path).path[:120], args[1] if len(args) > 1 else ""))

    def send(self, status, headers, body=b""):
        self.send_response(status)
        for k, v in headers:
            self.send_header(k, v)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def json(self, status, data, extra=()):
        self.send(status, JSON_HEADERS + list(extra), json.dumps(data).encode())

    def page(self, status, body, extra=()):
        self.send(status, PAGE_HEADERS + list(extra), body)

    def redirect(self, location, cookies=(), status=303):
        self.send(status, [("Location", location), ("Cache-Control", "no-store")]
                  + [("Set-Cookie", c) for c in cookies])

    def body(self):
        data = vsignin.read_body(self.headers, self.rfile, MAX_BODY)
        if data is None:
            self.close_connection = True
        else:
            self._body_read = True
        return data

    def query(self):
        try:
            return {k: v[0] for k, v in parse_qs(urlsplit(self.path).query, max_num_fields=8).items()}
        except ValueError:
            return {}

    def guard(self, fn):
        try:
            fn()
        except Exception as ex:             # never a traceback to a client
            LOG("hister-login: %s on %s %s" % (type(ex).__name__, self.command, urlsplit(self.path).path))
            try:
                self.json(500, {"error": "internal error"})
            except OSError:
                pass

    def reply(self, answer):
        status, headers, body = answer
        self.send(status, headers, body)

    def prefs_for(self, kind, value, cookie=False, rooms=None, origins=None):
        """GET/PUT of the account's settings (docs/contracts/prefs.md) for one credential: the key is only ever the
        user that credential proves (resolved as /v1/check does; `rooms`: the asking room's origins, None for the
        helper's own callers). A PUT carried by a cookie must come from `origins` (default: the return hosts' pages).
        Values are never logged."""
        lg = self.login
        if self.command == "PUT":
            body = vsignin.read_body(self.headers, self.rfile, vprefs.MAX_BODY)
            if body is None:
                self.close_connection = True
                return self.json(413, {"error": "request body too large"})
            self._body_read = True
        elif self.command not in ("GET", "HEAD"):
            return self.json(405, {"error": "GET or PUT"}, [("Allow", "GET, PUT")])
        who = lg.who(kind, value, rooms)
        if who[0] == "out":
            return self.json(401, {"error": "sign in", "signin": lg.s.public_url + "/machiya/signin",
                                   "reason": who[1] if len(who) > 1 else "signed-out"})
        if who[0] != "ok":
            return self.json(503, {"error": "preferences unavailable",
                                   "reason": "user-handling-off" if who[0] == "off" else "hister-unavailable"})
        key = user_key(who[1])
        try:
            if self.command != "PUT":
                rev, values, updated = lg.prefs.snapshot(key)
                if vprefs.matches(self.headers.get("If-None-Match"), rev):
                    return self.reply(vsignin.not_modified(rev))
                return self.reply(vsignin.prefs_answer(200, rev, values, updated))
            if cookie and not vsignin.same_origin(self.headers, lg.s.secure, origins or self.prefs_origins()):
                return self.json(403, {"error": "cross-site write refused"})
            changes, refused = vsignin.parse_put(self.headers, body)
            if refused:
                return self.reply(refused)
            try:
                return self.reply(vsignin.prefs_answer(200, *lg.prefs.write(key, changes)))
            except vprefs.PrefsError as ex:
                return self.json(400, {"error": str(ex)})
        except (sqlite3.Error, OSError) as ex:
            LOG("hister-login: the prefs file failed: %s" % type(ex).__name__)
            return self.json(503, {"error": "preferences unavailable"})

    def prefs_origins(self):
        s = self.login.s
        return [s.public_url] + ["https://" + h for h in s.return_hosts]


class Internal(Handler):
    """:8081, the rooms and nginx only."""

    def do_GET(self):
        self.guard(self._get)

    def do_PUT(self):
        self.guard(self._put)

    def _put(self):
        if urlsplit(self.path).path != "/v1/prefs":
            return self.json(404, {"error": "not found"})
        self.internal_prefs()

    def rooms(self):
        """X-Machiya-Room: the asking room's origins (its own first; v0.22 rooms always say), [] from an older room
        or an unreadable header (then only legacy credentials can pass)."""
        values = self.headers.get_all("X-Machiya-Room") or []
        return histerauth.origins_header(values[0]) if len(values) == 1 else []

    def one_credential(self):
        """Exactly one of X-Machiya-Session (mhs_, mhr_, mht_) and X-Access-Token (Hister's), once: (kind, value), or
        None (the caller answers 400)."""
        sid = self.headers.get_all("X-Machiya-Session") or []
        token = self.headers.get_all("X-Access-Token") or []
        if len(sid) + len(token) != 1:
            return None
        return ("sid", sid[0].strip()) if sid else ("token", token[0].strip())

    def internal_prefs(self):
        """GET/PUT /v1/prefs: a room's /api/prefs, with the caller's own credential (exactly one, as /v1/check)."""
        cred = self.one_credential()
        if cred is None:                            # a PUT's unread body closes the connection (end_headers)
            return self.json(400, {"reason": "one credential"})
        return self.prefs_for(cred[0], cred[1], rooms=self.rooms())

    def do_POST(self):
        self.guard(self._post)

    def _get(self):
        lg = self.login
        path = urlsplit(self.path).path
        if path == "/healthz":
            state = lg.hister.health()
            ok = state == "ok"
            return self.json(200 if ok else 503, {"ok": ok, "hister": state, "version": VERSION,
                                                  "sessions": lg.store.count()})
        if path == "/v1/check":
            cred = self.one_credential()
            if cred is None:
                return self.json(400, {"reason": "one credential"})
            answer = lg.resolve(cred[0], cred[1], self.rooms())
            if answer[0] == "ok":
                info = answer[1]
                return self.json(200, {"username": info["username"], "user_id": info["user_id"], "via": info["via"],
                                       "kind": info["kind"], "room": info.get("room"),
                                       "prefs": lg.shared_prefs(info["username"])})
            if answer[0] == "out":
                return self.json(401, {"reason": answer[1] if len(answer) > 1 else "signed-out"})
            return self.json(503, {"reason": "user-handling-off" if answer[0] == "off" else "hister-unavailable"})
        if path == "/v1/nginx":
            return self.nginx()
        if path == "/v1/prefs":
            return self.internal_prefs()
        self.json(404, {"error": "not found"})

    def nginx(self):
        """GET /v1/nginx (auth_request for the hosted pages): the hosted pages' room session (X-Machiya-Session, or
        their room cookie in Cookie) with X-Machiya-Room (their origin) -> 200 and X-Hister-Cookie for nginx's own
        hop to Hister; legacy: a browser's helper id without X-Machiya-Room (while domain-cookie is on)."""
        lg = self.login
        rooms = self.rooms()
        sid = (self.headers.get("X-Machiya-Session") or "").strip()
        if not sid and rooms:
            sid = next((v for v in histerauth.HisterAuth.cookie_values(self.headers.get("Cookie"),
                                                                       lg.s.proxied_cookie)[:4]
                        if histerauth.RSID_RE.match(v)), "")
        answer = lg.resolve("sid", sid, rooms) if sid else ("out",)
        row = answer[1].get("row") if answer[0] == "ok" else None
        if row is not None:
            return self.send(200, [("X-Hister-Cookie", "%s=%s" % (HISTER_COOKIE, row["hister_session"])),
                                   ("X-Hister-User", row["username"]), ("Cache-Control", "no-store")])
        return self.send(401 if answer[0] == "out" else 503, [("Cache-Control", "no-store")])

    def _post(self):
        path = urlsplit(self.path).path
        if path == "/v1/redeem":
            return self.redeem()
        if path != "/v1/signout":
            return self.json(404, {"error": "not found"})
        self.body()
        lg = self.login
        value = (self.headers.get("X-Machiya-Session") or "").strip()
        if value.startswith(histerauth.ROOM_PREFIX):
            room = lg.store.get_room(value)
            rooms = self.rooms()
            row = lg.store.by_hash(room["parent"]) if room and (not rooms or room["audience"] in rooms) else None
        else:
            row = lg.store.get(value)
        if row:
            lg.end_hister(row["hister_session"])
        self.send(204, [("Cache-Control", "no-store")])

    def redeem(self):
        """POST /v1/redeem: a room trades the code its callback got (X-Machiya-Code), saying who it is
        (X-Machiya-Room: its own origin) and what this browser's nonce is (X-Machiya-State)."""
        if self.body() is None:
            return self.json(413, {"error": "request body too large"})
        rooms = self.rooms()
        status, data = self.login.redeem((self.headers.get("X-Machiya-Code") or "").strip(),
                                         rooms[0] if len(rooms) == 1 else None,
                                         (self.headers.get("X-Machiya-State") or "").strip())
        self.json(status, data)


class Public(Handler):
    """:8080, browsers and apps through Tailscale Serve: /machiya/… and /api/oauth/callback."""

    def do_GET(self):
        self.guard(self._get)

    def do_HEAD(self):
        self.guard(self._get)

    def do_POST(self):
        self.guard(self._post)

    def do_PUT(self):
        self.guard(self._put)

    def _put(self):
        if urlsplit(self.path).path != "/machiya/api/prefs":
            return self.json(404, {"error": "not found"})
        origin = self.request_origin()
        if origin in self.login.s.proxied:
            return self.proxied_prefs(origin)
        self.public_prefs()

    # -- the hosted pages' hosts (HISTER_LOGIN_PROXIED_ORIGINS): their nginx sends /machiya/… here with their Host

    def proxied_get(self, origin, path):
        """A proxied origin's GET: the helper is that origin's room ("shiori") for these paths only."""
        if path.startswith("/machiya/static/"):
            return self.static(path[len("/machiya/static/"):])
        if path == "/machiya/start":
            return self.proxied_start(origin)
        if path == "/machiya/callback":
            return self.proxied_callback(origin)
        if path == "/machiya/api/prefs":
            return self.proxied_prefs(origin)
        if path == "/machiya/signed-out":
            return self.page(200, message_page(self.headers, "Signed Out", "You're signed out of Hister and every "
                                               "Machiya room on this device.",
                                               [("/machiya/start?return=%2F", "Sign In Again")]))
        self.page(404, message_page(self.headers, "Not Found", "There's nothing here.", [("/", "Go Back")]))

    def proxied_names(self):
        c = self.login.s.proxied_cookie
        return c, c + "_state", c + "_out"

    def proxied_start(self, origin):
        """GET /machiya/start?return=<page> on a hosted pages' host: a fresh nonce in that host's own state cookie,
        then the helper's sign-in with its SHA-256 (and the automatic provider, unless this host signed out on
        purpose). The helper sends a browser here when a hosted page's address came without a state."""
        lg = self.login
        _, state_name, out_name = self.proxied_names()
        q = self.query()
        ret = q.get("return") or "/"
        if ret.startswith("/") and not ret.startswith(("//", "/\\")):
            ret = origin + ret
        if histerauth.origin_of(ret) != origin or not lg.safe(ret):
            ret = origin + "/"
        nonce = histerauth.new_nonce()
        params = [("return", ret), ("state", histerauth.state_hash(nonce))]
        marked = bool(histerauth.HisterAuth.cookie_values(self.headers.get("Cookie"), out_name))
        if lg.s.auto_provider and lg.s.auto_provider in lg.s.providers and not marked:
            params += [("provider", lg.s.auto_provider), ("auto", "1")]
        self.redirect(lg.s.public_url + "/machiya/signin?" + urlencode(params),
                      [lg.cookie(state_name, nonce, histerauth.STATE_MAX_AGE)], status=302)

    def proxied_callback(self, origin):
        """GET /machiya/callback?code=… on a hosted pages' host: the code traded here (as a room would at
        /v1/redeem), the room cookie set on that host, then the page."""
        lg = self.login
        name, state_name, out_name = self.proxied_names()
        code = self.query().get("code") or ""
        nonce = next((v for v in histerauth.HisterAuth.cookie_values(self.headers.get("Cookie"), state_name)[:4]
                      if histerauth.NONCE_RE.match(v)), "")
        status, data = lg.redeem(code, origin, nonce) if code and nonce else (401, {})
        done = [lg.cookie(state_name, "", 0)]
        if status == 200:
            ret = data["return"] if histerauth.origin_of(data["return"]) == origin else origin + "/"
            cookies = [lg.cookie(name, data["session"], data["max_age"])] + done + [lg.cookie(out_name, "", 0)]
            return self.send(302, [("Location", ret), ("Cache-Control", "no-store"), ("Referrer-Policy", "no-referrer")]
                             + [("Set-Cookie", c) for c in cookies])
        if status == 503:
            return self.page(503, message_page(self.headers, "Sign-In Is Unavailable", "Hister can't be reached right "
                                               "now. Try again in a minute.", [("/", "Try Again")]),
                             [("Set-Cookie", c) for c in done])
        self.page(401, message_page(self.headers, "Sign In", "That sign-in didn't finish. Sign in to continue.",
                                    [("/machiya/start?return=%2F", "Sign In")]), [("Set-Cookie", c) for c in done])

    def proxied_prefs(self, origin):
        name, _, _ = self.proxied_names()
        sid = next((v for v in histerauth.HisterAuth.cookie_values(self.headers.get("Cookie"), name)[:4]
                    if histerauth.RSID_RE.match(v)), "")
        if not sid:
            if self.command == "PUT":
                self.body()
            return self.json(401, {"error": "sign in", "signin": origin + "/machiya/start?return=%2F"})
        return self.prefs_for("room", sid, cookie=True, rooms=[origin], origins=[origin])

    def proxied_post(self, origin, path):
        """POST /machiya/signout on a hosted pages' host (same-origin): the browser's helper session ends (Hister's,
        every room's), this host's cookie goes, and its marker stops the automatic sign-in from here."""
        lg = self.login
        if self.body() is None:
            return self.json(413, {"error": "request body too large"})
        if path != "/machiya/signout":
            return self.json(404, {"error": "not found"})
        if not vsignin.same_origin(self.headers, lg.s.secure, (origin,)):
            return self.page(403, message_page(self.headers, "Refused", "A sign-out must come from this page."))
        name, state_name, out_name = self.proxied_names()
        for value in histerauth.HisterAuth.cookie_values(self.headers.get("Cookie"), name)[:4]:
            room = lg.store.get_room(value)
            parent = lg.store.by_hash(room["parent"]) if room and room["audience"] == origin else None
            if parent:
                lg.end_hister(parent["hister_session"])
        self.redirect("/machiya/signed-out", [lg.cookie(name, "", 0), lg.cookie(state_name, "", 0),
                                              lg.cookie(out_name, "1", OUT_MAX_AGE)])

    def public_prefs(self):
        """GET/PUT /machiya/api/prefs: Shiori's apps (Authorization: Bearer mhs_…), extensions and scripts (a room
        token, Bearer mht_…, or a Hister token: X-Access-Token or Bearer; this is Hister's own host), the browser's
        own sign-in cookie (a PUT then needs an Origin among the return hosts), and the hosted pages through their
        nginx (proxied(): their room cookie). No CORS: no other site's page may read or write it."""
        auth = self.headers.get_all("Authorization") or []
        tok = self.headers.get_all("X-Access-Token") or []
        if len(auth) > 1 or len(tok) > 1 or (auth and tok):
            if self.command == "PUT":
                self.body()
            return self.json(400, {"error": "one credential"})
        refused = {"error": "sign in", "signin": self.login.s.public_url + "/machiya/signin"}
        if tok:
            value = tok[0].strip()
            if not histerauth.TOKEN_RE.match(value):
                return self.json(401, refused)
            return self.prefs_for("token", value)
        if auth:
            scheme, _, value = auth[0].strip().partition(" ")
            value = value.strip()
            if scheme.lower() != "bearer" or not histerauth.TOKEN_RE.match(value or " "):
                return self.json(401, refused)
            if value.startswith((SID_PREFIX, histerauth.RTOKEN_PREFIX)):
                return self.prefs_for("sid" if value.startswith(SID_PREFIX) else "rtoken", value)
            return self.prefs_for("token", value)
        sid = self.login.own_sid(self.headers)
        if not sid:
            if self.command == "PUT":
                self.body()
            return self.json(401, refused)
        return self.prefs_for("sid", sid, cookie=True)

    def same_origin(self):
        return vsignin.same_origin(self.headers, self.login.s.secure, (self.login.s.public_url,))

    def request_origin(self):
        """The origin this request was made to, from Host: a proxied origin (the hosted pages, through their nginx,
        which passes Host on) or anything else (the helper's own host)."""
        host = (self.headers.get("Host") or "").strip()
        return histerauth.origin_of(("https://" if self.login.s.secure else "http://") + host) if host else None

    def _get(self):
        lg, s = self.login, self.login.s
        path = urlsplit(self.path).path
        origin = self.request_origin()
        if origin in s.proxied:
            return self.proxied_get(origin, path)
        if path in ("/machiya", "/machiya/"):
            return self.redirect("/machiya/sessions")
        if path == "/machiya/healthz":         # the probe's: the helper's own health; Hister's is its own probe's
            try:
                sessions = lg.store.count()
            except (sqlite3.Error, OSError) as ex:
                LOG("hister-login: the state file failed: %s" % type(ex).__name__)
                return self.json(503, {"ok": False, "state": "error", "version": VERSION})
            return self.json(200, {"ok": True, "hister": lg.hister.health(), "sessions": sessions,
                                   "version": VERSION})
        if path.startswith("/machiya/static/"):
            return self.static(path[len("/machiya/static/"):])
        if path == "/machiya/signin":
            return self.signin()
        if path == "/api/oauth/callback":
            return self.callback()
        if path == "/machiya/sessions":
            return self.sessions()
        if path == "/machiya/api/prefs":
            return self.public_prefs()
        if path == "/machiya/signed-out":
            return self.page(200, message_page(self.headers, "Signed Out", "You're signed out of Hister and every "
                                               "Machiya room on this device.", [("/machiya/signin", "Sign In Again")]))
        self.page(404, message_page(self.headers, "Not Found", "There's nothing here.",
                                    [(s.public_url + "/", "Go to Hister")]))

    def static(self, name):
        entry = STATIC.get(name)
        if not entry:
            return self.send(404, [("Content-Type", "text/plain")], b"not found\n")
        try:
            with open(entry[0], "rb") as f:
                data = f.read()
        except OSError:
            return self.send(404, [("Content-Type", "text/plain")], b"not found\n")
        self.send(200, [("Content-Type", entry[1]), ("Cache-Control", "public, max-age=86400"),
                        ("X-Content-Type-Options", "nosniff")], data)

    def signin(self, form=None):
        """GET /machiya/signin, or (form) the confirmed app sign-in's same-origin POST."""
        lg = self.login
        q = self.query() if form is None else form
        app = q.get("app") == "1"
        ret = lg.safe(q.get("return"), app)
        state = q.get("state") or ""
        state = state if not app and histerauth.NONCE_RE.match(state) else ""   # a room's trip: a code goes back
        if q.get("return") and not ret:
            LOG("hister-login: refused a return address (not an allowed host)")
        if app and not ret:
            return self.page(400, message_page(self.headers, "Can't Sign In", "This app's sign-in address isn't one "
                                               "Machiya knows."))
        if app and form is None and (self.headers.get("Sec-Fetch-Site") or "").strip().lower() not in ("none",
                                                                                                    "same-origin"):
            # not the app's own web session, nor this page: a cross-site link must never hand the app's scheme a
            # session (LEAD-3); confirm with a same-origin POST, creating and remembering nothing until then
            return self.page(200, confirm_page(self.headers, ret, (q.get("provider") or "").strip().lower()[:20]))
        session = hister_cookie(self.headers)
        answer = lg.hister.profile(session=session) if session else None
        if answer is None or answer[0] == "out":
            own = lg.own_sid(self.headers)          # this browser's helper id, when Hister's own cookie is gone
            outcome, row = lg.check_sid(own) if own else ("out", None)
            if outcome == "ok" and row["kind"] == "browser":
                session, answer = row["hister_session"], ("ok", row["username"], row["user_id"])
            elif outcome in ("down", "off"):
                return self.unavailable(outcome)
        if answer is not None and answer[0] == "ok":
            location, cookies = lg.finish(session, answer, ret, app, self.headers, state=state)
            cookies.append(lg.return_clear())
            return self.redirect(location, cookies)
        if answer is not None and answer[0] in ("down", "off"):
            return self.unavailable(answer[0])
        if answer is None and lg.hister.health() != "ok":
            return self.unavailable(lg.hister.health_state)
        cookies = [lg.return_cookie(ret, app, state)] if ret else [lg.return_clear()]
        marked = lg.marked(self.headers)
        if marked and lg.store.was_ended(lg.own_sid(self.headers) or sso_value(self.headers, lg.s.sso)):
            # a room's Sign Out ended this browser's id: remember it here, and drop the dead cookie
            cookies += lg.signed_out() + lg.sso_clear()
        provider = (q.get("provider") or "").strip().lower()
        # straight to that sign-in, the return cookie set as for the page: a tap on Shiori's "Sign In with Tailscale"
        # (provider=) always; a room's automatic sign-in (provider=…&auto=1, MACHIYA_SIGNIN_PROVIDER) unless the
        # browser signed out on purpose or the last round trip failed (the marker): then the page, never a loop
        if provider and provider in lg.s.providers and not (q.get("auto") == "1" and marked):
            return self.redirect("/api/oauth?provider=" + provider, cookies)
        self.page(200, signin_page(lg.s, self.headers, ret, app, state=state), [("Set-Cookie", c) for c in cookies])

    def unavailable(self, state):
        text = ("Hister's user accounts are switched off, so nobody can sign in." if state == "user-handling-off"
                else "Hister can't be reached right now. Try again in a minute.")
        self.page(503, message_page(self.headers, "Sign-In Is Unavailable", text, [(self.again(), "Try Again")]))

    def again(self):
        """This request's own path and query, for a Try Again link (0.2.1, sweep LEAD-6): never the raw target, which
        may be `//other.host/…` (routing reads only its path), and never a `//` or `/\\` path another site would be."""
        u = urlsplit(self.path)
        path = u.path if u.path.startswith("/") and u.path[1:2] not in ("/", "\\") else "/machiya/signin"
        return path + ("?" + u.query if u.query else "")

    def callback(self):
        """The OAuth callback shim: Hister's own callback, unchanged, then an id for the new session and the way back
        to where the sign-in started (Hister itself always goes to /)."""
        lg = self.login
        forward = {k: v for k, v in self.headers.items() if k.lower() in (
            "user-agent", "accept", "accept-language", "x-forwarded-for", "x-forwarded-proto", "x-forwarded-host",
            "sec-fetch-site", "sec-fetch-mode", "sec-fetch-dest")}
        session = hister_cookie(self.headers)
        if session:
            forward["Cookie"] = "%s=%s" % (HISTER_COOKIE, session)
        try:
            status, headers, body = lg.hister.request("GET", self.path, forward)
        except OSError:
            return self.unavailable("down")
        out = [(k, v) for k, v in headers if k.lower() not in ("content-length", "connection", "transfer-encoding",
                                                                 "keep-alive", "date", "server")]
        new = None
        for k, v in headers:
            if k.lower() == "set-cookie":
                name, _, rest = v.partition("=")
                value = rest.split(";", 1)[0].strip()
                if name.strip() == HISTER_COOKIE and HISTER_SESSION_RE.match(value) and "max-age=0" not in v.lower():
                    new = value
        if status in (302, 303) and new:
            answer = lg.hister.profile(session=new)
            if answer[0] == "ok":
                ret, app, state = lg.read_return(self.headers.get("Cookie"))
                location, cookies = lg.finish(new, answer, ret, app, self.headers, state=state)
                if not ret:
                    location = dict((k.lower(), v) for k, v in out).get("location", location)
                out = [(k, v) for k, v in out if k.lower() != "location"] + [("Location", location)]
                out += [("Set-Cookie", c) for c in cookies + [lg.return_clear()]]
                return self.send(status, out, body)
        ret, app, state = lg.read_return(self.headers.get("Cookie"))
        if ret:     # a sign-in this helper started (a room's automatic one, a tap) that didn't finish: the page, with
            LOG("hister-login: a sign-in through the provider didn't finish (Hister answered %s)" % status)
            cookies = [v for k, v in out if k.lower() == "set-cookie"]          # Hister's own, for its host
            cookies += [lg.return_clear(), lg.cookie(lg.s.out, "failed", FAILED_MAX_AGE)]
            return self.page(401, signin_page(lg.s, self.headers, ret, app, error="Sign in with %s didn't work. Try "
                                              "again, or sign in with your password." % lg.s.oidc_label, state=state),
                             [("Set-Cookie", c) for c in cookies])
        self.send(status, out, body)

    def sessions(self, new_token=None, status=200):
        lg = self.login
        sid = lg.own_sid(self.headers)
        outcome, row = lg.check_sid(sid) if sid else ("out", None)
        if outcome in ("down", "off"):
            return self.unavailable("user-handling-off" if outcome == "off" else "down")
        if outcome != "ok":
            return self.redirect("/machiya/signin?" + urlencode({"return": lg.s.public_url + "/machiya/sessions"}))
        done = {"one": "Signed that session out.", "token": "Revoked that token."}.get(self.query().get("done"), "")
        rows = lg.store.of_user(row["user_id"])
        for r in rows:
            r["rooms"] = lg.store.rooms_of(r["sid_hash"])
        self.page(status, sessions_page(lg.s, self.headers, row, rows, done, lg.store.tokens_of(row["username"]),
                                        new_token))

    def _post(self):
        path = urlsplit(self.path).path
        origin = self.request_origin()
        if origin in self.login.s.proxied:
            return self.proxied_post(origin, path)
        if path == "/machiya/signin":
            return self.signin_post()
        if path == "/machiya/signout":
            return self.signout()
        if path == "/machiya/sessions":
            return self.revoke()
        if path == "/machiya/api/app-session":
            return self.app_session()
        self.json(404, {"error": "not found"})

    def signin_post(self):
        """POST /machiya/signin (0.2.1): the confirmation page's Continue for the app sign-in. Same-origin only; the app
        flow only (a browser's own sign-in is a GET)."""
        data = self.body()
        if data is None:
            return self.json(413, {"error": "request body too large"})
        if not self.same_origin():
            return self.page(403, message_page(self.headers, "Refused", "That must come from the sign-in page."))
        try:
            form = {k: v[0] for k, v in parse_qs(data.decode("utf-8"), max_num_fields=8).items()}
        except (ValueError, UnicodeError):
            return self.json(400, {"error": "unreadable form"})
        if form.get("app") != "1":
            # a password form posted here: the 0.4.0 page's direct submit (a cached copy; today's page has no form). It
            # carries no fields, so show the page again, same return address, with what to do
            q = self.query()
            ret = self.login.safe(q.get("return"), False)
            state = q.get("state") or ""
            state = state if histerauth.NONCE_RE.match(state) else ""
            return self.page(200, signin_page(self.login.s, self.headers, ret, False, state=state,
                                              error="Press Sign In to finish signing in."))
        return self.signin(form)

    def signout(self):
        """POST /machiya/signout: a browser (same-origin, its machiya_sso and hister cookies) or an app
        (Authorization: Bearer mhs_…): Hister's logout for the session, and every id on it gone."""
        lg = self.login
        data = self.body()
        if data is None:
            return self.json(413, {"error": "request body too large"})
        auth = (self.headers.get("Authorization") or "").strip()
        if auth.lower().startswith("bearer "):
            row = lg.store.get(auth[7:].strip())
            if row:
                lg.end_hister(row["hister_session"])
            return self.send(204, [("Cache-Control", "no-store")])
        if not self.same_origin():
            return self.page(403, message_page(self.headers, "Refused", "A sign-out must come from this page."))
        cookies, ended = [], set()
        row = lg.store.get(lg.own_sid(self.headers))
        if row:
            cookies += lg.end_hister(row["hister_session"])
            ended.add(row["hister_hash"])
        own = hister_cookie(self.headers)
        if own and sha(own) not in ended:
            cookies += lg.end_hister(own)
        cookies = [c for c in cookies if c.split("=", 1)[0].strip() == HISTER_COOKIE][:1] \
            or [lg.cookie(HISTER_COOKIE, "", 0)]
        self.redirect("/machiya/signed-out", lg.sso_clear() + lg.signed_out() + cookies)

    def revoke(self):
        lg = self.login
        data = self.body()
        if data is None:
            return self.json(413, {"error": "request body too large"})
        if not self.same_origin():
            return self.page(403, message_page(self.headers, "Refused", "That must come from the sessions page."))
        outcome, me = lg.check_sid(lg.own_sid(self.headers) or "")
        if outcome != "ok":
            return self.redirect("/machiya/sessions")
        try:
            lists = parse_qs(data.decode("utf-8"), max_num_fields=24)
        except (ValueError, UnicodeError):
            return self.json(400, {"error": "unreadable form"})
        form = {k: v[0] for k, v in lists.items()}
        if form.get("do") == "token-new":
            return self.token_new(me, form.get("label", ""), lists.get("room", []))
        if form.get("do") == "token-revoke":
            lg.store.revoke_token(form.get("token", ""), me["username"])
            return self.redirect("/machiya/sessions?done=token")
        if form.get("do") == "all":
            seen = set()
            for r in lg.store.of_user(me["user_id"]):
                if r["hister_hash"] not in seen:
                    seen.add(r["hister_hash"])
                    lg.end_hister(r["hister_session"])
            return self.redirect("/machiya/signed-out", lg.sso_clear() + lg.signed_out()
                                 + [lg.cookie(HISTER_COOKIE, "", 0)])
        try:
            target = lg.store.by_rowid(int(form.get("id", "")), me["user_id"])
        except ValueError:
            target = None
        if target:
            lg.end_hister(target["hister_session"])
            if target["hister_hash"] == me["hister_hash"]:
                return self.redirect("/machiya/signed-out", lg.sso_clear() + lg.signed_out()
                                     + [lg.cookie(HISTER_COOKIE, "", 0)])
        self.redirect("/machiya/sessions?done=one")

    def token_new(self, me, label, rooms):
        """New Token on the sessions page: a room token (mht_) for the rooms ticked, shown once on the answer."""
        lg = self.login
        label = re.sub(r"[\x00-\x1f\x7f]", "", label).strip()[:80]
        audiences = [lg.s.rooms[r] for r in rooms if r in lg.s.rooms]
        if not label or not audiences:
            return self.sessions(new_token={"error": "Name the token and tick at least one room."}, status=400)
        value = histerauth.RTOKEN_PREFIX + b64e(secrets.token_bytes(32))
        lg.store.add_token(value, me["username"], label, sorted(set(audiences)), user_id=me["user_id"])
        LOG("hister-login: a room token was made on the sessions page (%s)" % ", ".join(sorted(set(rooms))))
        self.sessions(new_token={"value": value, "label": label})

    def app_session(self):
        """POST /machiya/api/app-session {"hister": S, "label": "iPhone"}: an app that signed in to Hister with a
        password trades its own Hister session for an id (kind app). No cookie is involved."""
        lg = self.login
        data = self.body()
        if data is None:
            return self.json(413, {"error": "request body too large"})
        if (self.headers.get("Content-Type") or "").split(";")[0].strip().lower() != "application/json":
            return self.json(415, {"error": "send JSON"})
        try:
            req = json.loads(data)
            session, label = req.get("hister"), req.get("label", "")
        except (ValueError, AttributeError):
            return self.json(400, {"error": "unreadable JSON"})
        if not isinstance(session, str) or not HISTER_SESSION_RE.match(session) or not isinstance(label, str):
            return self.json(400, {"error": "hister must be a Hister session"})
        answer = lg.hister.profile(session=session)
        if answer[0] == "out":
            return self.json(401, {"error": "signed out"})
        if answer[0] != "ok":
            return self.json(503, {"error": "sign-in is unavailable"})
        sid = lg.store.create(session, answer[1], answer[2], "app", re.sub(r"[\x00-\x1f\x7f]", "", label)[:80]
                              or "Shiori app")
        self.json(200, {"sid": sid, "username": answer[1], "user_id": answer[2]})


class Server(socketserver.ThreadingMixIn, http.server.HTTPServer):
    daemon_threads = True
    allow_reuse_address = True


def housekeeping(login, stop):
    last_cleanup = 0.0
    while not stop.wait(60):
        try:
            if login.store.pending() and login.hister.health() == "ok":
                login.retry_pending()
            if time.monotonic() - last_cleanup >= 3600:
                last_cleanup = time.monotonic()
                n = login.store.cleanup()
                if n:
                    LOG("hister-login: removed %d expired id(s)" % n)
        except Exception as ex:
            LOG("hister-login: housekeeping: %s" % type(ex).__name__)


def serve(login, bind, port, internal_port):
    pub = type("PublicH", (Public,), {"login": login})
    internal = type("InternalH", (Internal,), {"login": login})
    servers = [Server((bind, port), pub), Server((bind, internal_port), internal)]
    for srv in servers:
        threading.Thread(target=srv.serve_forever, daemon=True).start()
    return servers


# -- the prefs command (docs/contracts/prefs.md, "Migration") ---------------------------------------------------------

def prefs_cli(argv, env=None, out=sys.stdout):
    """`prefs import|show|delete --user NAME`: run on the helper's host (in its container), against its prefs file.
    import: the rooms' old prefs.sqlite3 files (Kura's, Niwa's, Konbini's, landing's), read-only; for each key the value
    with the newest `updated` across them wins, and only when it is newer than what the account already holds (so a
    second run changes nothing). A file's rows are the user's when their principal is hi:<sha256(NAME)>, the
    tailscale_uid of a --tailscale login, or a --principal given; a file with one principal and none of those is taken
    as that one (said so). -> exit code."""
    env = os.environ if env is None else env
    ap = argparse.ArgumentParser(prog="hister_login.py prefs")
    sub = ap.add_subparsers(dest="what", required=True)
    imp = sub.add_parser("import", help="seed an account from the rooms' old prefs files")
    imp.add_argument("--user", required=True, help="the Hister username")
    imp.add_argument("--tailscale", action="append", default=[], help="a Tailscale login the rooms keyed it by")
    imp.add_argument("--principal", action="append", default=[], help="a principal key to take as the user's")
    imp.add_argument("--dry-run", action="store_true")
    imp.add_argument("files", nargs="+")
    for name, text in (("show", "what the account holds"), ("delete", "forget the account's settings")):
        p = sub.add_parser(name, help=text)
        p.add_argument("--user", required=True)
    a = ap.parse_args(argv)
    path = prefs_path(env)
    key = user_key(a.user)
    store = vprefs.Store(path)
    say = lambda *x: print(*x, file=out)                    # noqa: E731
    if a.what == "show":
        rev, values, updated = store.snapshot(key)
        say("%s (%s), rev %d, %d key(s), in %s" % (a.user, key, rev, len(values), path))
        for k in sorted(values):
            say("  %-24s %-20s %s UTC" % (k, values[k][:60], time.strftime("%Y-%m-%d %H:%M", time.gmtime(updated[k]))))
        return 0
    if a.what == "delete":
        say("%s: %d key(s) deleted from %s" % (a.user, store.delete(key), path))
        return 0
    from vaultkit.identity import tailscale_uid
    mine = {key} | {tailscale_uid(t) for t in a.tailscale} | set(a.principal)
    sources = []
    for f in a.files:
        try:
            rows = vprefs.read_rows(f)
        except sqlite3.Error as ex:
            say("%s: not a prefs file (%s)" % (f, ex))
            return 1
        found = sorted({r[0] for r in rows})
        take = [r for r in rows if r[0] in mine]
        if not take and len(found) == 1:
            say("%s: one principal (%s), taken as %s" % (f, found[0], a.user))
            take = rows
        elif not take and found:
            say("%s: %d principals (%s), none of them %s: name one with --principal or --tailscale"
                % (f, len(found), ", ".join(found), a.user))
            return 1
        sources.append((f, [(r[1], r[2], r[3]) for r in take]))
    values, stamps, source, refused = vprefs.merge(sources)
    have = store.snapshot(key)
    for k in sorted(values):
        older = k in have[2] and have[2][k] >= stamps[k]
        say("  %-24s %-20s from %s (%s UTC)%s" % (k, values[k][:60], source[k],
                                                time.strftime("%Y-%m-%d %H:%M", time.gmtime(stamps[k])),
                                                "; the account's is newer: kept" if older else ""))
    for r in refused:
        say("  skipped (not in the schema): %s" % r)
    if a.dry_run:
        say("dry run: nothing written")
        return 0
    rev, after, _ = store.write(key, values, stamps=stamps, only_newer=True)
    say("%s (%s): rev %d, %d key(s) in %s" % (a.user, key, rev, len(after), path))
    return 0


# -- the token command (0.3.0): room tokens for headless callers ----------------------------------------------------

def token_cli(argv, env=None, out=sys.stdout):
    """`token mint|add|list|revoke`, run where the helper runs (its sessions file, HISTER_LOGIN_DB). A token's value
    never goes in argv: mint prints it once (or writes it to --out, 0600), add reads it from --from-file.
    --rooms takes room names from MACHIYA_ROOMS (machiya = landing) or origins (https://host[:port]). -> exit code."""
    env = os.environ if env is None else env
    ap = argparse.ArgumentParser(prog="hister_login.py token")
    sub = ap.add_subparsers(dest="what", required=True)
    for name, text in (("mint", "make a room token (printed once, or --out FILE)"),
                       ("add", "register a token made elsewhere (--from-file FILE)")):
        p = sub.add_parser(name, help=text)
        p.add_argument("--user", required=True, help="the Hister username the token acts as")
        p.add_argument("--label", required=True, help="who holds it (pm on the laptop, machiya-mcp, …)")
        p.add_argument("--rooms", required=True, help="kura,niwa,konbini,machiya or origins, comma-separated")
        p.add_argument("--days", type=int, default=0, help="expire after N days (default: never)")
        if name == "mint":
            p.add_argument("--out", help="write the token to FILE (0600) instead of printing it")
        else:
            p.add_argument("--from-file", required=True, dest="src")
    sub.add_parser("list", help="every token: id, user, label, rooms, made, last used")
    p = sub.add_parser("revoke", help="revoke a token by its id (as listed)")
    p.add_argument("id")
    a = ap.parse_args(argv)
    say = lambda *x: print(*x, file=out)                    # noqa: E731
    store = Store((env.get("HISTER_LOGIN_DB") or "/data/hister-login.sqlite3").strip())
    named = token_rooms(env)
    if a.what == "list":
        for t in store.tokens_of():
            say("%s  %-12s %-28s %s  made %s  last used %s%s" % (
                t["id"], t["username"], t["label"][:28], ",".join(t["audiences"]),
                time.strftime("%Y-%m-%d", time.gmtime(t["created_at"])),
                time.strftime("%Y-%m-%d", time.gmtime(t["last_used"])) if t["last_used"] else "never",
                "  expires %s" % time.strftime("%Y-%m-%d", time.gmtime(t["expires_at"])) if t["expires_at"] else ""))
        return 0
    if a.what == "revoke":
        n = store.revoke_token(a.id.strip().lower())
        say("revoked %s" % a.id if n else "no token %s" % a.id)
        return 0 if n else 1
    audiences = []
    for r in [x.strip() for x in a.rooms.split(",") if x.strip()]:
        o = named.get(r) if r in named else histerauth.origin_of(r) if "://" in r else None
        if not o or (r not in named and urlsplit(r).path not in ("", "/")):
            say("%s: not a room in MACHIYA_ROOMS (%s) nor an origin" % (r, ", ".join(sorted(named)) or "none"))
            return 1
        audiences.append(o)
    if not audiences or not a.label.strip() or not a.user.strip():
        say("a token needs a user, a label and at least one room")
        return 1
    if a.what == "add":
        try:
            with open(a.src, encoding="utf-8") as f:
                value = f.readline().strip()
        except OSError as ex:
            say("%s: %s" % (a.src, ex.strerror))
            return 1
        if not histerauth.RTOKEN_RE.match(value):
            say("%s: not a room token (mht_ and 43 characters)" % a.src)
            return 1
        if store.get_token(value) is not None:
            say("already registered (%s)" % sha(value)[:12])
            return 0
    else:
        value = histerauth.RTOKEN_PREFIX + b64e(secrets.token_bytes(32))
    tid = store.add_token(value, a.user.strip(), a.label.strip(), sorted(set(audiences)), days=max(a.days, 0))
    if a.what == "mint":
        if a.out:
            fd = os.open(a.out, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            with os.fdopen(fd, "w") as f:
                f.write(value + "\n")
            os.chmod(a.out, 0o600)
            say("token %s for %s (%s) written to %s" % (tid, a.user, ",".join(sorted(set(audiences))), a.out))
        else:
            say(value)
            say("token %s for %s (%s): copy it now, it isn't shown again" % (tid, a.user,
                                                                           ",".join(sorted(set(audiences)))))
    else:
        say("token %s for %s (%s) registered" % (tid, a.user, ",".join(sorted(set(audiences)))))
    return 0


def main():
    if sys.argv[1:2] == ["token"]:
        os.umask(0o077)
        sys.exit(token_cli(sys.argv[2:]))
    if sys.argv[1:2] == ["prefs"]:
        os.umask(0o077)
        sys.exit(prefs_cli(sys.argv[2:]))
    os.umask(0o077)
    s = Settings()
    login = Login(s)
    LOG("hister-login %s: public :%d, internal :%d, Hister %s, return hosts %s, cookie %s, domain %s"
        % (VERSION, s.port, s.internal_port, s.hister_url, ",".join(s.return_hosts), s.sso,
           s.cookie_domain or "(none)"))
    servers = serve(login, s.bind, s.port, s.internal_port)
    stop = threading.Event()
    # PID 1 in its container with no init: without a handler, SIGTERM does nothing and `docker stop` ends in a
    # SIGKILL after 10 s (0.4.2). Stop the housekeeping loop, finish the servers, exit 0.
    signal.signal(signal.SIGTERM, lambda signum, frame: stop.set())
    try:
        housekeeping(login, stop)
    except KeyboardInterrupt:
        stop.set()
    for srv in servers:
        srv.shutdown()
        srv.server_close()
    LOG("hister-login: stopped")


if __name__ == "__main__":
    main()
