"""Machiya's landing page: one front door for the stack (docs/services/landing.md).

`/` is the launcher: Search everything, the rooms, and Today (what the rooms say is going on; today.py). `/status` links
every room, engine and stack service, and shows for each whether it answers, its version (and vendored vaultkit), how
fresh the vault's sync is, and what was deployed lately. It polls the apps' own status endpoints in the
background (GETs only, short timeouts, every LANDING_POLL seconds) and serves the last answers from memory; an app with
no address shows "Not in this stack". Stdlib Python plus the vendored vaultkit (the shell, the auth helpers).

Owner-only, in the rooms' auth shape: LANDING_AUTH=tailscale (the default: Tailscale-User-Login must be in
LANDING_USERS; `*` = anyone the tailnet lets through; unset = nobody) | open (no check; localhost only, with a Host
allow-list). With Machiya's identity file (MACHIYA_IDENTITY_FILE) callers are principals instead and need the
`landing` `read` grant (the owner has it); LANDING_AUTH=header then names a trusted proxy's login header.

What a deploy brought comes from each app's own GET /api/changelog (its CHANGELOG.md); an app that doesn't serve
one yet shows its versions only. LANDING_CHANGELOGS overrides the address per app.
"""
import json
import os
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, unquote, urlsplit

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import deploys                                   # noqa: E402
import probes                                    # noqa: E402
import render                                    # noqa: E402
import today as todaymod                         # noqa: E402
from vaultkit import changelog, histerauth, identity, read_secret, signin   # noqa: E402
from vaultkit import shell as house              # noqa: E402

with open(os.path.join(HERE, "VERSION"), encoding="utf-8") as _f:
    VERSION = _f.read().strip()

STATIC_TYPES = {"landing.css": "text/css", "landing.js": "text/javascript"}
SHARED_UI = {"machiya.css": "text/css", "machiya.js": "text/javascript"}
ICONS = {"machiya.svg": "image/svg+xml", "machiya-small.svg": "image/svg+xml", "machiya-apple-180.png": "image/png",
         "machiya-192.png": "image/png", "machiya-512.png": "image/png", "machiya-maskable-512.png": "image/png"}
TRUE = ("1", "on", "true", "yes")
CHANGELOG_APPS = ("kura", "konbini", "niwa", "machiya-mcp", "smallweb")    # the apps that serve GET /api/changelog
CHANGELOG_PATHS = {"shiori": "/_shiori/CHANGELOG.md"}                      # 0.3.0: Shiori's hosted build serves its own


def pairs(raw):
    """'a=x,b=y' -> {a: x, b: y} (keys lower-cased, trailing slashes dropped)."""
    out = {}
    for part in (raw or "").split(","):
        if "=" in part:
            k, v = part.split("=", 1)
            if k.strip() and v.strip():
                out[k.strip().lower()] = v.strip().rstrip("/")
    return out


def csv(raw):
    return {x.strip() for x in (raw or "").split(",") if x.strip()}


