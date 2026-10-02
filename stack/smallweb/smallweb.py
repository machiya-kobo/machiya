"""smallweb: Machiya's gateway to the small web, for Shiori (docs/contracts/smallweb-api.md). Stdlib only.

- GET /api/search?q=&source=tlgs,kennedy,veronica&page=  one query per engine (TLGS and Kennedy over Gemini,
  Veronica-2 over Gopher), in parallel, merged and deduped. Only on a user's search: never autocomplete, never
  prefetch, never following links on its own. Cached for an hour, capped per engine per hour, one connection per
  engine host at least 1.5 s apart, 44 SLOW DOWN honoured.
- GET /page?url=gemini://…|gopher://…  the page as HTML (no script; links go back through /page). A page read here
  is saved to Hister under its canonical gemini:// or gopher:// URL (not the proxy URL), without a label.
- GET /  a plain search page over the same data. GET /api/status  for the probe (open).

Egress goes through SMALLWEB_SOCKS (e.g. socks5h://proxy:1080) when set, so capsule hosts never see the server's own IP.
Gemini certificates are trusted on first use (known hosts in the sqlite file under SMALLWEB_DATA). Private, in
Machiya's auth shape: SMALLWEB_AUTH=tailscale (SMALLWEB_USERS) | open, SMALLWEB_BIND.
"""
import concurrent.futures
import hashlib
import json
import os
import re
import sys
import threading
import time
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, quote, urljoin, urlsplit

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import engines   # noqa: E402
import render    # noqa: E402
import smolnet   # noqa: E402
from store import Store   # noqa: E402

VERSION = "0.1.2"
HERE = os.path.dirname(os.path.abspath(__file__))


def auth_mode(value):
    value = (value or "tailscale").strip().lower()
    if value not in ("tailscale", "open"):
        raise SystemExit("smallweb: SMALLWEB_AUTH must be tailscale or open, not %r" % value)
    return value


def socks_addr(value):
    """socks5h://host:port -> "host:port" ("" = connect directly)."""
    value = (value or "").strip()
    if not value:
        return ""
    u = urlsplit(value)
    if u.scheme not in ("socks5h", "socks5") or not u.hostname or not u.port:
        raise SystemExit("smallweb: SMALLWEB_SOCKS must look like socks5h://host:port")
    return "%s:%d" % (u.hostname, u.port)


AUTH = auth_mode(os.environ.get("SMALLWEB_AUTH"))
BIND = os.environ.get("SMALLWEB_BIND", "0.0.0.0").strip() or "0.0.0.0"
PORT = int(os.environ.get("SMALLWEB_PORT", "8080"))
USERS = set(filter(None, (u.strip() for u in os.environ.get("SMALLWEB_USERS", "").split(","))))
PUBLIC_URL = os.environ.get("SMALLWEB_PUBLIC_URL", "").rstrip("/")
SOCKS = socks_addr(os.environ.get("SMALLWEB_SOCKS"))
HISTER = os.environ.get("SMALLWEB_HISTER_URL", "").rstrip("/")
# Origins besides smallweb's own that may POST /api/save: Shiori's native apps send Origin: hister://, and Shiori's
# hosted pages reach smallweb through a reverse proxy, so their browser Origin is that site's (list it in SMALLWEB_ORIGINS).
ORIGINS = {o.strip().rstrip("/") for o in ["hister://"] + os.environ.get("SMALLWEB_ORIGINS", "").split(",") if o.strip()}
DATA = os.environ.get("SMALLWEB_DATA", "/data")

SEARCH_TTL, PAGE_TTL, ROBOTS_TTL = 3600, 600, 86400
PER_HOUR = int(os.environ.get("SMALLWEB_PER_HOUR", "30"))     # searches per engine per hour
ENGINE_GAP = 1.5                                              # seconds between requests to one engine host
BUDGET = 12                                                   # seconds a search waits for the engines
MAX_REDIRECTS = 5

store = Store(os.path.join(DATA, "smallweb.sqlite3"))
polite = smolnet.Polite({e["host"]: ENGINE_GAP for e in engines.ENGINES.values()})
health = {k: {"last_ok": None, "last_error": None} for k in engines.ORDER}
hister_state = {"last_error": None, "fails": 0}      # fails: consecutive saves that didn't reach Hister
pool = concurrent.futures.ThreadPoolExecutor(max_workers=8)
save_pool = concurrent.futures.ThreadPoolExecutor(max_workers=2)      # POST /api/save: fetched in the background
save_state = {"pending": 0, "lock": threading.Lock()}
MAX_PENDING = 20


