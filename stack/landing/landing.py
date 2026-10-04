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
from vaultkit import changelog, identity, read_secret   # noqa: E402
from vaultkit import shell as house              # noqa: E402

with open(os.path.join(HERE, "VERSION"), encoding="utf-8") as _f:
    VERSION = _f.read().strip()

STATIC_TYPES = {"landing.css": "text/css", "landing.js": "text/javascript"}
SHARED_UI = {"machiya.css": "text/css", "machiya.js": "text/javascript"}
ICONS = {"machiya.svg": "image/svg+xml", "machiya-small.svg": "image/svg+xml", "machiya-apple-180.png": "image/png",
         "machiya-192.png": "image/png", "machiya-512.png": "image/png", "machiya-maskable-512.png": "image/png"}
TRUE = ("1", "on", "true", "yes")
CHANGELOG_APPS = ("kura", "konbini", "niwa", "machiya-mcp", "smallweb")    # the apps that serve GET /api/changelog
CHANGELOG_TYPES = ("text/markdown", "text/plain")


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
        allowed = ("tailscale", "open", "header") if has_file else ("tailscale", "open")
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
        self.users = csv(env.get("LANDING_USERS"))
        self.hosts = {h.lower() for h in csv(env.get("LANDING_ALLOWED_HOSTS") or "localhost,127.0.0.1,[::1]")}
        self.links = house.rooms(env)                     # MACHIYA_ROOMS: the rooms' and engines' public addresses
        self.targets = dict(self.links)                   # where each app is polled
        self.targets.update(pairs(env.get("LANDING_APPS")))     # more apps: machiya-mcp=…,smallweb=…
        self.targets.update(pairs(env.get("LANDING_PROBES")))   # a different address to poll (hister=http://hister:4433)
        mirror = (env.get("LANDING_MIRROR_STATUS") or "").strip()
        if mirror:
            self.targets["vault-mirror"] = mirror
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
        return target.rstrip("/") + "/api/changelog" if key in CHANGELOG_APPS and target.startswith(("http://", "https://")) else ""

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
            sections = deploys.parse(body.decode("utf-8", "replace")) if ctype.split(";")[0].strip().lower() in CHANGELOG_TYPES else {}
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

        def allowed(self):
            """(ok, status, reason): the owner gate."""
            if config.identity is not None:
                # 401: no proof or a bad one (never passed over for another); 403: proven, without `landing` `read`
                who = config.identity.resolve(self.headers, self.client_address[0] if self.client_address else "")
                if not who:
                    return False, who.status, who.error or "no identity"
                if not who.principal.can("landing", "read"):
                    return False, 403, "not allowed here"
                return True, 200, ""
            if config.auth == "open":
                host = (self.headers.get("Host") or "").strip().lower()
                name = host.rsplit(":", 1)[0] if not host.endswith("]") else host
                return (name in config.hosts or host in config.hosts), 403, "unknown Host (LANDING_ALLOWED_HOSTS)"
            login = identity.ambient("tailscale", self.headers)
            ok = login is not None and ("*" in config.users or login.name.lower() in {u.lower() for u in config.users})
            return ok, 403, "not an allowed user"

        def do_GET(self):
            url = urlsplit(self.path)
            path, query = unquote(url.path), parse_qs(url.query)
            if path == "/healthz":                 # the container's health check: no data, no identity
                return self.send_json(200, {"ok": True, "version": VERSION})
            ok, status, reason = self.allowed()
            if not ok:
                return self.send(status, "%s\n" % reason, "text/plain; charset=utf-8", [("Cache-Control", "no-store")])
            ctx = house.prefs(self.headers.get("Cookie"))
            if path == "/api/changelog":           # this page's own CHANGELOG.md, behind the same gate as /api/status
                status, body, headers = changelog.handle(os.path.join(HERE, "CHANGELOG.md"), self.headers)
                return self.send(status, body, dict(headers).pop("Content-Type"),
                                 [(k, v) for k, v in headers if k != "Content-Type"])
            if path.startswith("/static/"):
                return self.static(path[8:], query)
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
                return self.send(200, render.settings(ctx, config.links, VERSION), headers=[("Cache-Control", "no-store")])
            if path == "/status":
                snap = landing.current()
                return self.send(200, render.page(ctx, snap, landing.history, landing.logs, config.links, config.targets,
                                                  landing.now(), config.tz), headers=[("Cache-Control", "no-store")])
            if path == "/":
                snap = landing.current()
                return self.send(200, render.home(ctx, snap, config.links, config.targets, config.search, landing.now(),
                                                  config.tz), headers=[("Cache-Control", "no-store")])
            if path == "/api/today":
                return self.send_json(200, landing.current().get("today") or {})
            return self.send(404, render.message(ctx, config.links, "Not Found", "There's nothing here."),
                             headers=[("Cache-Control", "no-store")])

        do_HEAD = do_GET

        def do_POST(self):
            self.send(405, "", "text/plain", [("Allow", "GET, HEAD")])

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
