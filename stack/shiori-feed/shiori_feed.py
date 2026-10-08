#!/usr/bin/env python3
"""shiori-feed: an RSS 2.0 feed of any Hister search, for Shiori's Subscribe (Machiya, stack/shiori-feed).

    GET /shiori/feed?q=<Hister query>[&title=<channel title>][&exclude_label=<label>]...
        -> 200 application/rss+xml: the 50 most recently visited matches (Hister's sort "date" orders by `updated`;
           each item's pubDate is `added`, when Hister first saw the page). Cache-Control max-age=300.
           400: q missing or over 500 characters, a title over 200, more than 20 exclude_label or one over 100.
           403: the gate refused the caller. 429: over SHIORI_FEED_PER_MINUTE (+ Retry-After). 502: Hister failed.
    GET /shiori/healthz -> 200 "ok" (no gate; doesn't touch Hister)
    GET /shiori/api/status -> 200 JSON {"ok", "ready", "error", "version", "auth", "room_tokens", "hister": {"token",
        "last_ok", "last_error"}} (no gate, like every Machiya service's): `error` is set once three feeds in a row
        failed to reach Hister.
    GET /shiori/api/changelog -> this service's CHANGELOG.md, text/markdown (no gate; at most 64 KiB, an ETag)

The paths also answer without the /shiori prefix (/feed, /healthz, /api/status, /api/changelog), for a proxy that
strips its mount point.
`q` is anything Hister's search box takes: label:books, an alias, words, * for everything. Each exclude_label (exact,
case-sensitive) is sent to Hister as ` -label:"x"`. Code documents (metadata.source:code) are never feed items.

The gate (SHIORI_FEED_AUTH; the README has the details):
    tailscale  (default) a Tailscale-User-Login in SHIORI_FEED_USERS, believed only from SHIORI_FEED_TRUSTED_PROXIES
               when set; a non-loopback bind needs that or SHIORI_FEED_BIND_BEHIND_PROXY=1, or it refuses to start
    proxy      a proxy in front signs people in (nginx auth_request, say); only SHIORI_FEED_TRUSTED_PROXIES may connect
    open       no check; only on a loopback bind, or with SHIORI_FEED_BIND_BEHIND_PROXY=1 (a published 127.0.0.1 port)
Room tokens (0.2.0), in tailscale and proxy mode: with SHIORI_FEED_AUTH_URL (hister-login's internal address) a caller
may also send `Authorization: Bearer mht_…` (or `X-Machiya-Token`), a room token hister-login issued for
SHIORI_FEED_PUBLIC_URL's origin, acting as one of SHIORI_FEED_HISTER_USERS. A request that carries one is decided by
the token alone: a bad, revoked or other room's token is refused, never passed to the other checks.

Hister gets `Origin: hister://` and the owner's token from SHIORI_FEED_HISTER_TOKEN_FILE (X-Access-Token), never a
redirect. The log carries method, path and status only: never a query, a title or a token. Stdlib only.
"""
import collections
import hashlib
import http.client
import ipaddress
import json
import os
import re
import signal
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from email.utils import formatdate
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from xml.sax.saxutils import escape

VERSION = "0.2.0"

FEED_PATHS = {"/shiori/feed", "/feed"}
HEALTH_PATHS = {"/shiori/healthz", "/healthz"}
STATUS_PATHS = {"/shiori/api/status", "/api/status"}
CHANGELOG_PATHS = {"/shiori/api/changelog", "/api/changelog"}
OPEN_PATHS = HEALTH_PATHS | STATUS_PATHS | CHANGELOG_PATHS     # no gate, and not logged
CHANGELOG_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "CHANGELOG.md")
CHANGELOG_LIMIT = 64 * 1024
HISTER_FAILS = 3                    # feeds in a row that failed to reach Hister before /api/status reports an error
FEED_LIMIT, Q_MAX, TITLE_MAX, LABEL_MAX, LABELS_MAX, DESC_MAX = 50, 500, 200, 100, 20, 500
HISTER_TIMEOUT = 20                 # seconds for one Hister search
HISTER_MAX = 16 << 20               # the most of Hister's answer read (50 documents with their text)
CLIENT_TIMEOUT = 30                 # seconds a client may take to send its request
CTRL = re.compile("[\x00-\x08\x0b\x0c\x0e-\x1f\x7f￾￿]")     # not allowed in XML 1.0 (and DEL)
TOKEN_RE = re.compile(r"[\x21-\x7e]{1,4096}")
RTOKEN_RE = re.compile(r"mht_[A-Za-z0-9_-]{43}\Z")     # a room token, as hister-login mints them
MODES = ("tailscale", "proxy", "open")