class Config:
    def __init__(self, env):
        has_file = bool((env.get("MACHIYA_IDENTITY_FILE") or "").strip())
        self.auth = (env.get("LANDING_AUTH") or "tailscale").strip().lower()
        allowed = ("tailscale", "open", "hister", "header") if has_file else ("tailscale", "open", "hister")
        if self.auth not in allowed:
            raise SystemExit("machiya-landing: LANDING_AUTH must be %s, not %r%s" % (
                " or ".join(allowed), self.auth, " (header needs MACHIYA_IDENTITY_FILE)" if self.auth == "header" else ""))
        self.bind = (env.get("LANDING_BIND") or "0.0.0.0").strip()
        self.port = int(env.get("LANDING_PORT") or "8080")
        behind = (env.get("LANDING_BIND_BEHIND_PROXY") or "").strip().lower() in TRUE
        try:
            identity.check_bind(self.auth, self.bind, behind)
        except identity.IdentityError as e:
            raise SystemExit("machiya-landing: %s" % str(e).replace("<ROOM>", "LANDING"))
        self.identity = None
        if has_file:
            if "landing" not in identity.ACTIONS:
                raise SystemExit("machiya-landing: MACHIYA_IDENTITY_FILE is set, but this vaultkit (%s) has no `landing` "
                                 "room to grant; unset it (LANDING_USERS gates the page) or use a vaultkit that has one"
                                 % render.vaultkit_version())
            try:
                self.identity = identity.load_for("landing", env, bind=self.bind)
            except identity.IdentityError as e:
                raise SystemExit("machiya-landing: identity: %s" % e)
        # 0.3.0: Hister's users as the sign-in, like the rooms (vaultkit.histerauth): LANDING_AUTH=hister with
        # LANDING_AUTH_SIGNIN_URL, LANDING_HISTER_USERS, LANDING_PUBLIC_URL, LANDING_AUTH_URL and the tailscale fallback
        # (LANDING_USERS; LANDING_BIND_BEHIND_PROXY=1), so the status page still opens when sign-in is down.
        self.public_url = (env.get("LANDING_PUBLIC_URL") or "").strip().rstrip("/")
        if self.public_url and not self.public_url.startswith(("https://", "http://")):
            raise SystemExit("machiya-landing: LANDING_PUBLIC_URL must be an http(s) address, not %r" % self.public_url)
        self.secure = self.public_url.startswith("https://") if self.public_url else self.auth != "open"
        self.hister_auth = None
        if self.auth == "hister":
            try:
                self.hister_auth = histerauth.load_for("landing", env, bind=self.bind, secure=self.secure)
            except identity.IdentityError as e:
                raise SystemExit("machiya-landing: %s" % e)
        state_dir = os.path.dirname((env.get("LANDING_STATE") or "").strip())
        self.prefs_path = (env.get("LANDING_PREFS") or "").strip() or (os.path.join(state_dir, "prefs.sqlite3") if state_dir else "")
        self.users = csv(env.get("LANDING_USERS"))
        self.hosts = {h.lower() for h in csv(env.get("LANDING_ALLOWED_HOSTS") or "localhost,127.0.0.1,[::1]")}
        self.links = house.rooms(env)                     # MACHIYA_ROOMS: the rooms' and engines' public addresses
        self.targets = dict(self.links)                   # where each app is polled
        self.targets.update(pairs(env.get("LANDING_APPS")))     # more apps: machiya-mcp=…,smallweb=…
        self.targets.update(pairs(env.get("LANDING_PROBES")))   # a different address to poll (hister=http://hister:4433)
        mirror = (env.get("LANDING_MIRROR_STATUS") or "").strip()
        if mirror:
            self.targets["vault-mirror"] = mirror
        self.search_counts = (env.get("LANDING_SEARCH_COUNTS") or "").strip()   # web searches per day/month/year (a file)
        feeds = (env.get("LANDING_FEED_STATUS") or "").strip()          # feed-import's status.json (read-only mount)
        if feeds:
            self.targets["feed-import"] = feeds
        # The launcher's search pill: a form that GETs <url>?q=… (Shiori's search page, another origin); unset: no pill
        self.search = (env.get("LANDING_SEARCH_URL") or "").strip()
        if self.search and (not self.search.startswith(("http://", "https://"))
                            or any(c.isspace() or c in '<>"\'' for c in self.search)):
            raise SystemExit("machiya-landing: LANDING_SEARCH_URL must be a plain http(s) address, not %r" % self.search)
        origin = urlsplit(self.search)
        self.csp = house.CSP.replace("form-action 'self'", "form-action 'self' %s://%s" % (origin.scheme, origin.netloc)) \
            if self.search else house.CSP
        self.targets = {k: v for k, v in self.targets.items() if k in probes.NAMES}
        self.poll = max(15, int(env.get("LANDING_POLL") or "60"))
        self.timeout = max(0.5, float(env.get("LANDING_TIMEOUT") or "3"))
        self.token = self.secret(env.get("LANDING_TOKEN_FILE"), "LANDING_TOKEN_FILE")
        self.hister_token = self.secret_file(env.get("LANDING_HISTER_TOKEN_FILE"), "LANDING_HISTER_TOKEN_FILE")
        self.changelogs = {k: v for k, v in pairs(env.get("LANDING_CHANGELOGS")).items() if k in probes.NAMES}  # overrides
        self.changelog_token = self.secret(env.get("LANDING_CHANGELOG_TOKEN_FILE"), "LANDING_CHANGELOG_TOKEN_FILE")
        self.changelog_poll = max(60, int(env.get("LANDING_CHANGELOG_POLL") or "900"))
        self.state = (env.get("LANDING_STATE") or "").strip()
        self.tz = None
        tzname = (env.get("LANDING_TZ") or "").strip()
        if tzname:
            try:
                from zoneinfo import ZoneInfo
                self.tz = ZoneInfo(tzname)
            except Exception:
                raise SystemExit("machiya-landing: LANDING_TZ %r is not a known time zone" % tzname)

    @staticmethod
    def secret(path, name):
        """A token read from a file (never the environment); "" when unset. Set but empty or odd: refuse to start."""
        path = (path or "").strip()
        if not path:
            return ""
        token = read_secret(path)
        if not token or any(c.isspace() or ord(c) < 33 or ord(c) > 126 for c in token) or len(token) > 4096:
            raise SystemExit("machiya-landing: %s: no token on the first line of %s" % (name, path))
        return token

    @staticmethod
    def secret_file(path, name):
        """A token file re-read when it changes (probes.SecretFile); None when unset. Set but missing, empty or odd:
        refuse to start (never echoing the file)."""
        path = (path or "").strip()
        if not path:
            return None
        secret = probes.SecretFile(path) if os.path.isfile(path) else None
        if secret is None or not secret.value:
            raise SystemExit("machiya-landing: %s: no token on the first line of %s" % (name, path))
        return secret