# -- search ----------------------------------------------------------------------------------------------------------

class EngineError(Exception):
    pass


def ask(engine, q, page):
    """One engine, one page: the reply's text (network, TOFU, 44 and redirects handled)."""
    e = engines.ENGINES[engine]
    req = engines.request(engine, q, page)
    if polite.slowed(e["host"]):
        raise EngineError("slow-down")
    if store.queries_last_hour(engine) >= PER_HOUR:
        raise EngineError("rate-limited")
    store.count_query(engine)
    if req[0] == "gopher":
        _, host, port, sel, query = req
        body = polite.run(host, lambda: smolnet.gopher(host, port, sel, query, SOCKS))
        return smolnet.decode(body)
    url = req[1]
    for _ in range(MAX_REDIRECTS + 1):
        r = polite.run(e["host"], lambda: smolnet.gemini(url, SOCKS))
        if tofu(r) == "mismatch":
            raise EngineError("certificate changed")
        if r.status == 44:
            polite.slow_down(e["host"], int(r.meta) if r.meta.isdigit() else 30)
            raise EngineError("slow-down")
        if 30 <= r.status < 40:
            url = urljoin(url, r.meta)
            if urlsplit(url).hostname != e["host"]:
                raise EngineError("redirected away")
            continue
        if r.status != 20:
            raise EngineError("%d %s" % (r.status, r.meta))
        return smolnet.decode(r.body, r.charset)
    raise EngineError("too many redirects")


def search_one(engine, q, page):
    key = "2|%s|%s|%d" % (engine, " ".join(q.lower().split()), page)    # 2: marks only on query words
    cached, fresh = store.get_json("search", key, SEARCH_TTL)
    t0 = time.time()
    if cached is not None and fresh:
        return dict(cached, ms=0, cached=True, ok=True), None
    try:
        hits, total, nxt = engines.PARSERS[engine](ask(engine, q, page), q)
        out = {"hits": hits, "total": total, "next": nxt}
        store.put_json("search", key, out)
        health[engine]["last_ok"] = int(time.time())
        return dict(out, ms=int((time.time() - t0) * 1000), cached=False, ok=True), None
    except (EngineError, smolnet.FetchError) as ex:
        err = ex.kind if isinstance(ex, smolnet.FetchError) and ex.kind in ("timeout", "unreachable") else str(ex)
        health[engine]["last_error"] = "%s: %s" % (time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), err)
        if cached is not None:                                        # stale beats nothing
            return dict(cached, ms=int((time.time() - t0) * 1000), cached=True, ok=True), None
        return {"hits": [], "total": None, "next": False, "ms": int((time.time() - t0) * 1000), "cached": False,
                "ok": False}, err


def search(q, sources, page, public_url):
    futures = {k: pool.submit(search_one, k, q, page) for k in sources}
    deadline = time.time() + BUDGET
    lists, meta, errors = [], {}, {}
    for k in sources:
        try:
            res, err = futures[k].result(timeout=max(0.1, deadline - time.time()))
        except concurrent.futures.TimeoutError:          # it keeps running and caches its answer for next time
            res, err = {"hits": [], "total": None, "next": False, "ms": BUDGET * 1000, "cached": False, "ok": False}, "timeout"
        if err:
            errors[k] = err
        meta[k] = {"ok": res["ok"], "ms": res["ms"], "total": res["total"], "next": bool(res["next"]),
                   "cached": res["cached"]}
        lists.append((k, [dict(h, sources=list(h["sources"])) for h in res["hits"]]))
    results = engines.merge(lists)
    for r in results:
        r["proxy_url"] = public_url + render.proxied(r["url"])
    return {"query": q, "page": page, "results": results, "sources": meta, "errors": errors}


# -- pages -----------------------------------------------------------------------------------------------------------

def tofu(r):
    u = urlsplit(r.url)
    return store.tofu_check("%s:%d" % (u.hostname, u.port or 1965), r.cert_sha256, r.not_after)