def log(msg):
    sys.stderr.write("%s shiori-feed %s\n" % (time.strftime("%Y-%m-%d %H:%M:%S"), msg))
    sys.stderr.flush()


# -- settings --------------------------------------------------------------------------------------------------------

class SecretFile:
    """A token in a file (its first line), re-read when the file changes, so a rotated token needs no restart. A file
    that vanishes or holds no token keeps the last good value. Never in repr() or a log line."""

    def __init__(self, path):
        self.path, self.stamp, self.value, self.lock = path, None, "", threading.Lock()
        self.get()

    def get(self):
        try:
            st = os.stat(self.path)
            stamp = (st.st_ino, st.st_mtime_ns, st.st_size)
        except OSError:
            return self.value
        with self.lock:
            if stamp != self.stamp:
                try:
                    with open(self.path, encoding="utf-8") as f:
                        value = f.readline().strip()
                except (OSError, UnicodeError):
                    value = ""
                if TOKEN_RE.fullmatch(value):
                    self.value = value
                self.stamp = stamp
            return self.value

    def __repr__(self):
        return "SecretFile(%s)" % self.path


def cidrs(raw, name):
    out = []
    for item in (raw or "").split(","):
        item = item.strip()
        if not item:
            continue
        try:
            out.append(ipaddress.ip_network(item, strict=False))
        except ValueError:
            raise SystemExit("shiori-feed: %s: %r is not an address or a CIDR" % (name, item))
    return tuple(out)


def peer_ip(addr):
    try:
        ip = ipaddress.ip_address((addr or "").split("%", 1)[0])
    except ValueError:
        return None
    return ip.ipv4_mapped or ip if ip.version == 6 else ip


def is_loopback(bind):
    if bind == "localhost":
        return True
    try:
        return ipaddress.ip_address(bind).is_loopback
    except ValueError:
        return False


def flag(value):
    return (value or "").strip().lower() in ("1", "true", "yes", "on")


def own_origin(url):
    """scheme://host[:port], as hister-login writes a room's origin (lowercase, no default port)."""
    try:
        u = urllib.parse.urlsplit(url)
        port = None if u.port in (None, {"https": 443, "http": 80}.get(u.scheme)) else u.port
        return "%s://%s%s" % (u.scheme.lower(), (u.hostname or "").lower(), "" if port is None else ":%d" % port)
    except ValueError:
        return ""


class RoomTokens:
    """Room tokens checked with hister-login (GET <auth_url>/v1/check), as smallweb does: a good answer is kept 30 s,
    a refusal 5 s, keyed by the token's SHA-256 (the token itself is never kept, logged or passed on)."""

    def __init__(self, auth_url, public_url, users, timeout=2):
        self.auth_url, self.origin, self.users, self.timeout = auth_url, own_origin(public_url), users, timeout
        self.cache, self.lock = {}, threading.Lock()

    def check(self, value):
        """-> the Hister username the token acts as, or None."""
        key = hashlib.sha256(value.encode("ascii")).hexdigest()
        with self.lock:
            hit = self.cache.get(key)
        if hit and hit[0] > time.monotonic():
            return hit[1]
        user, ttl = None, 5
        u = urllib.parse.urlsplit(self.auth_url)
        conn = (http.client.HTTPSConnection if u.scheme == "https" else http.client.HTTPConnection)(
            u.hostname, u.port, timeout=self.timeout)
        try:
            conn.request("GET", u.path.rstrip("/") + "/v1/check", headers={
                "X-Machiya-Session": value, "X-Machiya-Room": self.origin, "Accept": "application/json"})
            r = conn.getresponse()
            data = json.loads(r.read(1 << 16) or b"null")
            if r.status == 200 and isinstance(data, dict) and data.get("kind") == "token" \
                    and data.get("username") in self.users:
                user, ttl = data["username"], 30
        except (OSError, http.client.HTTPException, ValueError):
            pass
        finally:
            conn.close()
        with self.lock:
            if len(self.cache) > 1024:
                self.cache.clear()
            self.cache[key] = (time.monotonic() + ttl, user)
        return user


