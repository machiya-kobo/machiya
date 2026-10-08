#!/usr/bin/env python3
"""shiori-feed: an RSS 2.0 feed of any Hister search, for Shiori's Subscribe (Machiya, stack/shiori-feed).

    GET /shiori/feed?q=<Hister query>[&title=<channel title>][&exclude_label=<label>]...
        -> 200 application/rss+xml: the 50 most recently visited matches (Hister's sort "date" orders by `updated`;
           each item's pubDate is `added`, when Hister first saw the page). Cache-Control max-age=300.
           400: q missing or over 500 characters, a title over 200, more than 20 exclude_label or one over 100.
           403: the gate refused the caller. 429: over SHIORI_FEED_PER_MINUTE (+ Retry-After). 502: Hister failed.
    GET /shiori/healthz -> 200 "ok" (no gate; doesn't touch Hister)

The paths also answer without the /shiori prefix (/feed, /healthz), for a proxy that strips its mount point.
`q` is anything Hister's search box takes: label:books, an alias, words, * for everything. Each exclude_label (exact,
case-sensitive) is sent to Hister as ` -label:"x"`. Code documents (metadata.source:code) are never feed items.

The gate (SHIORI_FEED_AUTH; the README has the details):
    tailscale  (default) a Tailscale-User-Login in SHIORI_FEED_USERS, believed only from SHIORI_FEED_TRUSTED_PROXIES
               when set; a non-loopback bind needs that or SHIORI_FEED_BIND_BEHIND_PROXY=1, or it refuses to start
    proxy      a proxy in front signs people in (nginx auth_request, say); only SHIORI_FEED_TRUSTED_PROXIES may connect
    open       no check; only on a loopback bind, or with SHIORI_FEED_BIND_BEHIND_PROXY=1 (a published 127.0.0.1 port)

Hister gets `Origin: hister://` and the owner's token from SHIORI_FEED_HISTER_TOKEN_FILE (X-Access-Token), never a
redirect. The log carries method, path and status only: never a query, a title or a token. Stdlib only.
"""
import collections
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

VERSION = "0.1.0"

FEED_PATHS = {"/shiori/feed", "/feed"}
HEALTH_PATHS = {"/shiori/healthz", "/healthz"}
FEED_LIMIT, Q_MAX, TITLE_MAX, LABEL_MAX, LABELS_MAX, DESC_MAX = 50, 500, 200, 100, 20, 500
HISTER_TIMEOUT = 20                 # seconds for one Hister search
HISTER_MAX = 16 << 20               # the most of Hister's answer read (50 documents with their text)
CLIENT_TIMEOUT = 30                 # seconds a client may take to send its request
CTRL = re.compile("[\x00-\x08\x0b\x0c\x0e-\x1f\x7f￾￿]")     # not allowed in XML 1.0 (and DEL)
TOKEN_RE = re.compile(r"[\x21-\x7e]{1,4096}")
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
        if self.auth == "open":
            return True
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
            return self.reply(502, b"Hister failed\n")
        except (OSError, ValueError) as e:                  # refused, timeouts, too big, bad JSON
            log("hister: %s" % type(e).__name__)
            return self.reply(502, b"Hister failed\n")
        self.reply(200, rss(self.cfg, q, title, docs), "application/rss+xml; charset=utf-8", "private, max-age=300")

    def do_GET(self):
        path = self.path.split("?", 1)[0]
        if path in FEED_PATHS:
            return self.feed()
        if path in HEALTH_PATHS:
            return self.reply(200, b"ok\n")
        self.reply(404, b"not found\n")

    def do_HEAD(self):
        path = self.path.split("?", 1)[0]
        self.reply(200 if path in HEALTH_PATHS else 405 if path in FEED_PATHS else 404)

    def refuse(self):
        path = self.path.split("?", 1)[0]
        self.reply(405 if path in FEED_PATHS | HEALTH_PATHS else 404, headers=[("Allow", "GET")])

    do_PUT = do_POST = do_DELETE = do_PATCH = do_OPTIONS = refuse

    def log_request(self, code="-", size="-"):        # method, path and status: never the query (searches)
        path = CTRL.sub("?", (getattr(self, "path", "") or "").split("?", 1)[0])[:200]
        if path not in HEALTH_PATHS:
            log("%s %s %s" % (CTRL.sub("?", self.command or "-")[:16], path, getattr(code, "value", code)))

    def log_error(self, fmt, *args):                   # the code only, never the raw request line
        log("error %s" % (args[0] if args else "-"))

    def log_message(self, fmt, *args):
        pass


def make_server(cfg, bind=None, port=None):
    server = ThreadingHTTPServer((cfg.bind if bind is None else bind, cfg.port if port is None else port), Handler)
    server.daemon_threads = True
    server.cfg, server.limiter = cfg, Limiter(cfg.per_minute)
    return server


def main():
    cfg = Config()
    server = make_server(cfg)
    log("%s: listening on %s:%d, auth %s%s, Hister %s%s" % (
        VERSION, cfg.bind, cfg.port, cfg.auth,
        "" if cfg.auth != "tailscale" else " (%s)" % (",".join(sorted(cfg.users)) or "NOBODY: set SHIORI_FEED_USERS"),
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