def robots_allows(scheme, host, port, path):
    """Gemini robots.txt for the virtual agents `webproxy` and `*`; gopher robots.txt for `*`. Cached for a day; a
    missing or unreadable file allows everything."""
    key = "%s://%s:%d" % (scheme, host, port)
    text, fresh = store.get("robots", key, ROBOTS_TTL)
    if text is None or not fresh:
        try:
            if scheme == "gemini":
                r = polite.run(host, lambda: smolnet.gemini("gemini://%s:%d/robots.txt" % (host, port), SOCKS))
                text = smolnet.decode(r.body) if r.status == 20 and r.mime.startswith("text/") else ""
            else:
                body = smolnet.decode(polite.run(host, lambda: smolnet.gopher(host, port, "robots.txt", "", SOCKS)))
                text = body if re.search(r"(?im)^\s*user-agent\s*:", body) else ""
        except smolnet.FetchError:
            text = text or ""
        store.put("robots", key, text)
    agents = ("webproxy", "*") if scheme == "gemini" else ("*",)
    group, rules = False, []
    for line in (text or "").splitlines():
        line = line.split("#", 1)[0].strip()
        k, _, v = line.partition(":")
        k, v = k.strip().lower(), v.strip()
        if k == "user-agent":
            group = v.lower() in agents
        elif k == "disallow" and group and v:
            rules.append(v)
    return not any(path.startswith(rule) for rule in rules)


def answers_prompt(url):
    """Does this gemini URL's query answer an input prompt (its path without the query says 10/11)? Then the page
    is a reply to something typed, and never saved. Checked once a day per path; unknown counts as yes."""
    base = url.split("?", 1)[0]
    cached, fresh = store.get("prompt", base, ROBOTS_TTL)
    if cached is not None and fresh:
        return cached == "1"
    try:
        host = urlsplit(base).hostname
        r = polite.run(host, lambda: smolnet.gemini(base, SOCKS))
        verdict = r.status in (10, 11)
    except smolnet.FetchError:
        return True
    store.put("prompt", base, "1" if verdict else "0")
    return verdict


def is_engine_search(url):
    u = urlsplit(url)
    for name, e in engines.ENGINES.items():
        if u.hostname == e["host"]:
            p = u.path if u.scheme == "gemini" else u.path[2:]
            if re.match(r"^/(v/)?search|^/lucky|^/image-search|^/v2/vs", p):
                return True
    return False


class Page:
    """What /page answers: an HTML document, or raw bytes (images, downloads)."""

    def __init__(self, status=200, html="", body=b"", ctype="", attachment="", save=None):
        self.status, self.html, self.body, self.ctype = status, html, body, ctype
        self.attachment, self.save = attachment, save


def notice_page(url, title, text, status=200, extra=""):
    return Page(status, render.page(title, '<div class="empty"><h2>%s</h2><p>%s</p>%s</div>'
                                    % (render.e(title), render.e(text), extra), url))


def open_page(url, q=None):
    try:
        url = smolnet.canonical(url)
    except ValueError:
        return notice_page("", "Not a Small-Web Address", "smallweb opens gemini:// and gopher:// URLs.", 400)
    if url.startswith("gemini://"):
        return open_gemini(url, q)
    return open_gopher(url, q)


def gemini_cached(url):
    raw, fresh = store.get("page", url, PAGE_TTL)
    if raw is not None and fresh:
        d = json.loads(raw)
        return smolnet.GeminiResponse(url, d["status"], d["meta"], bytes.fromhex(d["body"]), d["sha"], d["na"]), True
    return None, False