class Landing:
    """The poller and its last answers."""

    def __init__(self, config, now=time.time):
        self.config, self.now = config, now
        self.lock = threading.Lock()
        self.snap = None
        self.history = deploys.History(config.state)
        self.logs, self.logs_at, self.etags, self.fetched, self.missing = {}, 0.0, {}, {}, {}
        self.board_behind_since = None

    def poll(self):
        now = self.now()
        c = self.config
        keys = [k for k, _, _, _ in probes.APPS]
        with ThreadPoolExecutor(max_workers=len(keys)) as pool:
            found = dict(zip(keys, pool.map(lambda k: probes.probe(k, c.targets.get(k, ""), now, c.timeout, c.token, c.hister_token), keys)))
        kura, konbini = (found["kura"].get("data") or {}), (found["konbini"].get("data") or {})
        if kura.get("head") and konbini.get("head") and kura["head"] != konbini["head"]:
            self.board_behind_since = self.board_behind_since or now
        else:
            self.board_behind_since = None
        if c.search_counts and found["shiori"].get("state") not in ("absent", "down"):
            found["shiori"]["more"] = probes.search_counts(c.search_counts)
        feeds = found["feed-import"].get("data") or {}
        if feeds.get("added_total") is not None:              # "N added in the last day", from the page's own samples
            feeds["added_day"], feeds["added_since"] = self.history.sample("feed-import.added", feeds["added_total"], now)
        rows = probes.sync_rows(found, now, self.board_behind_since)
        try:
            today = todaymod.gather(c, found, now)
        except Exception as e:                                   # Today is a nicety: never the reason a poll fails
            sys.stderr.write("machiya-landing: today: %s\n" % type(e).__name__)
            today = {}
        snap = {"checked": now, "version": VERSION, "apps": found, "sync": rows, "overall": probes.overall(found, rows),
                "today": today}
        self.history.observe(found, now)
        self.fetch_changelogs(found, now)
        with self.lock:
            self.snap = snap
        return snap

    def changelog_url(self, key):
        """Where an app's changelog is: LANDING_CHANGELOGS' override, else <its address>/api/changelog; "" for none."""
        if key in self.config.changelogs:
            return self.config.changelogs[key]
        target = self.config.targets.get(key, "")
        if not target.startswith(("http://", "https://")):
            return ""
        if key in CHANGELOG_PATHS:
            return target.rstrip("/") + CHANGELOG_PATHS[key]
        return target.rstrip("/") + "/api/changelog" if key in CHANGELOG_APPS else ""

    def fetch_changelogs(self, apps, now):
        """Each app's changelog, every LANDING_CHANGELOG_POLL seconds, and at once (at most once a minute) when the app
        answers with a version its changelog doesn't have yet. A 304 or a failure keeps the last good copy; an app
        without the endpoint (404, or not markdown) shows its versions only."""
        due = now - self.logs_at >= self.config.changelog_poll
        if due:
            self.logs_at = now
        for key in probes.NAMES:
            url = self.changelog_url(key)
            app = apps.get(key) or {}
            if not url or app.get("state") in ("absent", "down"):
                continue
            stale = app.get("version") and app["version"] not in self.logs.get(key, {}) \
                and self.missing.get(key) != app["version"]          # not asked again for a version it lacked
            if not (due or (stale and now - self.fetched.get(key, 0) >= 60)):
                continue
            self.fetched[key] = now
            if key in self.config.changelogs:
                headers = {"Authorization": "token " + self.config.changelog_token} \
                    if self.config.changelog_token and urlsplit(url).scheme == "https" else {}
            else:
                headers = probes.auth_headers(key, url, self.config.token)
            headers["Accept"] = "text/markdown, text/plain;q=0.9"
            if self.etags.get(key) and key in self.logs:
                headers["If-None-Match"] = self.etags[key]
            try:
                _, ctype, body, tag = probes.fetch_text(url, headers, self.config.timeout * 2)
            except probes.FetchError as e:
                if str(e) != "HTTP 304":
                    self.missing[key] = app.get("version")
                if str(e) not in ("HTTP 304", "HTTP 404"):
                    sys.stderr.write("machiya-landing: changelog %s: %s\n" % (key, e))
                continue
            # markdown, plain text or a static server's octet-stream; never a page (an SPA's fallback, a proxy's 404)
            sections = deploys.parse(body.decode("utf-8", "replace")) if "html" not in ctype.lower() else {}
            if sections:
                self.logs[key], self.etags[key] = sections, tag
            if app.get("version") not in sections:
                self.missing[key] = app.get("version")

    def current(self):
        with self.lock:
            snap = self.snap
        return snap or self.poll()

    def run(self):
        while True:
            try:
                self.poll()
            except Exception as e:          # the loop must survive anything; the page shows the last good answers
                sys.stderr.write("machiya-landing: poll failed: %s\n" % type(e).__name__)
            time.sleep(self.config.poll)

    def public(self, snap):
        """/api/status: the snapshot without the apps' raw details."""
        apps = {k: {x: a[x] for x in ("state", "version", "vaultkit", "facts", "error", "ms") if x in a}
                for k, a in snap["apps"].items()}
        return {"ok": True, "version": VERSION, "vaultkit": render.vaultkit_version(), "checked": int(snap["checked"]),
                "overall": snap["overall"], "apps": apps,
                "sync": [{k: r.get(k) for k in ("key", "label", "source", "state", "text", "at", "error")} for r in snap["sync"]],
                "deploys": self.history.recent(self.now())}