def room_token_user(tokens, headers):
    """-> (presented, Hister username or None). A request without a room token isn't one (presented False: the other
    checks decide); a malformed token, two of them, or one sent when room tokens are off is refused."""
    values = headers.get_all("Authorization") or []
    extra = [v.strip() for v in headers.get_all("X-Machiya-Token") or [] if v.strip()]   # a client with fixed headers
    if extra:
        if values or len(extra) > 1 or tokens is None or not RTOKEN_RE.match(extra[0]):
            return True, None
        value = extra[0]
    else:
        if not values:
            return False, None
        scheme, _, value = values[0].strip().partition(" ")
        value = value.strip()
        if len(values) == 1 and not (scheme.lower() == "bearer" and value.startswith("mht_")):
            return False, None
        if tokens is None or len(values) > 1 or not RTOKEN_RE.match(value):
            return True, None
    return True, tokens.check(value)


def changelog():
    """GET /api/changelog (the shape of vaultkit.changelog): (status, body, headers). The file's first 64 KiB cut at a
    whole line, text/markdown, an ETag; a missing file is a 404."""
    try:
        with open(CHANGELOG_FILE, "rb") as f:
            data = f.read(CHANGELOG_LIMIT + 1)
    except OSError:
        return 404, b"no changelog\n", [("Content-Type", "text/plain; charset=utf-8"), ("Cache-Control", "no-store")]
    if len(data) > CHANGELOG_LIMIT:
        data = data[:CHANGELOG_LIMIT]
        cut = data.rfind(b"\n")
        data = data[:cut + 1] if cut > 0 else data
    body = data.decode("utf-8", "replace").encode("utf-8")
    tag = '"%s"' % hashlib.sha256(body).hexdigest()[:20]
    return 200, body, [("Content-Type", "text/markdown; charset=utf-8"), ("ETag", tag), ("Cache-Control", "no-cache")]


