"""The built-in sign-in, Shiori device pairing and per-user preferences (docs/identity.md, phase 6), for any room.

Every function takes plain inputs (a headers mapping with .get, the request body as bytes, the client address) and
returns `(status, [(header, value), ...], body bytes)`, so a room's handler stays a few lines (docs/vaultkit.md):

    GET  /signin      handle_get(ident, headers, query)              the form
    POST /signin      handle_post(ident, headers, body, client)      same-origin form post -> session cookie
    POST /signout     handle_signout(ident, headers)                 same-origin -> cookie cleared
    POST /api/pair    handle_pair(ident, headers, body, client)      {"code", "device"} -> {"token"}; no cookie involved
    GET/PUT /api/prefs  handle_prefs(prefs, principal, method, headers, body, secure)

The rules, all failing closed: a post that changes state on the strength of a cookie (sign-in, sign-out, a prefs PUT
made with a cookie) must be same-origin (Origin, else Referer, naming the request's own Host; neither is a refusal);
`next` is only ever a local path; bodies are size-capped before they are parsed; nothing a page or a JSON answer says
contains a password, a token's secret (except the one /api/pair hands out) or a hash.
"""
import json
import re
import os
import sqlite3
import threading
import time
from contextlib import closing
from urllib.parse import parse_qs, quote, urlsplit

from . import shell
from .identity import Identity, tailscale_uid

MAX_FORM = 4096                 # name + password (<= 1024) + next, urlencoded
MAX_PAIR = 1024                 # {"code": ..., "device": ...}
MAX_PREFS = 512 * 1024          # a PUT of every key at its largest, with room for JSON escapes
MAX_NEXT = 2048

PAGE_HEADERS = [
    ("Content-Type", "text/html; charset=utf-8"),
    ("Cache-Control", "no-store"),
    ("X-Frame-Options", "DENY"),                            # nobody frames the password form (clickjacking)
    ("Content-Security-Policy", "frame-ancestors 'none'"),
    ("X-Content-Type-Options", "nosniff"),
    ("Referrer-Policy", "same-origin"),
]
JSON_HEADERS = [("Content-Type", "application/json"), ("Cache-Control", "no-store"),
                ("X-Content-Type-Options", "nosniff")]

_PRINTABLE = "".join(chr(c) for c in range(0x21, 0x7f))


# -- small pieces ------------------------------------------------------------------------------------------------------

def safe_next(value):
    """A local path to go to after signing in, else "/". One leading "/", not followed by "/" or "\\" (browsers read
    //host and /\\host as another site), no control characters or whitespace, not longer than MAX_NEXT. Non-ASCII is
    percent-encoded so it fits in a Location header."""
    if not isinstance(value, str) or not value or len(value) > MAX_NEXT:
        return "/"
    if any(ord(c) < 32 or 127 <= ord(c) < 160 or c.isspace() for c in value):
        return "/"
    if not value.startswith("/") or value[1:2] in ("/", "\\"):
        return "/"
    value = quote(value, safe=_PRINTABLE)
    if urlsplit(value).path.rstrip("/") in ("/signin", "/signout"):
        return "/"
    return value


def _one(headers, name):
    """A header's single value; None when absent or sent twice (a duplicate is never half-read)."""
    values = Identity.header_values(headers, name)
    return values[0] if len(values) == 1 else None


def origin_of(url):
    """'https://Host.example:443/x' -> 'https://host.example:443' (scheme://host[:port], lowercased);
    "" if unreadable."""
    try:
        u = urlsplit((url or "").strip())
        scheme = u.scheme.lower()
        if scheme not in ("http", "https") or not u.hostname or u.username is not None or u.password is not None:
            return ""
        host, port = u.hostname.lower(), u.port               # a malformed port raises ValueError
        if any(c.isspace() or c in "@\\" for c in host):
            return ""
        if port in (None, {"https": 443, "http": 80}[scheme]):  # the default port is the same origin as none
            port = None
        host = "[%s]" % host if ":" in host else host
        return "%s://%s%s" % (scheme, host, ":%d" % port if port else "")
    except ValueError:
        return ""


