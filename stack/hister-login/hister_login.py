"""hister-login (machiya-kobo/machiya stack/hister-login): Hister's users as the one sign-in for every Machiya room.

Hister keeps its session cookie (`hister`) on its own host. This helper sits on that host too (Tailscale Serve sends
it /machiya/… and /api/oauth/callback; everything else goes to Hister) and turns a Hister session into an opaque id,
`machiya_sso=mhs_…`, on the tailnet's shared domain. The rooms ask it about that id (or a Hister token) over the
internal network; it asks Hister's GET /api/profile and caches the answer for 30 s. Signing out anywhere ends the
Hister session and every id on it. Hister itself is not patched.

Two ports:
  public   (HISTER_LOGIN_PORT, 8080): GET /machiya/signin, GET /api/oauth/callback (a pass-through shim),
           POST /machiya/signout, GET/POST /machiya/sessions, POST /machiya/api/app-session, GET /machiya/healthz
           (the probe's: 200 while the helper and its state work, with Hister's state as "hister"; 503 only when the
           helper's own state fails), GET/PUT /machiya/api/prefs (0.2.0: the account's settings, for Shiori's apps,
           extensions and hosted pages), /machiya/static/…
  internal (HISTER_LOGIN_INTERNAL_PORT, 8081; never routed by Serve): GET /v1/check, POST /v1/signout,
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
import socketserver
import sqlite3
import sys
import threading
import time
from urllib.parse import parse_qs, quote, urlencode, urlsplit

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

from vaultkit import histerauth, prefs as vprefs, shell, signin as vsignin   # noqa: E402

VERSION = "0.2.1"
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
        self.out = self.sso + "_out"            # the signed-out marker: no automatic sign-in (histerauth sets it too)
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
        self.return_hosts = sorted(set(hosts) | {own})
        self.app_schemes = [s.strip().lower() for s in (env.get("HISTER_LOGIN_APP_SCHEMES") or "shiori").split(",")
                            if s.strip()]
        self.cookie_domain = (env.get("MACHIYA_COOKIE_DOMAIN") or "").strip().lstrip(".")
        if not self.cookie_domain:
            LOG("hister-login: MACHIYA_COOKIE_DOMAIN is unset: machiya_sso stays on this host, so no room sees it")
        self.db = (env.get("HISTER_LOGIN_DB") or "/data/hister-login.sqlite3").strip()
        self.prefs_db = prefs_path(env)
        self.bind = (env.get("HISTER_LOGIN_BIND") or "0.0.0.0").strip()
        self.port = int(env.get("HISTER_LOGIN_PORT") or 8080)
        self.internal_port = int(env.get("HISTER_LOGIN_INTERNAL_PORT") or 8081)
        self.providers = [p.strip().lower() for p in (env.get("HISTER_LOGIN_PROVIDERS") or "").split(",")
                          if p.strip()]
        self.oidc_label = (env.get("HISTER_LOGIN_OIDC_LABEL") or "Tailscale").strip()


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
"""


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
            if path != ":memory:":
                self.db.execute("PRAGMA journal_mode=WAL")
            self.db.execute("PRAGMA busy_timeout=3000")
            self.db.executescript(SCHEMA)
            self.db.execute("PRAGMA user_version=1")

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
            self.q("DELETE FROM sessions WHERE sid_hash = ?", (row["sid_hash"],))
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

    def drop_hister(self, hister_hash):
        """Delete every id on one Hister session; -> how many."""
        with self.lock:
            return self.db.execute("DELETE FROM sessions WHERE hister_hash = ?", (hister_hash,)).rowcount

    def of_user(self, user_id):
        return [dict(r) for r in self.q("SELECT rowid, * FROM sessions WHERE user_id = ? AND expires_at > ? "
                                        "ORDER BY last_seen DESC", (user_id, now()))]

    def by_rowid(self, rowid, user_id):
        rows = self.q("SELECT rowid, * FROM sessions WHERE rowid = ? AND user_id = ?", (rowid, user_id))
        return dict(rows[0]) if rows else None

    def cleanup(self):
        with self.lock:
            return self.db.execute("DELETE FROM sessions WHERE expires_at <= ?", (now(),)).rowcount

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

    def who(self, kind, value):
        """A credential -> ("ok", username) | ("out",) | ("off",) | ("down",): the same checks as /v1/check."""
        if kind == "sid":
            outcome, row = self.check_sid(value)
            return ("ok", row["username"]) if outcome == "ok" else (outcome,)
        answer = self.check_token(value)
        return ("ok", answer[1]) if answer[0] == "ok" else (answer[0],)

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

    def end_hister(self, hister_session):
        """Sign one Hister session out and drop every id on it. -> Set-Cookie values from Hister's logout. When Hister
        can't be reached the ids still go (the rooms see 401 at once) and the logout is retried later."""
        n = self.store.drop_hister(sha(hister_session))
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
        return self.cookie(self.s.sso, sid, CAP_DAYS * 86400, domain=True)

    def sso_clear(self):
        out = [self.cookie(self.s.sso, "", 0, domain=True)]
        if self.s.cookie_domain:
            out.append(self.cookie(self.s.sso, "", 0))
        return out

    def signed_out(self):
        """A deliberate sign-out: the marker that stops the automatic sign-in (?provider=) until the next sign-in, on
        the shared domain so the rooms' own sign-outs set the same one."""
        return [self.cookie(self.s.out, "1", OUT_MAX_AGE, domain=True)]

    def marker_clear(self):
        out = [self.cookie(self.s.out, "", 0, domain=True)]
        if self.s.cookie_domain:
            out.append(self.cookie(self.s.out, "", 0))
        return out

    def marked(self, headers):
        """The browser signed out on purpose, or the last automatic round trip failed: show the page."""
        return bool(histerauth.HisterAuth.cookie_values(headers.get("Cookie"), self.s.out))

    def return_cookie(self, ret, app):
        value = b64e(json.dumps({"r": ret, "a": 1 if app else 0}, separators=(",", ":")).encode())
        return self.cookie(RETURN_COOKIE, value, RETURN_MAX_AGE)

    def read_return(self, cookie_header):
        """The remembered (return, app) from machiya_return, checked again now; (None, False) if none holds."""
        for value in histerauth.HisterAuth.cookie_values(cookie_header, RETURN_COOKIE)[:2]:
            try:
                data = json.loads(b64d(value))
                app = data.get("a") == 1
                ret = self.safe(data.get("r"), app)
                if ret:
                    return ret, app
            except (ValueError, TypeError, AttributeError, UnicodeError):
                continue
        return None, False

    def safe(self, ret, app=False):
        ret = histerauth.safe_return(ret, self.s.return_hosts, self.s.app_schemes if app else ())
        if app and ret and ret.lower().startswith("https://"):
            return None                 # the app flow returns to the app, never to a page
        return ret

    def finish(self, hister_session, answer, ret, app, headers, label=""):
        """A good Hister session: an id for it, and where to go. -> (location, cookies)."""
        username, user_id = answer[1], answer[2]
        if app:
            sid = self.store.create(hister_session, username, user_id, "app", label or "Shiori app")
            return ret + "#" + urlencode({"sid": sid, "hister": hister_session}), []
        cookies = []
        for value in histerauth.HisterAuth.cookie_values(headers.get("Cookie"), self.s.sso)[:4]:
            row = self.store.get(value)
            if row and row["hister_hash"] == sha(hister_session):
                break                   # this browser already holds an id for this session
        else:
            sid = self.store.create(hister_session, username, user_id, "browser", device_label(headers))
            cookies.append(self.sso_cookie(sid))
        if self.marked(headers):                # signed in again: the automatic sign-in is back
            cookies += self.marker_clear()
        return ret or self.s.public_url + "/", cookies


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
    page = shell.page(ctx, "hister", title, shell.header("hister", [], "", {}, subtitle="Machiya", settings=False)
                      + body, links={}, manifest=False, scripts=scripts)
    return page.replace('"/static/', '"/machiya/static/').encode()