class Config:
    """Everything from the environment, checked once at start: a bad setting stops the start with its reason."""

    def __init__(self, env=None):
        env = os.environ if env is None else env
        g = lambda name, default="": (env.get("SHIORI_FEED_" + name) or default).strip()
        self.hister = g("HISTER_URL").rstrip("/")
        if not self.hister.startswith(("http://", "https://")):
            raise SystemExit("shiori-feed: SHIORI_FEED_HISTER_URL must be Hister's http(s) address (http://hister:4433)")
        self.public = (g("HISTER_PUBLIC_URL") or self.hister).rstrip("/")
        token = g("HISTER_TOKEN_FILE")
        self.token = None
        if token:
            self.token = SecretFile(token)
            if not self.token.value:
                raise SystemExit("shiori-feed: SHIORI_FEED_HISTER_TOKEN_FILE: no token on the first line of %s" % token)
        self.auth = g("AUTH", "tailscale").lower()
        if self.auth not in MODES:
            raise SystemExit("shiori-feed: SHIORI_FEED_AUTH must be tailscale, proxy or open, not %r" % self.auth)
        self.users = frozenset(u.strip() for u in g("USERS").split(",") if u.strip())
        self.trusted = cidrs(g("TRUSTED_PROXIES"), "SHIORI_FEED_TRUSTED_PROXIES")
        self.behind_proxy = flag(g("BIND_BEHIND_PROXY"))
        self.bind = g("BIND", "127.0.0.1")
        try:
            self.port = int(g("PORT", "8080"))
            self.per_minute = int(g("PER_MINUTE", "60"))
        except ValueError:
            raise SystemExit("shiori-feed: SHIORI_FEED_PORT and SHIORI_FEED_PER_MINUTE are numbers")
        self.auth_url = g("AUTH_URL").rstrip("/")
        self.public_url = g("PUBLIC_URL").rstrip("/")
        self.hister_users = frozenset(u.strip() for u in g("HISTER_USERS").split(",") if u.strip())
        if self.auth_url:
            if not self.auth_url.startswith(("http://", "https://")):
                raise SystemExit("shiori-feed: SHIORI_FEED_AUTH_URL must be hister-login's http(s) address")
            if not self.public_url.startswith(("http://", "https://")) or not self.hister_users \
                    or "*" in self.hister_users:
                raise SystemExit("shiori-feed: SHIORI_FEED_AUTH_URL (room tokens) needs SHIORI_FEED_PUBLIC_URL (the "
                                 "address the tokens are issued for) and SHIORI_FEED_HISTER_USERS (never *)")
        self.room_tokens = RoomTokens(self.auth_url, self.public_url, self.hister_users) if self.auth_url else None
        self.check_bind()

    def check_bind(self):
        """A gate that trusts a header, or no gate, must not be reachable by anyone who can reach the port."""
        if self.auth == "open" and not (self.behind_proxy or is_loopback(self.bind)):
            raise SystemExit("shiori-feed: SHIORI_FEED_AUTH=open checks nobody, so it binds 127.0.0.1 only, not %s, unless "
                             "SHIORI_FEED_BIND_BEHIND_PROXY=1 says only this host's own port reaches it. Use tailscale or "
                             "proxy mode for anything else." % self.bind)
        if self.auth == "proxy" and not self.trusted:
            raise SystemExit("shiori-feed: SHIORI_FEED_AUTH=proxy needs SHIORI_FEED_TRUSTED_PROXIES: the address (or "
                             "CIDR) of the proxy that signs people in")
        if self.auth == "tailscale" and not (self.trusted or self.behind_proxy or is_loopback(self.bind)):
            raise SystemExit("shiori-feed: SHIORI_FEED_BIND=%s trusts Tailscale-User-Login from anyone who can reach "
                             "it. Set SHIORI_FEED_TRUSTED_PROXIES to the Tailscale sidecar's address (or CIDR), or "
                             "SHIORI_FEED_BIND_BEHIND_PROXY=1 when only the proxy shares its network, or bind "
                             "127.0.0.1." % self.bind)

    def allows(self, client, headers):
        """The gate, for one request: True to serve it."""
        return self.decide(client, headers)[0]

    def decide(self, client, headers):
        """The gate: (serve it, decided by a room token)."""
        if self.auth == "open":
            return True, False
        presented, user = room_token_user(self.room_tokens, headers)
        if presented:                                   # a room token decides alone
            return user is not None, True
        return self.allows_session(client, headers), False

    def allows_session(self, client, headers):
        ip = peer_ip(client)
        from_proxy = ip is not None and any(ip in net for net in self.trusted)
        if self.auth == "proxy":
            return from_proxy
        if self.trusted and not from_proxy:
            return False                                # the header counts only from the sidecar
        if "*" in self.users:
            return True
        logins = headers.get_all("Tailscale-User-Login") or []
        return len(logins) == 1 and logins[0].strip() in self.users


class Limiter:
    """At most `per_minute` requests in any 60 s, for every caller together. take() -> 0, or seconds to wait."""

    def __init__(self, per_minute, clock=time.monotonic):
        self.per_minute, self.clock = per_minute, clock
        self.times, self.lock = collections.deque(), threading.Lock()

    def take(self):
        if self.per_minute <= 0:
            return 0
        now = self.clock()
        with self.lock:
            while self.times and self.times[0] <= now - 60:
                self.times.popleft()
            if len(self.times) >= self.per_minute:
                return int(self.times[0] + 60 - now) + 1
            self.times.append(now)
            return 0


# -- Hister ----------------------------------------------------------------------------------------------------------

class NoRedirect(urllib.request.HTTPRedirectHandler):
    """A redirect from Hister is an error: urllib would carry X-Access-Token to wherever it points."""

    def redirect_request(self, *args, **kwargs):
        return None


OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect)


def x(text):
    return escape(CTRL.sub("", str(text or "")))