def same_origin(headers, secure=True, origins=()):
    """True when the request names its own site: Origin (or, without one, Referer) is one of the room's own
    `origins` (its public address(es), e.g. KURA_PUBLIC_URL), or without those has the scheme and host:port of the
    request's Host. Neither header, "null", a duplicate or anything unreadable is False. With `secure` the page must
    be https. Over plain http without `origins` it is always False: there, Host and Origin both come from a page that
    pointed its own name at the room (DNS rebinding), so they prove nothing; a room served over http passes origins."""
    if isinstance(origins, str):        # one address passed as a string, not a list
        origins = (origins,)
    if origins:
        source = _one(headers, "Origin")
        source = source if source is not None else _one(headers, "Referer")
        found = origin_of(source) if source else ""
        return bool(found) and found in {origin_of(o) for o in origins} - {""} \
            and (not secure or found.startswith("https:"))
    if not secure:
        return False
    host = (_one(headers, "Host") or "").strip().lower()
    if not host or len(host) > 255 or any(c.isspace() or ord(c) < 32 for c in host):
        return False
    origin = _one(headers, "Origin")
    source = origin if origin is not None else _one(headers, "Referer")
    if not source:
        return False
    try:
        u = urlsplit(source.strip())
        netloc = u.netloc.lower()
    except ValueError:
        return False
    return u.scheme in (("https",) if secure else ("http", "https")) and netloc == host


def _media(headers):
    return (headers.get("Content-Type") or "").split(";")[0].strip().lower()


def read_body(headers, rfile, limit):
    """The request body, at most `limit` bytes; None when it's larger, has no usable Content-Length (http.server
    doesn't decode chunked bodies) or ends early. On None a room answers 413 and closes the connection."""
    if headers.get("Transfer-Encoding"):
        return None
    values = Identity.header_values(headers, "Content-Length")
    if len(values) > 1:
        return None                     # a duplicate is never half-read
    raw = (values[0] if values else "0").strip()
    if not re.fullmatch(r"[0-9]{1,9}", raw):
        return None                     # int() would take "5_0", "+5" or other scripts' digits
    length = int(raw)
    if length < 0 or length > limit:
        return None
    data = rfile.read(length) if length else b""
    return data if len(data) == length else None


def _text(status, text):
    return status, [("Content-Type", "text/plain; charset=utf-8"), ("Cache-Control", "no-store"),
                    ("X-Content-Type-Options", "nosniff")], (text + "\n").encode()


def _redirect(location, cookies):
    return 303, [("Location", location), ("Cache-Control", "no-store")] + [("Set-Cookie", c) for c in cookies], b""


def _json(status, data, extra=()):
    return status, JSON_HEADERS + list(extra), json.dumps(data, ensure_ascii=False).encode()


# -- the sign-in page --------------------------------------------------------------------------------------------------

def page(room, next="/", error="", name="", ctx=None, action="/signin"):
    """The sign-in page (HTML): name, password and the safe `next`, in the shared shell. `error` is shown as is
    (escaped); the password is never echoed back."""
    ctx = ctx or shell.Prefs()
    e = shell.e
    alert = '<p class="signin-error" role="alert">%s</p>' % e(error) if error else ""
    body = (
        '%s<main class="signin"><h1>Sign in</h1>%s'
        '<form class="group" method="post" action="%s">'
        '<input type="hidden" name="next" value="%s">'
        '<label class="item"><span>Name</span><input name="name" value="%s" required maxlength="64" '
        'autocomplete="username" autocapitalize="none" autocorrect="off" spellcheck="false"%s></label>'
        '<label class="item"><span>Password</span><input type="password" name="password" required maxlength="1024" '
        'autocomplete="current-password"%s></label>'
        '<button type="submit">Sign In</button></form>'
        '<p class="footnote">You stay signed in on this browser until you sign out or stop visiting for a while.</p>'
        '</main>'
    ) % (shell.header(room, [], "", shell.rooms(), settings=False), alert, e(action), e(safe_next(next)), e(name),
         "" if name else " autofocus", " autofocus" if name else "")
    return shell.page(ctx, room, "Sign In", body, manifest=False)


def _page(identity, headers, status, next="/", error="", name=""):
    ctx = shell.prefs(headers.get("Cookie"))
    return status, list(PAGE_HEADERS), page(identity.room, next, error, name, ctx).encode()