def make_handler(landing):
    config = landing.config

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"
        server_version = "machiya-landing"

        def log_message(self, *a):
            pass

        def send(self, code, body=b"", ctype="text/html; charset=utf-8", headers=()):
            body = body.encode("utf-8") if isinstance(body, str) else body
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            for k, v in headers:
                self.send_header(k, v)
            if ctype.startswith("text/html"):
                for k, v in house.security_headers(config.csp):
                    self.send_header(k, v)
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(body)

        def send_json(self, code, obj):
            self.send(code, json.dumps(obj, ensure_ascii=False, indent=1), "application/json; charset=utf-8",
                      [("Cache-Control", "no-store")])

        _hres = None                       # the Hister sign-in's answer, worked out once per request

        def handle_one_request(self):
            """Every request starts with no sign-in answer (0.3.1). HTTP/1.1 keeps a connection, and one handler,
            for many requests, and Tailscale Serve sends different people's requests down the same connection: an
            answer kept from the last request (a signed-out redirect, or worse, someone's principal) must never
            decide this one."""
            self._hres = None
            return super().handle_one_request()

        def browser_page(self):
            """A browser asking for a page (not an API, not a write): a signed-out one is sent to sign in."""
            return self.command in ("GET", "HEAD") and not urlsplit(self.path).path.startswith("/api/") \
                and "text/html" in (self.headers.get("Accept") or "")

        def hres(self):
            if self._hres is None:
                self._hres = config.hister_auth.resolve(self.headers, is_page=self.browser_page(), path=self.path)
            return self._hres

        def host_ok(self):
            if config.auth != "open":
                return True
            host = (self.headers.get("Host") or "").strip().lower()
            name = host.rsplit(":", 1)[0] if not host.endswith("]") else host
            return name in config.hosts or host in config.hosts

        def gate(self):
            """(principal or None, status, reason): the owner gate. The principal is the owner (or, with the identity
            file, a principal with `landing` `read`)."""
            if not self.host_ok():
                return None, 403, "unknown Host (LANDING_ALLOWED_HOSTS)"
            if config.identity is not None:
                # 401: no proof or a bad one (never passed over for another); 403: proven, without `landing` `read`
                who = config.identity.resolve(self.headers, self.client_address[0] if self.client_address else "")
                if not who:
                    return None, who.status, who.error or "no identity"
                if not who.principal.can("landing", "read"):
                    return None, 403, "not allowed here"
                return who.principal, 200, ""
            if config.hister_auth is not None:
                res = self.hres()
                return res.principal, res.status, res.reason
            if config.auth == "open":
                return identity.OPEN_OWNER, 200, ""
            login = identity.ambient("tailscale", self.headers)
            ok = login is not None and ("*" in config.users or login.name.lower() in {u.lower() for u in config.users})
            return (login if ok else None), 403, "not an allowed user"

        def allowed(self):
            p, status, reason = self.gate()
            return p is not None, status, reason

        def refuse(self, status, reason):
            if config.hister_auth is not None and self.host_ok():   # a 302 to sign in, a 401 page or JSON, 403, 503
                code, headers, body = config.hister_auth.respond(self.hres(), is_page=self.browser_page(),
                                                                 ctx=house.prefs(self.headers.get("Cookie")))
                ctype = dict(headers).get("Content-Type", "text/plain")
                return self.send(code, body, ctype, [(k, v) for k, v in headers if k != "Content-Type"])
            return self.send(status, "%s\n" % reason, "text/plain; charset=utf-8", [("Cache-Control", "no-store")])

        def context(self, principal):
            """Theme, text size and palette (cookies), plus: who is signed in (the header's person button and
            Settings' Account), the fallback banner, and /api/prefs when there is somewhere to keep preferences."""
            ctx = house.prefs(self.headers.get("Cookie"))
            ctx.signin = config.hister_auth is not None
            ctx.banner = config.hister_auth is not None and self._hres is not None and self._hres.banner
            via = getattr(principal, "via", "") if principal is not None else ""
            ctx.who = principal.name if principal is not None and via not in ("open", "") else ""
            ctx.account = {"name": ctx.who, "via": via,
                           "signout": config.hister_auth is not None and via in ("hister", "app", "token")} if ctx.who else None
            ctx.prefs_url = "/api/prefs" if principal is not None and config.prefs_path else ""
            return ctx

        def origins(self):
            """Where a cookie-borne prefs PUT or sign-out may come from: LANDING_PUBLIC_URL; else, in open mode, this
            request's own Host (host_ok already checked it)."""
            if config.public_url:
                return [config.public_url]
            host = (self.headers.get("Host") or "").strip()
            return ["http://" + host, "https://" + host] if config.auth == "open" and host and self.host_ok() else []

        def reply(self, status, headers, body):
            ctype = dict(headers).get("Content-Type", "application/json")
            return self.send(status, body, ctype, [(k, v) for k, v in headers if k != "Content-Type"])

        def body(self, limit):
            try:
                length = int(self.headers.get("Content-Length") or 0)
            except ValueError:
                length = -1
            if length < 0 or length > limit:
                self.close_connection = True
                return None
            return self.rfile.read(length) if length else b""

        def do_GET(self):
            url = urlsplit(self.path)
            path, query = unquote(url.path), parse_qs(url.query)
            if path == "/healthz":                 # the container's health check: no data, no identity
                return self.send_json(200, {"ok": True, "version": VERSION})
            if path == "/api/changelog":           # this page's own CHANGELOG.md: open, like every app's (0.3.0)
                status, body, headers = changelog.handle(os.path.join(HERE, "CHANGELOG.md"), self.headers)
                return self.send(status, body, dict(headers).pop("Content-Type"),
                                 [(k, v) for k, v in headers if k != "Content-Type"])
            if path.startswith("/static/"):        # the shared UI and icons: the sign-in pages need them too
                return self.static(path[8:], query)
            principal, status, reason = self.gate()
            if principal is None:
                return self.refuse(status, reason)
            ctx = self.context(principal)
            cookies = [("Set-Cookie", c) for c in (self._hres.cookies if self._hres is not None else [])]
            if path == "/api/prefs":
                if not config.prefs_path:
                    return self.send_json(404, {"error": "not found"})
                return self.reply(*signin.handle_prefs(prefs_store(config.prefs_path), principal, "GET", self.headers,
                                                       b"", config.secure, self.origins()))
            if path == "/api/status":
                return self.send_json(200, landing.public(landing.current()))
            if path == "/manifest.webmanifest":
                return self.send(200, json.dumps(manifest(ctx, self.headers), indent=1), "application/manifest+json",
                                 [("Cache-Control", "no-cache"), ("Vary", house.MANIFEST_VARY)])
            if path == "/theme":                   # the no-JavaScript fallback for /settings' Appearance
                theme = (query.get("set") or ["system"])[0]
                theme = {"auto": "system"}.get(theme, theme)
                theme = theme if theme in ("night", "day", "system") else "system"
                cookies = [("Set-Cookie", "theme=%s; path=/; max-age=31536000; samesite=lax" % theme)]
                if house.COOKIE_DOMAIN:
                    cookies.append(("Set-Cookie", "machiya_theme=%s; domain=%s; path=/; max-age=31536000; samesite=lax"
                                    % (theme, house.COOKIE_DOMAIN)))
                return self.send(302, "", "text/plain", [("Location", "/settings")] + cookies)
            if path == "/settings":
                return self.send(200, render.settings(ctx, config.links, VERSION), headers=[("Cache-Control", "no-store")] + cookies)
            if path == "/status":
                snap = landing.current()
                return self.send(200, render.page(ctx, snap, landing.history, landing.logs, config.links, config.targets,
                                                  landing.now(), config.tz), headers=[("Cache-Control", "no-store")] + cookies)
            if path == "/":
                snap = landing.current()
                return self.send(200, render.home(ctx, snap, config.links, config.targets, config.search, landing.now(),
                                                  config.tz), headers=[("Cache-Control", "no-store")] + cookies)
            if path == "/api/today":
                return self.send_json(200, landing.current().get("today") or {})
            return self.send(404, render.message(ctx, config.links, "Not Found", "There's nothing here."),
                             headers=[("Cache-Control", "no-store")])

        do_HEAD = do_GET

        def do_POST(self):
            path = unquote(urlsplit(self.path).path)
            if path == "/signout" and config.hister_auth is not None:     # before the gate: works while sign-in is down
                if self.body(signin.MAX_FORM) is None:
                    return self.send(413, "request body too large\n", "text/plain")
                if not signin.same_origin(self.headers, config.secure, self.origins()):
                    return self.send(403, "cross-site sign-out refused\n", "text/plain", [("Cache-Control", "no-store")])
                _, cookies = config.hister_auth.signout(self.headers)
                return self.send(303, "", "text/plain", [("Cache-Control", "no-store"), ("Location", "/")]
                                 + [("Set-Cookie", c) for c in cookies])
            self.send(405, "", "text/plain", [("Allow", "GET, HEAD, PUT")])

        def do_PUT(self):
            path = unquote(urlsplit(self.path).path)
            principal, status, reason = self.gate()
            if principal is None:
                return self.refuse(status, reason)
            if path != "/api/prefs" or not config.prefs_path:
                return self.send_json(404, {"error": "not found"})
            body = self.body(signin.MAX_PREFS)
            if body is None:
                return self.send_json(413, {"error": "request body too large"})
            return self.reply(*signin.handle_prefs(prefs_store(config.prefs_path), principal, "PUT", self.headers,
                                                   body, config.secure, self.origins()))

        def static(self, name, query):
            if name in STATIC_TYPES:
                path, ctype = os.path.join(render.STATIC_DIR, name), STATIC_TYPES[name]
            elif name in SHARED_UI:
                path, ctype = os.path.join(house.UI_DIR, name), SHARED_UI[name]
            elif name.startswith("icons/") and name[6:] in ICONS:
                path, ctype = os.path.join(render.STATIC_DIR, "icons", name[6:]), ICONS[name[6:]]
            else:
                return self.send(404, "not found\n", "text/plain")
            cache = "public, max-age=31536000, immutable" if query.get("v") else "max-age=300"
            with open(path, "rb") as f:
                return self.send(200, f.read(), ctype, [("Cache-Control", cache)])

    return Handler