def signin_page(s, headers, ret, app, error=""):
    nxt = "/machiya/signin?" + urlencode([("return", ret or "")] + ([("app", "1")] if app else []))
    oauth = ""
    if "oidc" in s.providers:
        oauth = ('<p class="actions"><a class="button" href="/api/oauth?provider=oidc">Sign in with %s</a></p>'
                 % e(s.oidc_label))
    alert = '<p class="signin-error" role="alert" data-error%s>%s</p>' % ("" if error else " hidden", e(error))
    body = (
        '<main class="signin"><h1>Sign In</h1>%s'
        '<form class="group" id="hister-signin" method="post" action="/machiya/signin" data-next="%s">'
        '<label class="item"><span>Name</span><input name="username" required maxlength="64" autofocus '
        'autocomplete="username" autocapitalize="none" autocorrect="off" spellcheck="false"></label>'
        '<label class="item"><span>Password</span><input type="password" name="password" required maxlength="1024" '
        'autocomplete="current-password"></label>'
        '<button type="submit">Sign In</button></form>'
        '<div class="empty">%s</div>'
        '<p class="footnote">One sign-in for Hister and every Machiya room on this device, until you sign out.</p>'
        '<noscript><p class="signin-error">The password form needs JavaScript.</p></noscript>'
        '</main>'
    ) % (alert, e(nxt), oauth)
    return render(headers, "Sign In · Machiya", body, ["/static/signin.js?v=%s" % VERSION])