def handle_get(identity, headers, query=""):
    """GET /signin[?next=/path]: the form. 404 when this room has no identity file or its sign-in is off."""
    if identity is None or not identity.signin:
        return _text(404, "not found")
    try:
        nxt = parse_qs(query or "", max_num_fields=8).get("next", ["/"])[0]
    except ValueError:
        nxt = "/"
    return _page(identity, headers, 200, nxt)


def _form(body):
    """urlencoded body -> {field: value}; ValueError when unreadable or a field comes twice."""
    form = parse_qs(body.decode("utf-8"), keep_blank_values=True, max_num_fields=8)    # UnicodeDecodeError too
    if any(len(v) != 1 for v in form.values()):
        raise ValueError("a field twice")
    return {k: v[0] for k, v in form.items()}


def handle_post(identity, headers, body, client="", origins=()):
    """POST /signin. Same-origin only (403); urlencoded and at most MAX_FORM bytes. Success: 303 to the safe `next`
    with the session cookie. A wrong name or password: the page again, 401, one message for both; too many tries:
    429. Sign-in off or no identity file: 404."""
    if identity is None or not identity.signin:
        return _text(404, "not found")
    if body is None or len(body) > MAX_FORM:
        return _text(413, "request body too large")
    if not same_origin(headers, identity.secure, origins):
        return _text(403, "cross-site sign-in refused")
    if _media(headers) != "application/x-www-form-urlencoded":
        return _text(415, "send the sign-in form")
    try:
        form = _form(body)
    except ValueError:
        return _text(400, "unreadable form")
    nxt, name = safe_next(form.get("next", "/")), form.get("name", "")
    result = identity.sign_in(name, form.get("password", ""), client)
    if result:
        return _redirect(nxt, result.cookies)
    shown = name.strip()[:64] if isinstance(name, str) else ""
    if result.status == 401:
        return _page(identity, headers, 401, nxt, "Wrong name or password.", shown)
    if result.status in (429, 503):
        return _page(identity, headers, result.status, nxt, result.error[:1].upper() + result.error[1:] + ".", shown)
    return _text(result.status if 400 <= result.status < 600 else 500, result.error or "refused")


def handle_signout(identity, headers, origins=()):
    """POST /signout: same-origin only (403); clears the session cookie and goes to /."""
    if identity is None:
        return _text(404, "not found")
    if not same_origin(headers, identity.secure, origins):
        return _text(403, "cross-site sign-out refused")
    return _redirect("/", identity.sign_out())


def handle_pair(identity, headers, body, client=""):
    """POST /api/pair, JSON {"code": "ABCD-EFGH", "device": "iPhone"} -> {"token": "mcd_...", "principal": name}.
    `device` is informational and checked for shape only; the device id and label come from the CLI's pairing entry.
    No cookie is read or set, so no same-origin rule: the code is the proof (throttled, 5 per address per 10 minutes).
    Errors are {"error": ...} with 400, 401 (unknown or expired code), 413, 415, 429 or 503."""
    if identity is None:
        return _json(404, {"error": "not found"})
    if body is None or len(body) > MAX_PAIR:
        return _json(413, {"error": "request body too large"})
    if _media(headers) != "application/json":
        return _json(415, {"error": "send JSON"})
    try:
        data = json.loads(body.decode("utf-8"))
    except (ValueError, RecursionError):
        return _json(400, {"error": "unreadable JSON"})
    code = data.get("code") if isinstance(data, dict) else None
    device = data.get("device", "") if isinstance(data, dict) else None
    if not isinstance(code, str) or not code.strip() or not isinstance(device, str) or len(device) > 64 \
            or any(ord(c) < 32 or ord(c) == 127 for c in device):
        return _json(400, {"error": "send {\"code\": ..., \"device\": ...}"})
    result, token = identity.pair(code, client)
    if not result or not token:
        return _json(result.status if result.status >= 400 else 401, {"error": result.error or "refused"})
    return _json(200, {"token": token, "principal": result.principal.name})


# -- per-user preferences ----------------------------------------------------------------------------------------------

KEY_RE = re.compile(r"[a-z0-9_.-]{1,64}\Z")
MAX_VALUE = 4096                # bytes of UTF-8
MAX_KEYS = 100                  # per principal