def open_gemini(url, q):
    answered = q is not None
    if answered:
        u = urlsplit(url)
        url = "gemini://%s%s?%s" % (u.netloc, u.path or "/", quote(q))
    for _ in range(MAX_REDIRECTS + 1):
        u = urlsplit(url)
        host, port = u.hostname, u.port or 1965
        if polite.slowed(host):
            return notice_page(url, "Slow Down", "The capsule asked us to wait; try again in %d seconds."
                               % polite.slowed(host), 429)
        if not robots_allows("gemini", host, port, u.path or "/"):
            return notice_page(url, "Not for Proxies", "This capsule's robots.txt asks web proxies not to fetch this "
                               "page. Open it natively instead.", 200)
        r, cached = gemini_cached(url)
        if not r:
            try:
                r = polite.run(host, lambda: smolnet.gemini(url, SOCKS))
            except smolnet.FetchError as ex:
                return notice_page(url, "Couldn't Reach the Capsule", ex.detail or ex.kind, 502)
            trust = tofu(r)
            if trust == "mismatch":
                form = ('<form method="post" action="/tofu"><input type="hidden" name="url" value="%s">'
                        '<input type="hidden" name="sha256" value="%s"><input type="hidden" name="not_after" value="%d">'
                        '<button>Trust the New Certificate</button></form>' % (render.e(url), r.cert_sha256, r.not_after))
                return notice_page(url, "Certificate Changed", "%s presented a different certificate than the one "
                                   "first seen, and the old one hasn't expired. New fingerprint: %s."
                                   % (host, r.cert_sha256[:32]), 200, form)
            if r.status == 20:
                store.put("page", url, json.dumps({"status": r.status, "meta": r.meta, "body": r.body.hex(),
                                                   "sha": r.cert_sha256, "na": r.not_after}))
        if 30 <= r.status < 40:
            nxt = urljoin(url, r.meta)
            if not nxt.startswith("gemini://"):
                return Page(200, render.page("Redirect", '<p>This page moved to %s.</p>' % render.link(nxt, nxt), url))
            url = smolnet.canonical(nxt)
            continue
        break
    else:
        return notice_page(url, "Too Many Redirects", "The capsule redirected more than %d times." % MAX_REDIRECTS, 502)
    if r.status in (10, 11):
        base = url.split("?", 1)[0]
        form = ('<form class="input" method="get" action="/page"><input type="hidden" name="url" value="%s">'
                '<label>%s <input type="%s" name="q" required autofocus></label> <button>Send</button></form>'
                % (render.e(base), render.e(r.meta or "Input"), "password" if r.status == 11 else "text"))
        return Page(200, render.page(r.meta or "Input", form, base))
    if r.status == 44:
        polite.slow_down(host, int(r.meta) if r.meta.isdigit() else 30)
        return notice_page(url, "Slow Down", "The capsule asked us to wait %s seconds." % (r.meta or "a few"), 429)
    if 60 <= r.status < 70:
        return notice_page(url, "Needs a Client Certificate", "This page wants a client certificate (%s), which "
                           "smallweb doesn't send. Open it natively." % r.meta, 200)
    if r.status != 20:
        return notice_page(url, "Gemini %d" % r.status, r.meta or "The capsule answered with an error.", 502)
    mime = r.mime or "text/gemini"
    savable = not answered and not is_engine_search(url) and not ("?" in url and answers_prompt(url))
    if mime == "text/gemini":
        title, body, plain = render.gemtext(smolnet.decode(r.body, r.charset), url)
    elif mime.startswith("text/"):
        plain = smolnet.decode(r.body, r.charset)
        title, body = "", render.plaintext(plain)
        savable = savable and mime == "text/plain"
    else:
        return bytes_page(r.body, mime, url)
    title = title or url
    save = {"url": url, "title": title, "body": body, "text": plain, "scheme": "gemini", "mime": mime,
            "cert": r.cert_sha256, "sha": hashlib.sha256(r.body).hexdigest()} if savable else None
    return Page(200, render.page(title, body, url), save=save)


def bytes_page(body, mime, url):
    name = url.rstrip("/").rsplit("/", 1)[-1] or "download"
    image = mime.split("/", 1)[0] == "image" and mime != "image/svg+xml"
    return Page(200, body=body, ctype=mime if image else "application/octet-stream", attachment="" if image else name)


GOPHER_IMAGE = {"g": "image/gif", "I": "image/*", "p": "image/png", ":": "image/*"}


def sniff_image(body):
    for sig, mime in ((b"\x89PNG", "image/png"), (b"GIF8", "image/gif"), (b"\xff\xd8", "image/jpeg"),
                      (b"RIFF", "image/webp")):
        if body.startswith(sig):
            return mime
    return "application/octet-stream"