_PREFS = {}
_PREFS_LOCK = threading.Lock()


def prefs_store(path):
    """vaultkit.signin.Prefs on `path` (LANDING_PREFS, else prefs.sqlite3 beside LANDING_STATE), opened once."""
    with _PREFS_LOCK:
        if path not in _PREFS:
            _PREFS[path] = signin.Prefs(path)
        return _PREFS[path]


def manifest(ctx, headers):
    return dict({
        "id": "/", "name": "Machiya", "short_name": "Machiya", "description": "The rooms of Machiya and how they are doing",
        "start_url": "/", "scope": "/", "display": "standalone",
        "icons": [{"src": "/static/icons/machiya-192.png", "sizes": "192x192", "type": "image/png"},
                  {"src": "/static/icons/machiya-512.png", "sizes": "512x512", "type": "image/png"},
                  {"src": "/static/icons/machiya-maskable-512.png", "sizes": "512x512", "type": "image/png", "purpose": "maskable"}],
    }, **house.manifest_colors(ctx.theme, headers, ctx.palette))


def main():
    config = Config(os.environ)
    if config.auth == "open":
        sys.stderr.write("machiya-landing: LANDING_AUTH=open, no identity check: use it on localhost only\n")
    elif config.hister_auth is not None:
        h = config.hister_auth
        sys.stderr.write("machiya-landing: LANDING_AUTH=hister: %s, sign-in %s, fallback %s%s\n" % (
            ",".join(sorted(h.users)), h.signin, h.fallback,
            " (%s)" % ",".join(sorted(h.fallback_users)) if h.fallback == "tailscale" else ""))
    elif not config.users and config.identity is None:
        sys.stderr.write("machiya-landing: LANDING_USERS is empty, so every request will be refused\n")
    landing = Landing(config)
    if landing.history.error:
        sys.stderr.write("machiya-landing: %s\n" % landing.history.error)
    threading.Thread(target=landing.run, daemon=True).start()
    sys.stderr.write("machiya-landing %s on %s:%d, apps: %s\n" % (VERSION, config.bind, config.port,
                                                                   ", ".join(sorted(config.targets)) or "none"))
    ThreadingHTTPServer((config.bind, config.port), make_handler(landing)).serve_forever()


if __name__ == "__main__":
    main()