class PrefsError(ValueError):
    """A PUT the limits refuse; the message is safe to show."""


class Prefs:
    """A principal's preferences in the room's own SQLite file: table prefs(principal, key, value, updated), keyed by
    the principal's id (Principal.uid), so a name deleted and added again starts empty. Keys match
    KEY_RE, values are strings of at most MAX_VALUE bytes, at most MAX_KEYS keys per principal. Nothing here decides who
    the principal is: the room passes the name `resolve` gave it.

    `fallbacks` (get_all, put): ids the same person's preferences may have been stored under before (the ambient
    "ts:..." key of a Tailscale login, from a room without an identity file). The first time a principal with
    fallbacks is seen, if it has no preferences of its own, the first fallback that has some is copied to it, in the
    same transaction; the old rows stay. Table prefs_moved(principal, source, moved) records that this was decided
    (source "" when nothing was copied), so it happens once: preferences removed later never come back."""

    def __init__(self, path):
        self.path, self.lock = path, threading.Lock()
        try:                            # 0600: other local users don't read anyone's preferences
            os.close(os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600))
        except FileExistsError:
            pass                        # already there (or another process just made it): keep it as it is
        with self.lock, closing(self._db()) as db, db:
            db.execute("CREATE TABLE IF NOT EXISTS prefs (principal TEXT NOT NULL, key TEXT NOT NULL, "
                       "value TEXT NOT NULL, updated INTEGER NOT NULL, PRIMARY KEY (principal, key))")
            db.execute("CREATE TABLE IF NOT EXISTS prefs_moved (principal TEXT PRIMARY KEY, source TEXT NOT NULL, "
                       "moved INTEGER NOT NULL)")

    def _db(self):
        # autocommit (put() begins its own); a short wait, since the lock below is held meanwhile
        return sqlite3.connect(self.path, timeout=2, isolation_level=None)

    @staticmethod
    def _principal(principal):
        if not isinstance(principal, str) or not principal or len(principal) > 64:
            raise PrefsError("no principal")
        return principal

    @staticmethod
    def _fallbacks(principal, fallbacks):
        return [f for f in fallbacks or () if isinstance(f, str) and f and len(f) <= 64 and f != principal]

    @staticmethod
    def _adopt(db, principal, fallbacks):
        """Inside a transaction: the one-time copy from the first fallback with preferences (see the class)."""
        if not fallbacks or db.execute("SELECT 1 FROM prefs_moved WHERE principal = ?", (principal,)).fetchone():
            return
        source = ""
        if not db.execute("SELECT 1 FROM prefs WHERE principal = ? LIMIT 1", (principal,)).fetchone():
            for f in fallbacks:
                if db.execute("SELECT 1 FROM prefs WHERE principal = ? LIMIT 1", (f,)).fetchone():
                    db.execute("INSERT INTO prefs (principal, key, value, updated) "
                               "SELECT ?, key, value, updated FROM prefs WHERE principal = ?", (principal, f))
                    source = f
                    break
        db.execute("INSERT INTO prefs_moved (principal, source, moved) VALUES (?, ?, ?)",
                   (principal, source, int(time.time())))

    @staticmethod
    def _rollback(db):
        try:
            db.execute("ROLLBACK")
        except sqlite3.Error:
            pass                        # the original error matters, not a failed rollback

    def get_all(self, principal, fallbacks=()):
        principal = self._principal(principal)
        fallbacks = self._fallbacks(principal, fallbacks)
        with self.lock, closing(self._db()) as db:
            if fallbacks and not db.execute("SELECT 1 FROM prefs_moved WHERE principal = ?", (principal,)).fetchone():
                db.execute("BEGIN IMMEDIATE")
                try:
                    self._adopt(db, principal, fallbacks)
                    db.execute("COMMIT")
                except BaseException:
                    self._rollback(db)
                    raise
            return dict(db.execute("SELECT key, value FROM prefs WHERE principal = ? ORDER BY key", (principal,)))

    def put(self, principal, changes, fallbacks=()):
        """Set each key to its string value, or remove it when the value is None; all or nothing. -> get_all."""
        principal = self._principal(principal)
        fallbacks = self._fallbacks(principal, fallbacks)
        if not isinstance(changes, dict):
            raise PrefsError("prefs must be an object of key: string")
        if len(changes) > MAX_KEYS:
            raise PrefsError("at most %d keys" % MAX_KEYS)
        for k, v in changes.items():
            if not isinstance(k, str) or not KEY_RE.match(k):
                raise PrefsError("a key is 1-64 of a-z 0-9 _ . -")
            if v is None:
                continue
            if not isinstance(v, str):
                raise PrefsError("%s: a value is a string (or null to remove it)" % k)
            try:
                size = len(v.encode("utf-8"))
            except UnicodeError:
                raise PrefsError("%s: not valid text" % k)
            if size > MAX_VALUE:
                raise PrefsError("%s: a value is at most %d bytes" % (k, MAX_VALUE))
        t = int(time.time())
        with self.lock, closing(self._db()) as db:
            db.execute("BEGIN IMMEDIATE")
            try:
                self._adopt(db, principal, fallbacks)
                for k, v in changes.items():
                    if v is None:
                        db.execute("DELETE FROM prefs WHERE principal = ? AND key = ?", (principal, k))
                    else:
                        db.execute("INSERT INTO prefs (principal, key, value, updated) VALUES (?, ?, ?, ?) "
                                   "ON CONFLICT (principal, key) DO UPDATE SET value = excluded.value, "
                                   "updated = excluded.updated", (principal, k, v, t))
                count = db.execute("SELECT COUNT(*) FROM prefs WHERE principal = ?", (principal,)).fetchone()[0]
                if count > MAX_KEYS:
                    raise PrefsError("at most %d keys" % MAX_KEYS)
                db.execute("COMMIT")
            except BaseException:
                self._rollback(db)
                raise
            return dict(db.execute("SELECT key, value FROM prefs WHERE principal = ? ORDER BY key", (principal,)))