def open_gopher(url, q):
    host, port, itype, sel, query = smolnet.gopher_parts(url)
    answered = q is not None or bool(query)
    if q is not None:
        itype, query = "7", q
        url = smolnet.canonical(smolnet.gopher_url(host, port, "7", sel, q))
    if itype == "7" and not query:
        form = ('<form class="input" method="get" action="/page"><input type="hidden" name="url" value="%s">'
                '<label>Search <input type="search" name="q" required autofocus></label> <button>Search</button></form>'
                % render.e(url))
        return Page(200, render.page("Search", form, url))
    if not robots_allows("gopher", host, port, sel or "/"):
        return notice_page(url, "Not for Proxies", "This gopher hole's robots.txt asks robots not to fetch this "
                           "selector. Open it natively instead.", 200)
    raw, fresh = store.get("page", url, PAGE_TTL)
    if raw is not None and fresh:
        body = raw
    else:
        try:
            body = polite.run(host, lambda: smolnet.gopher(host, port, sel, query, SOCKS))
        except smolnet.FetchError as ex:
            return notice_page(url, "Couldn't Reach the Gopher Hole", ex.detail or ex.kind, 502)
        store.put("page", url, body)
    savable = not answered and not is_engine_search(url)
    if itype in ("1", "7"):
        title, html, plain = render.gophermap(smolnet.decode(body), host, port)
        info = sum(len(line) for line in plain.splitlines())
        savable = savable and itype == "1" and info >= 300              # menus are mostly navigation
        mime = "text/gophermap"
    elif itype in ("0", "h"):                                        # h: remote HTML is shown as text, never rendered
        plain = smolnet.decode(body)
        if plain.endswith("\r\n.\r\n") or plain.endswith("\n.\n"):
            plain = plain.rsplit(".", 1)[0]
        title, html, mime = "", render.plaintext(plain), "text/plain"
        savable = savable and itype == "0"
    else:
        mime = GOPHER_IMAGE.get(itype)
        return bytes_page(body, sniff_image(body) if mime else "application/octet-stream", url)
    title = (title or sel.rsplit("/", 1)[-1] or host).strip()
    save = {"url": url, "title": title, "body": html, "text": plain, "scheme": "gopher", "mime": mime,
            "cert": None, "sha": hashlib.sha256(body).hexdigest()} if savable else None
    return Page(200, render.page(title, html, url), save=save)


# -- Hister ----------------------------------------------------------------------------------------------------------

def save_to_hister(s):
    """POST the page to Hister (in the background): the canonical URL, no label (a visited page), html + text."""
    if not HISTER or store.saved_hash(s["url"]) == s["sha"]:
        return
    meta = {"source": "smallweb", "smallweb_scheme": s["scheme"], "smallweb_mime": s["mime"],
            "smallweb_fetched": int(time.time())}
    if s["cert"]:
        meta["smallweb_cert_sha256"] = s["cert"]
    doc = {"url": s["url"], "title": s["title"], "text": s["text"],
           "html": "<!DOCTYPE html><html><head><title>%s</title></head><body><article>%s</article></body></html>"
                   % (render.e(s["title"]), s["body"]),
           "metadata": meta}
    req = urllib.request.Request(HISTER + "/api/add", data=json.dumps(doc).encode(), method="POST",
                                 headers={"Content-Type": "application/json", "Origin": "hister://"})
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            status = r.status
    except urllib.error.HTTPError as ex:
        status = ex.code                               # 406 skipped by a rule, 422 sensitive
    except (urllib.error.URLError, OSError) as ex:
        hister_state["last_error"] = "%s: %s" % (time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), ex)
        hister_state["fails"] += 1
        return
    store.record_save(s["url"], s["sha"], status)
    # 406 (a skip rule) and 422 (sensitive content) are Hister's answers, not failures
    hister_state["fails"] = 0 if status in (200, 201, 406, 422) else hister_state["fails"] + 1
    if status not in (200, 201):
        hister_state["last_error"] = "%s: %s -> %d" % (time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), s["url"], status)


def queue_save(url):
    """POST /api/save: fetch a page Shiori opened directly (in a Gemini app, so it never passed through /page) and
    save it exactly as a /page read would. -> (status, body)."""
    try:
        url = smolnet.canonical(url)
    except ValueError:
        return 400, {"error": "url must be a gemini:// or gopher:// URL"}
    with save_state["lock"]:
        if save_state["pending"] >= MAX_PENDING:
            return 429, {"error": "too many saves waiting; try again later"}
        save_state["pending"] += 1

    def job():
        try:
            p = open_page(url)
            if p.save:
                save_to_hister(p.save)
        except Exception as ex:                       # a background job must never die silently
            hister_state["last_error"] = "%s: save %s: %s" % (time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), url, ex)
        finally:
            with save_state["lock"]:
                save_state["pending"] -= 1
    save_pool.submit(job)
    return 202, {"queued": True, "url": url}