def hister_search(cfg, q, exclude=()):
    """The newest FEED_LIMIT matches for q, minus the excluded labels and code documents, as Hister's documents[]."""
    text = q + "".join(' -label:"%s"' % e.replace('"', "") for e in sorted(exclude)) + " -metadata.source:code"
    body = {"text": text, "sort": "date", "limit": FEED_LIMIT, "include_text": True}
    headers = {"Origin": "hister://", "Accept": "application/json", "User-Agent": "machiya-shiori-feed/" + VERSION}
    token = cfg.token.get() if cfg.token else ""
    if token:
        headers["X-Access-Token"] = token
    req = urllib.request.Request(cfg.hister + "/search?query=" + urllib.parse.quote(json.dumps(body)), headers=headers)
    with OPENER.open(req, timeout=HISTER_TIMEOUT) as r:
        if r.status != 200:
            raise ValueError("Hister answered %d" % r.status)
        raw = r.read(HISTER_MAX + 1)
    if len(raw) > HISTER_MAX:
        raise ValueError("Hister's answer is over %d bytes" % HISTER_MAX)
    res = json.loads(raw)
    docs = res.get("documents") if isinstance(res, dict) else None
    return [d for d in docs or [] if isinstance(d, dict) and d.get("label") not in exclude][:FEED_LIMIT]


def rss(cfg, q, title, docs):
    items = []
    for d in docs:
        url = str(d.get("url") or "")
        if not url:
            continue
        text = " ".join(str(d.get("text") or "").split())
        desc = text[:DESC_MAX] + ("…" if len(text) > DESC_MAX else "")
        try:
            pub = formatdate(int(d.get("added") or 0), usegmt=True)
        except (TypeError, ValueError, OverflowError, OSError):
            pub = None
        items.append("<item><title>%s</title><link>%s</link><guid isPermaLink=\"true\">%s</guid>%s"
                     "<description>%s</description>%s</item>" % (
                         x(d.get("title") or url), x(url), x(url),
                         "<pubDate>%s</pubDate>" % pub if pub else "", x(desc),
                         "<category>%s</category>" % x(d["label"]) if d.get("label") else ""))
    return ('<?xml version="1.0" encoding="utf-8"?>\n<rss version="2.0"><channel>'
            "<title>%s</title><link>%s</link><description>%s</description>%s</channel></rss>\n" % (
                x("Shiori – " + (title or q)), x(cfg.public + "/?q=" + urllib.parse.quote(q)),
                x("Hister search: " + q), "".join(items))).encode("utf-8")


# -- HTTP ------------------------------------------------------------------------------------------------------------