def bearer(principal):
    """The principal was proven by an Authorization header (a stored or a device token), which a browser never adds
    on its own: no CSRF to guard against. Everything else (a session cookie, a Tailscale or proxy login, open mode)
    rides along with any request a browser makes."""
    return (getattr(principal, "via", "") or "").startswith(("token:", "device:"))


def handle_prefs(prefs, principal, method, headers, body=b"", secure=True, origins=()):
    """GET /api/prefs -> {"prefs": {key: value}}; PUT /api/prefs with JSON {"prefs": {key: value or null}} merges
    (null removes) and answers the same shape. `principal` is what the room's resolve gave (None: 401). A PUT not made
    with a token must be same-origin (403); `secure` is the room's (identity.secure). The room calls this only after
    its own gate (the principal's read grant in the room): a principal with no grants here stores nothing. Without an
    identity file the principal is identity.ambient's. A principal with Tailscale logins (principal.tailscale) gets,
    once, the preferences its logins stored as ambient principals (Prefs' fallbacks)."""
    if principal is None:
        return _json(401, {"error": "sign in first"})
    fallbacks = [tailscale_uid(login) for login in getattr(principal, "tailscale", ()) if login.strip()]
    if method == "GET":
        try:
            return _json(200, {"prefs": prefs.get_all(principal.uid, fallbacks)})  # by id: a reused name starts empty
        except sqlite3.Error:
            return _json(503, {"error": "preferences unavailable"})      # locked, full or read-only: never a crash
    if method != "PUT":
        return _json(405, {"error": "GET or PUT"}, [("Allow", "GET, PUT")])
    if not bearer(principal) and not same_origin(headers, secure, origins):
        return _json(403, {"error": "cross-site write refused"})
    if body is None or len(body) > MAX_PREFS:
        return _json(413, {"error": "request body too large"})
    if _media(headers) != "application/json":
        return _json(415, {"error": "send JSON"})
    try:
        data = json.loads(body.decode("utf-8"))
    except (ValueError, RecursionError):
        return _json(400, {"error": "unreadable JSON"})
    if not isinstance(data, dict) or set(data) != {"prefs"}:
        return _json(400, {"error": "send {\"prefs\": {key: value}}"})
    try:
        return _json(200, {"prefs": prefs.put(principal.uid, data["prefs"], fallbacks)})
    except PrefsError as e:
        return _json(400, {"error": str(e)})
    except sqlite3.Error:
        return _json(503, {"error": "preferences unavailable"})