def message_page(headers, heading, text, actions=()):
    return render(headers, heading + " · Machiya", shell.message(heading, text, actions))


def sessions_page(s, headers, me, rows, done=""):
    items = []
    for r in rows:
        this = r["sid_hash"] == me["sid_hash"]
        when = time.strftime("%Y-%m-%d %H:%M", time.gmtime(r["last_seen"]))
        items.append(
            '<div class="item"><span>%s<br><small class="value">%s · last seen %s UTC%s</small></span>'
            '<form method="post" action="/machiya/sessions"><input type="hidden" name="id" value="%d">'
            '<button type="submit" name="do" value="one">Sign&nbsp;Out</button></form></div>'
            % (e(r["label"] or r["kind"]), "app" if r["kind"] == "app" else "browser", e(when),
               " · this device" if this else "", r["rowid"]))
    note = '<div class="machiya-banner" role="status">%s</div>' % e(done) if done else ""
    body = (
        '<main class="settings sessions"><h1 class="sechead">Signed In as %s</h1>%s'
        '<h2>Sessions</h2><div class="group">%s</div>'
        '<p class="footnote">Every browser and app signed in through Hister. Signing one out ends it in Hister and in '
        'every room within a minute. Hister tokens (extensions, scripts) are separate.</p>'
        '<h2>Everywhere</h2><div class="group"><div class="item"><span>Sign out every session above</span>'
        '<form method="post" action="/machiya/sessions"><button type="submit" name="do" value="all">'
        'Sign&nbsp;Out Everywhere</button></form></div></div>'
        '</main>'
    ) % (e(me["username"]), note, "".join(items) or '<div class="item"><span>None</span></div>')
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

    def prefs_for(self, kind, value, cookie=False):
        """GET/PUT of the account's settings (docs/contracts/prefs.md) for one credential: the key is only ever the
        user that credential proves. A PUT carried by the sign-in cookie must come from one of the return hosts' pages
        (the hosted pages, through their nginx). Values are never logged."""
        lg = self.login
        if self.command == "PUT":
            body = vsignin.read_body(self.headers, self.rfile, vprefs.MAX_BODY)
            if body is None:
                self.close_connection = True
                return self.json(413, {"error": "request body too large"})
            self._body_read = True
        elif self.command not in ("GET", "HEAD"):
            return self.json(405, {"error": "GET or PUT"}, [("Allow", "GET, PUT")])
        who = lg.who(kind, value)
        if who[0] == "out":
            return self.json(401, {"error": "sign in", "signin": lg.s.public_url + "/machiya/signin"})
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
            if cookie and not vsignin.same_origin(self.headers, lg.s.secure, self.prefs_origins()):
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

    def internal_prefs(self):
        """GET/PUT /v1/prefs: a room's /api/prefs, with the caller's own credential (exactly one, as /v1/check)."""
        sid = self.headers.get("X-Machiya-Session")
        token = self.headers.get("X-Access-Token")
        if (sid is None) == (token is None) or len(self.headers.get_all("X-Machiya-Session") or []) > 1 \
                or len(self.headers.get_all("X-Access-Token") or []) > 1:
            return self.json(400, {"reason": "one credential"})
        return self.prefs_for("sid" if sid is not None else "token", (sid if sid is not None else token).strip())

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
            sid = self.headers.get("X-Machiya-Session")
            token = self.headers.get("X-Access-Token")
            if (sid is None) == (token is None):
                return self.json(400, {"reason": "one credential"})
            if sid is not None:
                outcome, row = lg.check_sid(sid.strip())
                if outcome == "ok":
                    return self.json(200, {"username": row["username"], "user_id": row["user_id"], "via": "session",
                                           "kind": row["kind"], "prefs": lg.shared_prefs(row["username"])})
            else:
                answer = lg.check_token(token.strip())
                outcome = answer[0]
                if outcome == "ok":
                    return self.json(200, {"username": answer[1], "user_id": answer[2], "via": "token",
                                           "prefs": lg.shared_prefs(answer[1])})
            if outcome == "out":
                return self.json(401, {"reason": "signed-out"})
            return self.json(503, {"reason": "user-handling-off" if outcome == "off" else "hister-unavailable"})
        if path == "/v1/nginx":
            sid = (self.headers.get("X-Machiya-Session") or "").strip()
            outcome, row = lg.check_sid(sid) if sid else ("out", None)
            if outcome == "ok":
                return self.send(200, [("X-Hister-Cookie", "%s=%s" % (HISTER_COOKIE, row["hister_session"])),
                                       ("X-Hister-User", row["username"]), ("Cache-Control", "no-store")])
            return self.send(401 if outcome == "out" else 503, [("Cache-Control", "no-store")])
        if path == "/v1/prefs":
            return self.internal_prefs()
        self.json(404, {"error": "not found"})

    def _post(self):
        if urlsplit(self.path).path != "/v1/signout":
            return self.json(404, {"error": "not found"})
        self.body()
        row = self.login.store.get((self.headers.get("X-Machiya-Session") or "").strip())
        if row:
            self.login.end_hister(row["hister_session"])
        self.send(204, [("Cache-Control", "no-store")])


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
        self.public_prefs()

    def public_prefs(self):
        """GET/PUT /machiya/api/prefs: Shiori's apps (Authorization: Bearer mhs_…), extensions and scripts (a Hister
        token: X-Access-Token or Bearer), the hosted pages through their nginx (the sign-in cookie; a PUT then needs
        an Origin among the return hosts). No CORS: no other site's page may read or write it."""
        auth = self.headers.get_all("Authorization") or []
        tok = self.headers.get_all("X-Access-Token") or []
        if len(auth) > 1 or len(tok) > 1 or (auth and tok):
            return self.json(400, {"error": "one credential"})
        if tok:
            value = tok[0].strip()
            if not histerauth.TOKEN_RE.match(value):
                return self.json(401, {"error": "sign in", "signin": self.login.s.public_url + "/machiya/signin"})
            return self.prefs_for("token", value)
        if auth:
            scheme, _, value = auth[0].strip().partition(" ")
            value = value.strip()
            if scheme.lower() != "bearer" or not histerauth.TOKEN_RE.match(value or " "):
                return self.json(401, {"error": "sign in", "signin": self.login.s.public_url + "/machiya/signin"})
            if value.startswith(SID_PREFIX):
                return self.prefs_for("sid", value)
            return self.prefs_for("token", value)
        sid = sso_value(self.headers, self.login.s.sso)
        if not sid:
            if self.command == "PUT":
                self.body()
            return self.json(401, {"error": "sign in", "signin": self.login.s.public_url + "/machiya/signin"})
        return self.prefs_for("sid", sid, cookie=True)

    def same_origin(self):
        return vsignin.same_origin(self.headers, self.login.s.secure, (self.login.s.public_url,))

    def _get(self):
        lg, s = self.login, self.login.s
        path = urlsplit(self.path).path
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

    def signin(self):
        lg = self.login
        q = self.query()
        app = q.get("app") == "1"
        ret = lg.safe(q.get("return"), app)
        if q.get("return") and not ret:
            LOG("hister-login: refused a return address (not an allowed host)")
        if app and not ret:
            return self.page(400, message_page(self.headers, "Can't Sign In", "This app's sign-in address isn't one "
                                               "Machiya knows."))
        session = hister_cookie(self.headers)
        if session:
            answer = lg.hister.profile(session=session)
            if answer[0] == "ok":
                location, cookies = lg.finish(session, answer, ret, app, self.headers)
                cookies.append(lg.cookie(RETURN_COOKIE, "", 0))
                return self.redirect(location, cookies)
            if answer[0] in ("down", "off"):
                return self.unavailable(answer[0])
        elif lg.hister.health() != "ok":
            return self.unavailable(lg.hister.health_state)
        cookies = [lg.return_cookie(ret, app)] if ret else [lg.cookie(RETURN_COOKIE, "", 0)]
        provider = (q.get("provider") or "").strip().lower()
        # straight to that sign-in, the return cookie set as for the page: a tap on Shiori's "Sign In with Tailscale"
        # (provider=) always; a room's automatic sign-in (provider=…&auto=1, MACHIYA_SIGNIN_PROVIDER) unless the
        # browser signed out on purpose or the last round trip failed (the marker): then the page, never a loop
        if provider and provider in lg.s.providers and not (q.get("auto") == "1" and lg.marked(self.headers)):
            return self.redirect("/api/oauth?provider=" + provider, cookies)
        self.page(200, signin_page(lg.s, self.headers, ret, app), [("Set-Cookie", c) for c in cookies])

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
                ret, app = lg.read_return(self.headers.get("Cookie"))
                location, cookies = lg.finish(new, answer, ret, app, self.headers)
                if not ret:
                    location = dict((k.lower(), v) for k, v in out).get("location", location)
                out = [(k, v) for k, v in out if k.lower() != "location"] + [("Location", location)]
                out += [("Set-Cookie", c) for c in cookies + [lg.cookie(RETURN_COOKIE, "", 0)]]
                return self.send(status, out, body)
        ret, app = lg.read_return(self.headers.get("Cookie"))
        if ret:     # a sign-in this helper started (a room's automatic one, a tap) that didn't finish: the page, with
            LOG("hister-login: a sign-in through the provider didn't finish (Hister answered %s)" % status)
            cookies = [v for k, v in out if k.lower() == "set-cookie"]          # Hister's own, for its host
            cookies += [lg.cookie(RETURN_COOKIE, "", 0), lg.cookie(lg.s.out, "failed", FAILED_MAX_AGE)]
            return self.page(401, signin_page(lg.s, self.headers, ret, app, error="Sign in with %s didn't work. Try "
                                              "again, or sign in with your password." % lg.s.oidc_label),
                             [("Set-Cookie", c) for c in cookies])
        self.send(status, out, body)

    def sessions(self):
        lg = self.login
        sid = sso_value(self.headers, self.login.s.sso)
        outcome, row = lg.check_sid(sid) if sid else ("out", None)
        if outcome in ("down", "off"):
            return self.unavailable("user-handling-off" if outcome == "off" else "down")
        if outcome != "ok":
            return self.redirect("/machiya/signin?" + urlencode({"return": lg.s.public_url + "/machiya/sessions"}))
        done = "Signed that session out." if self.query().get("done") == "one" else ""
        self.page(200, sessions_page(lg.s, self.headers, row, lg.store.of_user(row["user_id"]), done))

    def _post(self):
        path = urlsplit(self.path).path
        if path == "/machiya/signout":
            return self.signout()
        if path == "/machiya/sessions":
            return self.revoke()
        if path == "/machiya/api/app-session":
            return self.app_session()
        self.json(404, {"error": "not found"})

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
        row = lg.store.get(sso_value(self.headers, self.login.s.sso))
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
        outcome, me = lg.check_sid(sso_value(self.headers, self.login.s.sso) or "")
        if outcome != "ok":
            return self.redirect("/machiya/sessions")
        try:
            form = {k: v[0] for k, v in parse_qs(data.decode("utf-8"), max_num_fields=4).items()}
        except (ValueError, UnicodeError):
            return self.json(400, {"error": "unreadable form"})
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


def main():
    if sys.argv[1:2] == ["prefs"]:
        os.umask(0o077)
        sys.exit(prefs_cli(sys.argv[2:]))
    os.umask(0o077)
    s = Settings()
    login = Login(s)
    LOG("hister-login %s: public :%d, internal :%d, Hister %s, return hosts %s, cookie %s, domain %s"
        % (VERSION, s.port, s.internal_port, s.hister_url, ",".join(s.return_hosts), s.sso,
           s.cookie_domain or "(none)"))
    serve(login, s.bind, s.port, s.internal_port)
    stop = threading.Event()
    try:
        housekeeping(login, stop)
    except KeyboardInterrupt:
        stop.set()


if __name__ == "__main__":
    main()