# -- HTTP ------------------------------------------------------------------------------------------------------------

CSP = ("default-src 'none'; style-src 'self'; img-src 'self'; form-action 'self'; base-uri 'none'; "
       "frame-ancestors 'none'")


def problems():
    """What is actually broken in smallweb itself (an engine or a capsule being down isn't): the data dir, the SOCKS
    proxy, Hister saves. A monitoring probe should fail on a non-null "error"."""
    out = []
    try:
        store.q("INSERT OR REPLACE INTO cache VALUES ('health', 'probe', ?, '')", (int(time.time()),))
    except Exception as ex:
        out.append("data dir not writable (%s): %s" % (DATA, ex))
    if SOCKS and smolnet.PROXY["fails"] >= 2:
        out.append(smolnet.PROXY["error"])
    if HISTER and hister_state["fails"] >= 3:
        out.append("Hister saves failing (%d in a row): %s" % (hister_state["fails"], hister_state["last_error"]))
    return out


def status():
    errors = problems()
    return {"ok": not errors, "ready": True, "error": "; ".join(errors) or None,
            "version": VERSION, "auth": AUTH, "socks": bool(SOCKS),
            "sources": {k: dict(v) for k, v in health.items()},
            "hister": {"enabled": bool(HISTER), "saved": store.save_count(), "queued": save_state["pending"],
                       "last_error": hister_state["last_error"]},
            "known_hosts": store.tofu_count()}