class Handler(BaseHTTPRequestHandler):
    server_version = "shiori-feed/" + VERSION
    sys_version = ""
    timeout = CLIENT_TIMEOUT            # a client that stalls loses its connection (HTTP/1.0: one request each)

    @property
    def cfg(self):
        return self.server.cfg

    def reply(self, code, body=b"", ctype="text/plain; charset=utf-8", cache="no-store", headers=()):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", cache)
        self.send_header("X-Content-Type-Options", "nosniff")
        for k, v in headers:
            self.send_header(k, v)
        self.end_headers()
        if body and self.command != "HEAD":
            self.wfile.write(body)

    def feed(self):
        if not self.cfg.allows(self.client_address[0], self.headers):
            return self.reply(403, b"forbidden\n")
        wait = self.server.limiter.take()
        if wait:
            return self.reply(429, b"too many feeds: try again later\n", headers=[("Retry-After", str(wait))])
        try:
            params = urllib.parse.parse_qs(urllib.parse.urlsplit(self.path).query, max_num_fields=50)
        except ValueError:
            return self.reply(400, b"bad request\n")
        q = (params.get("q") or [""])[0].strip()
        title = (params.get("title") or [""])[0].strip()
        exclude = {e for e in params.get("exclude_label", []) if e}
        if not q or len(q) > Q_MAX or len(title) > TITLE_MAX or len(exclude) > LABELS_MAX \
                or any(len(e) > LABEL_MAX for e in exclude):
            return self.reply(400, b"bad request\n")
        try:
            docs = hister_search(self.cfg, q, exclude)
        except urllib.error.HTTPError as e:
            log("hister: HTTP %d" % e.code)
            self.server.hister_failed("HTTP %d" % e.code)
            return self.reply(502, b"Hister failed\n")
        except (OSError, ValueError) as e:                  # refused, timeouts, too big, bad JSON
            log("hister: %s" % type(e).__name__)
            self.server.hister_failed(type(e).__name__)
            return self.reply(502, b"Hister failed\n")
        self.server.hister_ok()
        self.reply(200, rss(self.cfg, q, title, docs), "application/rss+xml; charset=utf-8", "private, max-age=300")

    def do_GET(self):
        path = self.path.split("?", 1)[0]
        if path in FEED_PATHS:
            return self.feed()
        if path in HEALTH_PATHS:
            return self.reply(200, b"ok\n")
        if path in STATUS_PATHS:
            return self.reply(200, json.dumps(self.server.status()).encode(), "application/json; charset=utf-8")
        if path in CHANGELOG_PATHS:
            code, body, headers = changelog()
            tag = dict(headers).get("ETag")
            asked = [t.strip() for t in (self.headers.get("If-None-Match") or "").split(",")]
            if code == 200 and tag and (tag in asked or "*" in asked):
                code, body = 304, b""
            h = dict(headers)
            return self.reply(code, body, h["Content-Type"], h["Cache-Control"],
                              [(k, v) for k, v in headers if k not in ("Content-Type", "Cache-Control")])
        self.reply(404, b"not found\n")

    def do_HEAD(self):
        path = self.path.split("?", 1)[0]
        if path in STATUS_PATHS | CHANGELOG_PATHS:
            return self.do_GET()
        self.reply(200 if path in HEALTH_PATHS else 405 if path in FEED_PATHS else 404)

    def refuse(self):
        path = self.path.split("?", 1)[0]
        self.reply(405 if path in FEED_PATHS | OPEN_PATHS else 404, headers=[("Allow", "GET")])

    do_PUT = do_POST = do_DELETE = do_PATCH = do_OPTIONS = refuse

    def log_request(self, code="-", size="-"):        # method, path and status: never the query (searches)
        path = CTRL.sub("?", (getattr(self, "path", "") or "").split("?", 1)[0])[:200]
        if path not in OPEN_PATHS:
            log("%s %s %s" % (CTRL.sub("?", self.command or "-")[:16], path, getattr(code, "value", code)))

    def log_error(self, fmt, *args):                   # the code only, never the raw request line
        log("error %s" % (args[0] if args else "-"))

    def log_message(self, fmt, *args):
        pass


class Server(ThreadingHTTPServer):
    daemon_threads = True

    def setup(self, cfg):
        self.cfg, self.limiter = cfg, Limiter(cfg.per_minute)
        self.hister = {"last_ok": None, "last_error": None, "fails": 0}
        self.state_lock = threading.Lock()

    def hister_ok(self):
        with self.state_lock:
            self.hister.update(last_ok=int(time.time()), fails=0)

    def hister_failed(self, why):
        with self.state_lock:
            self.hister["fails"] += 1
            self.hister["last_error"] = "%s: %s" % (time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), why)

    def status(self):
        """GET /api/status: never a query, a title or a token."""
        with self.state_lock:
            h = dict(self.hister)
        error = "Hister failed %d feeds in a row (%s)" % (h["fails"], h["last_error"]) \
            if h["fails"] >= HISTER_FAILS else None
        return {"ok": error is None, "ready": True, "error": error, "version": VERSION, "auth": self.cfg.auth,
                "room_tokens": self.cfg.room_tokens is not None,
                "hister": {"token": self.cfg.token is not None, "last_ok": h["last_ok"], "last_error": h["last_error"]}}


def make_server(cfg, bind=None, port=None):
    server = Server((cfg.bind if bind is None else bind, cfg.port if port is None else port), Handler)
    server.setup(cfg)
    return server


def main():
    cfg = Config()
    server = make_server(cfg)
    log("%s: listening on %s:%d, auth %s%s%s, Hister %s%s" % (
        VERSION, cfg.bind, cfg.port, cfg.auth,
        "" if cfg.auth != "tailscale" else " (%s)" % (",".join(sorted(cfg.users)) or "NOBODY: set SHIORI_FEED_USERS"),
        " + room tokens" if cfg.room_tokens and cfg.auth != "open" else "",
        cfg.hister, " with a token" if cfg.token else ""))
    if cfg.auth == "open":
        log("WARNING: SHIORI_FEED_AUTH=open: no identity check; anyone who reaches %s:%d can read your Hister through "
            "it" % (cfg.bind, cfg.port))
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))     # docker stop: exit at once, not after 10 s
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