class Handler(BaseHTTPRequestHandler):
    server_version = "smallweb/" + VERSION
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):
        if not self.path.startswith("/api/status"):
            sys.stderr.write("smallweb %s %s\n" % (self.headers.get("Tailscale-User-Login", "-"), fmt % args))

    def allowed(self):
        return AUTH == "open" or "*" in USERS or self.headers.get("Tailscale-User-Login", "") in USERS

    def public_url(self):
        if PUBLIC_URL:
            return PUBLIC_URL
        host = self.headers.get("Host", "localhost")
        local = host.split(":")[0] in ("localhost", "127.0.0.1") or host.startswith("[")
        return ("http://" if local else "https://") + host

    def send(self, code, body, ctype="text/html; charset=utf-8", headers=()):
        data = body.encode("utf-8") if isinstance(body, str) else body
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Content-Security-Policy", CSP if ctype.startswith("text/html") else "default-src 'none'; sandbox")
        for k, v in headers:
            self.send_header(k, v)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(data)

    def send_json(self, code, obj):
        self.send(code, json.dumps(obj, ensure_ascii=False), "application/json; charset=utf-8",
                  [("Cache-Control", "no-store")])

    def do_HEAD(self):
        self.do_GET()

    def do_GET(self):
        u = urlsplit(self.path)
        qs = {k: v[-1] for k, v in parse_qs(u.query, keep_blank_values=True).items()}
        if u.path == "/api/status":
            return self.send_json(200, status())
        if not self.allowed():
            return self.send(403, "forbidden\n", "text/plain")
        if u.path == "/static/smallweb.css":
            with open(os.path.join(HERE, "smallweb.css"), "rb") as f:
                return self.send(200, f.read(), "text/css", [("Cache-Control", "max-age=3600")])
        if u.path == "/api/search":
            try:
                q, sources, page = self.search_args(qs)
            except ValueError as ex:
                return self.send_json(400, {"error": str(ex)})
            return self.send_json(200, search(q, sources, page, self.public_url()))
        if u.path == "/":
            # the engine checkboxes send source=a&source=b; the API takes source=a,b; both work here
            picked = ",".join(v for v in parse_qs(u.query).get("source", []) if v)
            if picked:
                qs["source"] = picked
            chosen = [x for x in qs.get("source", "").split(",") if x] or None
            if not qs.get("q", "").strip():
                return self.send(200, render.page("smallweb", '<div class="empty"><h2>Search the Small Web</h2><p>'
                                                  'Gemini (TLGS, Kennedy) and Gopher (Veronica-2), asked only when '
                                                  'you search.</p></div>', home=True, sources=chosen))
            try:
                q, sources, page = self.search_args(qs)
            except ValueError as ex:
                return self.send(400, render.page("smallweb", '<p class="notice">%s</p>' % render.e(str(ex)), home=True))
            data = search(q, sources, page, "")
            return self.send(200, render.page("%s - smallweb" % q, render.results(q, data, sources), home=True,
                                              q=q, sources=sources))
        if u.path == "/page":
            p = open_page(qs.get("url", ""), qs.get("q"))
            if p.save:
                threading.Thread(target=save_to_hister, args=(p.save,), daemon=True).start()
            if p.html:
                return self.send(p.status, p.html)
            headers = [("Content-Disposition", 'attachment; filename="%s"' % re.sub(r'[^\w.-]', "_", p.attachment))] \
                if p.attachment else []
            return self.send(p.status, p.body, p.ctype, headers)
        self.send(404, render.page("Not Found", '<div class="empty"><h2>Not Found</h2><p>%s</p></div>'
                                   % render.e(u.path), native=False))

    def search_args(self, qs):
        q = " ".join(qs.get("q", "").split())
        if not q:
            raise ValueError("q is required")
        if len(q) > 300:
            raise ValueError("q is longer than 300 characters")
        sources = [s.strip().lower() for s in qs.get("source", "").split(",") if s.strip()] or list(engines.ORDER)
        bad = [s for s in sources if s not in engines.ENGINES]
        if bad:
            raise ValueError("unknown source: %s" % ", ".join(bad))
        try:
            page = int(qs.get("page") or 1)
        except ValueError:
            raise ValueError("page must be a number")
        if not 1 <= page <= 50:
            raise ValueError("page must be 1 to 50")
        return q, list(dict.fromkeys(sources)), page

    def do_POST(self):
        if not self.allowed():
            return self.send(403, "forbidden\n", "text/plain")
        u = urlsplit(self.path)
        origin = (self.headers.get("Origin") or "").rstrip("/")
        own = bool(self.headers.get("Host")) and urlsplit(origin or self.headers.get("Referer") or "").netloc == self.headers["Host"]
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(min(length, 65536)).decode("utf-8", "replace")
        if u.path == "/api/save":
            if not (own or origin in ORIGINS):
                return self.send_json(403, {"error": "cross-site request refused"})
            try:
                data = json.loads(raw or "{}") if "json" in (self.headers.get("Content-Type") or "") else \
                    {k: v[-1] for k, v in parse_qs(raw).items()}
            except ValueError:
                return self.send_json(400, {"error": "invalid JSON"})
            code, body = queue_save(str(data.get("url") or ""))
            return self.send_json(code, body)
        if not own:
            return self.send(403, "cross-site form post refused\n", "text/plain")
        form = {k: v[-1] for k, v in parse_qs(raw).items()}
        if u.path == "/tofu":
            try:
                url = smolnet.canonical(form.get("url", ""))
                sha, na = form["sha256"], int(form.get("not_after") or 0)
            except (ValueError, KeyError):
                return self.send(400, "bad request\n", "text/plain")
            if not re.fullmatch(r"[0-9a-f]{64}", sha):
                return self.send(400, "bad fingerprint\n", "text/plain")
            h = urlsplit(url)
            store.tofu_pin("%s:%d" % (h.hostname, h.port or 1965), sha, na)
            return self.send(303, "", "text/plain", [("Location", render.proxied(url))])
        self.send(404, "not found\n", "text/plain")


def main():
    print("smallweb %s: auth %s%s, listening on %s:%d; egress %s; Hister %s" % (
        VERSION, AUTH, "" if AUTH == "open" else " (%s)" % (",".join(sorted(USERS)) or "NOBODY: set SMALLWEB_USERS"),
        BIND, PORT, ("SOCKS " + SOCKS) if SOCKS else "DIRECT (set SMALLWEB_SOCKS)", HISTER or "off"), flush=True)
    if AUTH == "open":
        print("smallweb: WARNING: SMALLWEB_AUTH=open: no identity check. Anyone who can reach %s:%d can search and "
              "read through smallweb and add pages to Hister. Use it only on localhost or a trusted LAN." % (BIND, PORT),
              flush=True)
    store.prune()
    server = ThreadingHTTPServer((BIND, PORT), Handler)
    server.daemon_threads = True
    server.serve_forever()


if __name__ == "__main__":
    main()
