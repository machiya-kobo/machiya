"""What the landing page asks each app in the stack, and how it reads the answers (docs/services/landing.md).

Every app is optional. An app with no address is "absent" (shown as "Not in this stack", never as an error); one that
doesn't answer within the timeout is "down". Only GETs are sent, never with a redirect followed, and an answer larger
than LIMIT bytes is cut off and counted as unreadable. Nothing here renders HTML: it turns answers into plain values.
"""
import json
import os
import re
from decimal import ROUND_HALF_UP, Decimal
import socket
import threading
import time
import urllib.error
import urllib.request
from urllib.parse import quote, urlsplit

LIMIT = 4 << 20
HISTER_ORIGIN = "hister://"      # contracts/hister.md: every Hister call says who it is
USER_AGENT = "machiya-landing"

# (key, name, group, what it is) in the order the page shows them: the rooms front to back (as the Rooms menu), the
# engines, then the stack's own services.
APPS = [
    ("shiori", "Shiori", "rooms", "search"),
    ("konbini", "Konbini", "rooms", "board"),
    ("niwa", "Niwa", "rooms", "garden"),
    ("kura", "Kura", "rooms", "notes"),
    ("hister", "Hister", "engines", "pages"),
    ("searxng", "SearXNG", "engines", "the web"),
    ("machiya-mcp", "machiya-mcp", "services", "MCP for agents"),
    ("smallweb", "smallweb", "services", "Gemini and Gopher"),
    ("vault-mirror", "vault-mirror", "services", "the vault's shared copy"),
    ("feed-import", "feed-import", "services", "feeds into Hister"),
]
NAMES = {k: n for k, n, _, _ in APPS}
ROOMS_WITH_TOKEN = ("kura", "niwa", "konbini")     # LANDING_TOKEN_FILE goes to these only (never Hister or SearXNG)

# Freshness, in seconds: (behind, broken). "Behind" is drawn calm (a yellow dot); only "broken" is drawn as an alert.
FRESH = {
    "pull": (15 * 60, 6 * 3600),        # the vault's last pull (Kura, the mirror): they poll every minute
    "livesync": (15 * 60, 2 * 3600),    # Konbini's LiveSync cycle
    "push": (60 * 60, 24 * 3600),       # Kura's last push of the notes into Hister
    "board": (15 * 60, None),           # Konbini's commit differs from Kura's for this long
    "pages": (3 * 86400, None),         # Hister's newest page (0.2.0: judged; red only when Hister is down)
    "feeds": (60 * 60, None),           # feed-import's last good run (it runs every 10 min; ok=false is broken)
}
READERS_NAMES = {"newsblur": "NewsBlur", "miniflux": "Miniflux", "freshrss": "FreshRSS", "feedbin": "Feedbin"}
STATES = ("up", "behind", "starting", "error", "down", "absent")
BAD = ("error", "down")


TOKEN_RE = re.compile(r"[\x21-\x7e]{1,4096}")


class SecretFile:
    """LANDING_HISTER_TOKEN_FILE: a token kept in a file (its first line), re-read when the file changes (inode, mtime,
    size), so a rotated token needs no restart; a file that vanishes or holds no token keeps the last good value.
    Never in repr() or a log line."""

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


class FetchError(Exception):
    """Why an app didn't answer, in a few words fit for the page ("timed out", "connection refused", "HTTP 502")."""


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *a, **kw):
        return None                     # a 3xx is an answer, not a hop (and a token never follows one)


_OPENER = urllib.request.build_opener(_NoRedirect)


def fetch(url, headers=None, timeout=3.0):
    """GET url -> (status, content type, body). FetchError for no answer, a non-2xx status or a body over LIMIT."""
    req = urllib.request.Request(url, headers=dict({"User-Agent": USER_AGENT, "Accept": "application/json"}, **(headers or {})))
    try:
        with _OPENER.open(req, timeout=timeout) as r:
            body = r.read(LIMIT + 1)
            if len(body) > LIMIT:
                raise FetchError("answer too large")
            return r.status, r.headers.get("Content-Type", ""), body
    except urllib.error.HTTPError as e:
        raise FetchError("HTTP %d" % e.code)
    except urllib.error.URLError as e:
        reason = e.reason
        if isinstance(reason, (socket.timeout, TimeoutError)):
            raise FetchError("timed out")
        if isinstance(reason, ConnectionRefusedError):
            raise FetchError("connection refused")
        if isinstance(reason, socket.gaierror):
            raise FetchError("name not found")
        raise FetchError("unreachable")
    except (socket.timeout, TimeoutError):
        raise FetchError("timed out")
    except (ConnectionError, OSError):
        raise FetchError("connection failed")


def fetch_text(url, headers=None, timeout=3.0):
    """GET url -> (status, content type, body, ETag) for a text answer (a changelog); FetchError as fetch()."""
    req = urllib.request.Request(url, headers=dict({"User-Agent": USER_AGENT}, **(headers or {})))
    try:
        with _OPENER.open(req, timeout=timeout) as r:
            body = r.read(LIMIT + 1)
            if len(body) > LIMIT:
                raise FetchError("answer too large")
            return r.status, r.headers.get("Content-Type", "") or "", body, r.headers.get("ETag", "") or ""
    except urllib.error.HTTPError as e:
        raise FetchError("HTTP %d" % e.code)
    except (urllib.error.URLError, OSError):
        raise FetchError("unreachable")


def post_json(url, payload, headers=None, timeout=3.0):
    """POST a JSON body -> the JSON answer (a dict). Only for Hister's MCP `initialize`, which changes nothing."""
    body = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=body, method="POST", headers=dict(
        {"User-Agent": USER_AGENT, "Content-Type": "application/json", "Accept": "application/json, text/event-stream"},
        **(headers or {})))
    try:
        with _OPENER.open(req, timeout=timeout) as r:
            raw = r.read(LIMIT + 1)
    except urllib.error.HTTPError as e:
        raise FetchError("HTTP %d" % e.code)
    except (urllib.error.URLError, OSError):
        raise FetchError("unreachable")
    if len(raw) > LIMIT:
        raise FetchError("answer too large")
    text_ = raw.decode("utf-8", "replace").strip()
    if text_.startswith("event:") or text_.startswith("data:"):      # an SSE reply: the first data line
        text_ = next((l[5:].strip() for l in text_.splitlines() if l.startswith("data:")), "")
    try:
        data = json.loads(text_)
    except ValueError:
        raise FetchError("not JSON")
    if not isinstance(data, dict):
        raise FetchError("unexpected answer")
    return data


def fetch_json(url, headers=None, timeout=3.0):
    _, _, body = fetch(url, headers, timeout)
    try:
        data = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        raise FetchError("not JSON")
    if not isinstance(data, dict):
        raise FetchError("unexpected answer")
    return data


# -- small readers -----------------------------------------------------------------------------------------------

def num(v):
    return v if isinstance(v, (int, float)) and not isinstance(v, bool) else None


def text(v, limit=160):
    """A value from an app, as one line of plain text (the page escapes it)."""
    if v is None or v is False:
        return ""
    s = " ".join(str(v).split())
    return s[:limit - 1] + "…" if len(s) > limit else s


def short(head):
    head = text(head)
    return head[:7] if re.fullmatch(r"[0-9a-f]{7,64}", head) else head


def vk(v):
    """'v0.17.2' -> '0.17.2' (Kura and Niwa report the vendored vaultkit's tag)."""
    v = text(v, 40)
    return v[1:] if re.match(r"v\d", v) else v


def count(n):
    """A count as the owner reads it (0.3.0): below 1,000 as is; from 1,000 one decimal and k, M or B, a trailing .0
    dropped, rounded half up (1,049 -> 1k, 1,050 -> 1.1k, 12,340 -> 12.3k, 999,950 -> 1M)."""
    n = int(n)
    if abs(n) < 1000:
        return str(n)
    for div, unit, nxt in ((10 ** 3, "k", 10 ** 6), (10 ** 6, "M", 10 ** 9), (10 ** 9, "B", None)):
        if nxt is not None and abs(n) >= nxt:
            continue
        v = (Decimal(n) / div).quantize(Decimal("0.1"), rounding=ROUND_HALF_UP)
        if nxt is not None and abs(v) >= 1000:          # 999,950 rounds up to the next unit
            continue
        text_ = format(v, "f")
        return (text_[:-2] if text_.endswith(".0") else text_) + unit
    return str(n)


def plural(n, word, many=None):
    return "%s %s" % (count(n), word if n == 1 else (many or word + "s"))


def age_state(at, now, kind):
    """up / behind / error by the age of a timestamp (FRESH); None when there's no timestamp."""
    if not num(at):
        return None
    behind, broken = FRESH[kind]
    age = now - at
    if broken is not None and age > broken:
        return "error"
    return "behind" if age > behind else "up"


def worst(*states):
    """The more serious of several states (None ignored)."""
    order = {"down": 5, "error": 4, "starting": 3, "behind": 2, "up": 1}
    states = [s for s in states if s]
    return max(states, key=lambda s: order.get(s, 0)) if states else "up"


def result(state="up", version="", vaultkit="", facts=(), error="", data=None):
    return {"state": state, "version": text(version, 40), "vaultkit": vaultkit, "facts": [f for f in facts if f],
            "error": text(error), "data": data or {}}


# -- one reader per app ------------------------------------------------------------------------------------------

def kura(base, now, timeout, headers, owner=None):
    """GET /api/status (open, sent without the owner's token; contracts/kura-api.md): the vault's head and last pull,
    the note count, the Hister push, `vault_count`. Then, with the owner's credential (`owner`), GET /api/vaults for the
    notes across every vault; refused (401/403), the open `vault_count` and the default vault's count stand."""
    d = fetch_json(base + "/api/status", headers, timeout)
    synced, notes = num(d.get("synced_at")), num(d.get("notes"))
    push = d.get("push") if isinstance(d.get("push"), dict) else {}
    state = "starting" if d.get("ready") is False else (age_state(synced, now, "pull") or "up")
    if d.get("error"):
        state = "error"
    facts = [plural(int(notes), "note") if notes is not None else ""]
    vaults, total = num(d.get("vault_count")), None          # 0.2.3: Kura 0.6.13's open status counts its vaults
    if vaults is not None and vaults > 1:
        facts = [facts[0], plural(int(vaults), "vault")]
    try:                    # 0.2.1: every vault, owner-only; COUNTS ONLY: a private vault's name never leaves this function
        listed = [v for v in fetch_json(owner_url(base, "/api/vaults"), owner if owner is not None else headers, timeout).get("vaults") or []
                  if isinstance(v, dict)]
        if listed:
            vaults = len(listed)
            total = int(sum(num(v.get("notes")) or 0 for v in listed))
            facts = ["%s · %s" % (plural(vaults, "vault"), plural(total, "note"))]
    except FetchError:
        pass
    return result(state, d.get("version"), vk(d.get("vaultkit")), facts, d.get("error"),
                  {"head": text(d.get("head"), 64), "synced_at": synced, "notes": notes, "vaults": int(vaults) if vaults is not None else None,
                   "total_notes": total, "push": {
                      "at": num(push.get("at")), "docs": num(push.get("docs")), "failed": num(push.get("failed")),
                      "error": text(push.get("error")), "complete": push.get("complete")} if push else None})


def konbini(base, now, timeout, headers, owner=None):
    """GET /api/health (contracts/konbini-api.md): cards, the vault head, sync, LiveSync and per-board counts. With
    AUTH=hister Konbini answers anyone a limited view (ok, version, head, cards) and the owner the full one, so it is
    asked with the owner's credential (`owner`, the lead's decision for 0.2.3); refused (401/403), it is asked again
    without it and the limited view stands."""
    try:
        d = fetch_json(owner_url(base, "/api/health"), owner if owner else headers, timeout)
    except FetchError as e:
        if not owner or str(e) not in ("HTTP 401", "HTTP 403"):
            raise
        d = fetch_json(base + "/api/health", headers, timeout)
    sync = d.get("sync") if isinstance(d.get("sync"), dict) else {}
    live = ((d.get("livesync") or {}).get("status") or {}) if isinstance(d.get("livesync"), dict) else {}
    cards = num(d.get("cards"))
    err = text(sync.get("error")) or ("" if d.get("ok", True) else "not ok")
    state = "error" if err else "up"
    wip = None
    boards = d.get("boards") if isinstance(d.get("boards"), dict) else None     # per-board counts, when Konbini has them
    if boards is not None:
        wip = num(boards.get("wip"))
    else:
        try:
            wip = sum(1 for c in fetch_json(owner_url(base, "/api/cards"), owner if owner is not None else headers, timeout).get("cards") or []
                      if isinstance(c, dict) and c.get("board") == "wip")
        except FetchError:
            pass
    facts = ["%s in WIP" % count(wip) if wip is not None else "", plural(int(cards), "card") if cards is not None else ""]
    return result(state, d.get("version"), vk(d.get("vaultkit")), facts, err,
                  {"head": text(d.get("head"), 64), "pending": num(sync.get("pending")), "ahead": num(sync.get("ahead")),
                   "wip": wip, "sync_error": text(sync.get("error")), "livesync": {
                       "daemon": text(live.get("daemon"), 40), "last_cycle": num(live.get("last_cycle_ts")),
                       "errors": len(live.get("errors") or []) + len(live.get("conflicts") or [])} if live else None})


def niwa(base, now, timeout, headers):
    """GET /api/status: published notes, its clone's sync (pending, ahead) and errors."""
    d = fetch_json(base + "/api/status", headers, timeout)
    sync = d.get("sync") if isinstance(d.get("sync"), dict) else {}
    pub, notes = num(d.get("published")), num(d.get("notes"))
    err = text(d.get("error")) or text(sync.get("error"))
    state = "error" if err else ("starting" if d.get("ready") is False else "up")
    return result(state, d.get("version"), vk(d.get("vaultkit")),
                  ["%s published" % count(pub) if pub is not None else ""], err,      # 0.2.0: no note count
                  {"head": text(d.get("head"), 64), "pending": num(sync.get("pending")), "ahead": num(sync.get("ahead")),
                   "sync_error": text(sync.get("error")), "published": pub})


BUILD_RE = re.compile(rb"""(?:app|theme)\.(?:js|css)\?v=([0-9a-f]{6,40})""")


def shiori(base, now, timeout, headers):
    """The hosted search page (GET /: up when it answers with a page), then best effort:
    - /_shiori/status.json ({"version", "build", "built"}, Shiori's build stamp); without it, the assets' ?v= hash;
    - /shiori/ai/status ({"enabled", "remaining", ...}: requests left of the day's); a non-JSON answer is "AI down";
    - /shiori/healthz (shiori-feed: 200 "ok").
    None of those makes Shiori down; a 404 leaves its part out."""
    _, ctype, body = fetch(base + "/", dict(headers, Accept="text/html"), timeout)
    if "html" not in ctype.lower():
        raise FetchError("not a page")
    version, build, built = "", "", None
    try:
        st = fetch_json(base + "/_shiori/status.json", headers, timeout)
        version, build = text(st.get("version"), 40), text(st.get("build"), 40)
        built = text(st.get("built"), 40)
    except FetchError:
        m = BUILD_RE.search(body)
        build = m.group(1).decode()[:7] if m else ""
    facts = []
    try:
        ai = fetch_json(base + "/shiori/ai/status", headers, timeout)
        left = num(ai.get("remaining"))
        if ai.get("enabled"):
            facts.append("AI on" + (" · %s left today" % count(left) if left is not None else ""))
        else:
            facts.append("AI off")
    except FetchError as e:
        if str(e) != "HTTP 404":
            facts.append("AI down")
    try:
        _, _, ok = fetch(base + "/shiori/healthz", dict(headers, Accept="text/plain"), timeout)
        facts.append("feed ok" if ok.strip()[:2].lower() == b"ok" else "feed down")
    except FetchError as e:
        if str(e) != "HTTP 404":
            facts.append("feed down")
    out = result("up", version or (("build " + build) if build else ""), "", facts, "", {"build": build, "built": built})
    return out                  # 0.3.0: the version only on the card; the build stays in data (Recent Deploys)


HISTER_VERSION = {}             # base -> (when asked, version): Hister's MCP is asked at most every 15 minutes
HISTER_VERSION_TTL = 15 * 60


def hister_version(base, now, timeout, headers):
    """Hister's version from its MCP `initialize` (result.serverInfo.version, "v0.20.0"): open without users, the
    owner's token with them. A POST, but it changes nothing; cached for 15 minutes; "" when it can't be read."""
    cached = HISTER_VERSION.get(base)
    if cached and now - cached[0] < HISTER_VERSION_TTL:
        return cached[1]
    version = ""
    try:
        d = post_json(base + "/mcp", {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {
            "protocolVersion": "2025-06-18", "capabilities": {},
            "clientInfo": {"name": "machiya-landing", "version": "0"}}}, headers, timeout)
        info = ((d.get("result") or {}).get("serverInfo") or {}) if isinstance(d.get("result"), dict) else {}
        version = vk(info.get("version"))
    except FetchError:
        pass
    HISTER_VERSION[base] = (now, version)
    return version


def page_entry(doc):
    """One Hister document for Today's Saved & Read: plain values only."""
    meta = doc.get("metadata") if isinstance(doc.get("metadata"), dict) else {}
    source = text(meta.get("via") or meta.get("source"), 40).lower()
    reader = READERS_NAMES.get(source, "")
    stream = text(meta.get(source + "_stream"), 20).lower() if reader else ""
    starred = reader and (stream == "starred" or bool(meta.get(source + "_starred")))
    url = text(doc.get("url"), 2000)
    return {"title": text(doc.get("title"), 200) or text(doc.get("domain"), 100) or url, "url": url,
            "domain": text(doc.get("domain"), 100), "at": num(doc.get("updated")) or num(doc.get("added")),   # Hister's order
            "reader": reader, "starred": bool(starred), "label": text(doc.get("label"), 40)}


def hister(base, now, timeout, headers, token=None):
    """Up or down from GET /health (open, even with Hister's user handling on: contracts/hister.md); then, best
    effort, the page count (GET /api/stats) and the newest page by date (a search; vault notes left out: Kura's push
    covers them), with the owner's token (LANDING_HISTER_TOKEN_FILE) as X-Access-Token when set. With users on and no
    token (or a refused one) those answer 403: Hister is still up, only the count is missing. 0.2.0: its version from
    its MCP `initialize` (hister_version), and the newest pages for Today."""
    h = dict(headers, Origin=HISTER_ORIGIN)            # every Hister call says who it is, /health too
    fetch(base + "/health", dict(h, Accept="text/plain"), timeout)      # any 2xx is up: Hister's /health is 200, empty body
    value = token.get() if hasattr(token, "get") else (token or "")
    if value:
        h["X-Access-Token"] = value
    version = hister_version(base, now, timeout, {"X-Access-Token": value} if value else {})
    docs = newest = None
    pages = []
    note = ""
    try:
        docs = num(fetch_json(base + "/api/stats", h, timeout).get("doc_count"))
        s = fetch_json(base + "/search?format=json&sort=date&q=" + quote("* -label:vault -metadata.source:vault"), h, timeout)
        found = [d for d in (s.get("documents") or []) if isinstance(d, dict)]
        if found:
            newest = num(found[0].get("updated")) or num(found[0].get("added"))
        pages = [page_entry(d) for d in found[:8]]
    except FetchError as e:
        if str(e) in ("HTTP 401", "HTTP 403"):          # users on: the count needs the owner's token
            note = "page count: the token was refused" if value else "page count needs LANDING_HISTER_TOKEN_FILE"
    return result("up", version, "", [plural(int(docs), "page") if docs is not None else note], "",
                  {"docs": docs, "newest": newest, "pages": pages})


def searxng(base, now, timeout, headers):
    """GET /healthz ("OK"), then /config for its version."""
    _, _, body = fetch(base + "/healthz", dict(headers, Accept="text/plain"), timeout)
    if body.strip()[:2].upper() != b"OK":
        raise FetchError("not healthy")
    version = ""
    try:
        version = text(fetch_json(base + "/config", headers, timeout).get("version"), 40).split("+")[0]
    except FetchError:
        pass
    return result("up", version, "", [])


def machiya_mcp(base, now, timeout, headers):
    """GET /api/status (open): version, tools and the rooms it reaches."""
    d = fetch_json(base + "/api/status", headers, timeout)
    tools, rooms = num(d.get("tools")), d.get("rooms") if isinstance(d.get("rooms"), list) else []
    return result("up" if d.get("ok", True) else "error", d.get("version"), vk(d.get("vaultkit")),
                  [plural(int(tools), "tool") if tools is not None else "", plural(len(rooms), "room") if rooms else ""])


def smallweb(base, now, timeout, headers):
    """GET /api/status (open; contracts/smallweb-api.md): ready, error, pages saved to Hister."""
    d = fetch_json(base + "/api/status", headers, timeout)
    h = d.get("hister") if isinstance(d.get("hister"), dict) else {}
    saved = num(h.get("saved"))
    err = text(d.get("error"))
    state = "error" if err else ("starting" if d.get("ready") is False else "up")
    return result(state, d.get("version"), vk(d.get("vaultkit")),
                  ["%s saved" % count(saved) if saved is not None and h.get("enabled") else ""], err)


def vault_mirror(path, now):
    """The mirror's status.json ({head, synced_at, error}), read from its volume (mounted read-only)."""
    try:
        with open(path, encoding="utf-8") as f:
            d = json.load(f)
    except FileNotFoundError:
        raise FetchError("no status file yet")
    except (OSError, ValueError):
        raise FetchError("status file unreadable")
    if not isinstance(d, dict):
        raise FetchError("status file unreadable")
    synced = num(d.get("synced_at"))
    err = text(d.get("error"))
    state = "error" if err else (age_state(synced, now, "pull") or "starting")
    return result(state, d.get("version"), "", [("at " + short(d.get("head"))) if d.get("head") else ""], err,
                  {"head": text(d.get("head"), 64), "synced_at": synced})


def feed_import(path, now):
    """feed-import's status.json ({version, ok, running, started, last_success, failures_in_a_row, error, counts}),
    read from its volume (mounted read-only). ok=false is broken; no good run for an hour is behind."""
    try:
        with open(path, encoding="utf-8") as f:
            d = json.load(f)
    except FileNotFoundError:
        raise FetchError("no status file yet")
    except (OSError, ValueError):
        raise FetchError("status file unreadable")
    if not isinstance(d, dict):
        raise FetchError("status file unreadable")
    counts = d.get("counts") if isinstance(d.get("counts"), dict) else {}
    added = sum(num((c or {}).get("added")) or 0 for c in counts.values() if isinstance(c, dict))
    last = num(d.get("last_success"))
    fails = num(d.get("failures_in_a_row")) or 0
    err = text(d.get("error"))
    if d.get("ok") is False:
        state = "error"
    elif last:
        state = age_state(last, now, "feeds")
    else:
        state = "starting"
    readers = [READERS_NAMES.get(k, k) for k in sorted(counts)]
    return result(state, d.get("version"), "", [", ".join(readers)], err if d.get("ok") is False else "",
                  {"last_success": last, "failures": fails, "error": err, "added_total": added, "running": bool(d.get("running"))})


compact = count             # 0.2.1's name for the same thing


def search_counts(path):
    """LANDING_SEARCH_COUNTS: web searches (every SearXNG /search, what would hit a paid engine) as counted by the
    deployment: {"updated", "today", "yesterday", "month", "year", "by_day"}, UTC days, counts only. "" when the file
    is missing or unreadable (quietly)."""
    try:
        with open(path, encoding="utf-8") as f:
            d = json.load(f)
    except (OSError, ValueError):
        return ""
    if not isinstance(d, dict):
        return ""
    bits = [("%s %s" % (count(d[k]), w)) for k, w in (("today", "today"), ("month", "mo"), ("year", "yr"))
            if num(d.get(k)) is not None]
    return ("Searches " + " · ".join(bits)) if bits else ""        # 0.3.0: "Searches 112 today · 3.4k mo · 41.2k yr"


READERS = {"kura": kura, "konbini": konbini, "niwa": niwa, "shiori": shiori, "hister": hister, "searxng": searxng,
           "machiya-mcp": machiya_mcp, "smallweb": smallweb}


def auth_headers(key, target, token):
    """LANDING_TOKEN_FILE's token, as a Bearer, for Kura, Niwa and Konbini over https only (identity.md); nothing for
    the engines or the stack services, and nothing over plain http."""
    if token and key in ROOMS_WITH_TOKEN and urlsplit(target).scheme == "https":
        return {"Authorization": "Bearer " + token}
    return {}


def is_loopback_host(host):
    host = (host or "").strip("[]").lower()
    return host in ("localhost", "127.0.0.1", "::1") or host.startswith("127.")


def owner_headers(key, target, token="", secret=None):
    """The owner's credential for a room's owner-only reads (0.2.3: the rooms run AUTH=hister): LANDING_TOKEN_FILE's
    Bearer as auth_headers says, plus the owner's Hister token (LANDING_HISTER_TOKEN_FILE, re-read when it changes) as
    X-Access-Token, ONLY for Kura, Konbini and Niwa at their configured address (`target`, from MACHIYA_ROOMS,
    LANDING_APPS or LANDING_PROBES), over https (or loopback, for tests and a local stack). Callers send it only to
    URLs under `target` (owner_url), and fetch never follows a redirect, so it can't leave that origin. The open
    reads (/api/status, /api/health, /api/changelog) never get it."""
    h = auth_headers(key, target, token)
    value = secret.get() if hasattr(secret, "get") else (secret or "")
    parts = urlsplit(target or "")
    if value and key in ROOMS_WITH_TOKEN and parts.netloc and (parts.scheme == "https" or (
            parts.scheme == "http" and is_loopback_host(parts.hostname))):
        h["X-Access-Token"] = value
    return h


def owner_url(target, path):
    """target + path, refusing anything that would leave target's origin (a path is always ours, but be sure)."""
    url = target.rstrip("/") + path
    a, b = urlsplit(url), urlsplit(target)
    if (a.scheme, a.netloc) != (b.scheme, b.netloc) or not path.startswith("/") or path.startswith("//"):
        raise FetchError("refused: another origin")
    return url


def probe(key, target, now, timeout=3.0, token="", hister_token=None):
    """One app's result (never raises). target: its base URL, or for vault-mirror a file path; "" = absent.
    hister_token: LANDING_HISTER_TOKEN_FILE's SecretFile, for Hister only."""
    if not target:
        return dict(result("absent"), ms=0)
    started = time.monotonic()
    try:
        if key == "vault-mirror":
            out = vault_mirror(target, now)
        elif key == "feed-import":
            out = feed_import(target, now)
        elif key == "hister":
            out = hister(target.rstrip("/"), now, timeout, auth_headers(key, target, token), hister_token)
        else:
            open_headers = auth_headers(key, target, token)          # the open probes: no owner's token
            if key in ("kura", "konbini"):
                out = READERS[key](target.rstrip("/"), now, timeout, open_headers,
                                   owner=owner_headers(key, target, token, hister_token))
            else:
                out = READERS[key](target.rstrip("/"), now, timeout, open_headers)
    except FetchError as e:
        out = result("down", error=str(e))
    except Exception as e:              # an answer we couldn't read must never take the page down
        out = result("error", error="unreadable answer (%s)" % type(e).__name__)
    out["ms"] = int((time.monotonic() - started) * 1000)
    return out


# -- sync freshness, from the apps' answers ----------------------------------------------------------------------

def sync_rows(apps, now, board_behind_since=None):
    """[{key, label, state, text, at, source}] for the Sync section; rows whose app is absent are left out.
    board_behind_since: when Konbini's commit first differed from Kura's (the poller keeps it)."""
    rows = []
    k, b, n, h, m = (apps.get(x) or {} for x in ("kura", "konbini", "niwa", "hister", "vault-mirror"))
    kd, bd, nd, hd, md = (x.get("data") or {} for x in (k, b, n, h, m))
    if k.get("state") not in (None, "absent", "down") and kd:
        st = "error" if k.get("state") == "error" else (age_state(kd.get("synced_at"), now, "pull") or "starting")
        rows.append({"key": "pull", "label": "Vault pulled", "source": "Kura", "state": st, "at": kd.get("synced_at"),
                     "text": "at %s" % short(kd["head"]) if kd.get("head") else "not synced yet", "error": k.get("error", "")})
    if m.get("state") not in (None, "absent", "down") and md:
        rows.append({"key": "mirror", "label": "Vault fetched", "source": "vault-mirror", "state": m["state"],
                     "at": md.get("synced_at"), "text": "at %s" % short(md["head"]) if md.get("head") else "", "error": m.get("error", "")})
    if b.get("state") not in (None, "absent", "down") and bd:
        live = bd.get("livesync") or {}
        parts, st = [], "up"
        if bd.get("head"):
            same = kd.get("head") and bd["head"] == kd["head"]
            parts.append("at %s%s" % (short(bd["head"]), " (the vault's head)" if same else ""))
            if kd.get("head") and not same and board_behind_since and FRESH["board"][0] < now - board_behind_since:
                st = "behind"
        parts.append(writes(bd))
        if bd.get("sync_error"):
            st = "error"
        at = None
        if live:
            at = live.get("last_cycle")
            st = worst(st, age_state(at, now, "livesync"))
            if live.get("daemon") and live["daemon"] != "running":
                st, parts = "error", parts + ["LiveSync %s" % live["daemon"]]
            elif live.get("errors"):
                st, parts = worst(st, "behind"), parts + [plural(live["errors"], "LiveSync problem")]
        rows.append({"key": "board", "label": "Board synced", "source": "Konbini", "state": st, "at": at,
                     "text": " · ".join(p for p in parts if p), "error": bd.get("sync_error", "")})
    if n.get("state") not in (None, "absent", "down") and nd:
        st = "error" if nd.get("sync_error") else ("behind" if (nd.get("ahead") or 0) > 0 else "up")
        rows.append({"key": "garden", "label": "Garden writes", "source": "Niwa", "state": st, "at": None,
                     "text": " · ".join(p for p in ("at %s" % short(nd["head"]) if nd.get("head") else "", writes(nd)) if p),
                     "error": nd.get("sync_error", "")})
    push = kd.get("push") if kd else None
    if push:
        st = "error" if push.get("error") else (age_state(push.get("at"), now, "push") or "up")
        bits = [plural(int(push["docs"]), "note") + " in Hister" if push.get("docs") is not None else "",
                plural(int(push["failed"]), "failure") if push.get("failed") else ""]
        rows.append({"key": "push", "label": "Notes indexed", "source": "Kura → Hister", "state": st, "at": push.get("at"),
                     "text": " · ".join(x for x in bits if x), "error": push.get("error", "")})
    if h.get("state") == "up":
        rows.append({"key": "pages", "label": "Pages indexed", "source": "Hister",
                     "state": age_state(hd.get("newest"), now, "pages") or "up", "at": hd.get("newest"),
                     "text": "newest page" if hd.get("newest") else "", "error": ""})
    elif h.get("state") in BAD:
        rows.append({"key": "pages", "label": "Pages indexed", "source": "Hister", "state": "error", "at": None,
                     "text": "", "error": "Hister is %s" % ("down" if h["state"] == "down" else "failing")})
    f = apps.get("feed-import") or {}
    fd = f.get("data") or {}
    if f.get("state") not in (None, "absent", "down") and fd:
        bits = []
        if fd.get("added_day") is not None:
            since = fd.get("added_since")
            bits.append("%s added %s" % (count(fd["added_day"]),
                                          "in the last day" if not since else "since the page started watching"))
        if fd.get("failures"):
            bits.append(plural(int(fd["failures"]), "failed run"))
        rows.append({"key": "feeds", "label": "Feeds read", "source": "feed-import", "state": f["state"],
                     "at": fd.get("last_success"), "text": " · ".join(bits), "error": fd.get("error", "")})
    return rows


def writes(d):
    """'all pushed' / '2 waiting, 1 not pushed' from a writer's sync counts."""
    pending, ahead = d.get("pending"), d.get("ahead")
    if pending is None and ahead is None:
        return ""
    bits = []
    if pending:
        bits.append("%d waiting" % pending)
    if ahead:
        bits.append("%d not pushed" % ahead)
    return ", ".join(bits) or "all pushed"


ROW_APP = {"pull": "kura", "mirror": "vault-mirror", "board": "konbini", "garden": "niwa", "push": "kura", "pages": "hister",
           "feeds": "feed-import"}


def overall(apps, rows):
    """The page's one-line summary: {state, text}."""
    bad = [NAMES.get(k, k) for k, a in apps.items() if a.get("state") in BAD]
    bad += [r["label"] for r in rows if r["state"] == "error" and NAMES.get(ROW_APP.get(r["key"])) not in bad]
    behind = [NAMES.get(k, k) for k, a in apps.items() if a.get("state") in ("behind", "starting")]
    behind += [r["label"] for r in rows if r["state"] == "behind"]
    present = [k for k, a in apps.items() if a.get("state") != "absent"]
    if bad:
        return {"state": "error", "count": len(bad),
                "text": "%s need%s a look: %s" % (len(bad), "s" if len(bad) == 1 else "", ", ".join(bad))}
    if behind:
        return {"state": "behind", "count": len(behind), "text": "Everything answers; %s %s behind: %s" % (
            len(behind), "is" if len(behind) == 1 else "are", ", ".join(behind))}
    if not present:
        return {"state": "behind", "text": "No apps are configured yet"}
    return {"state": "up", "text": "Everything is up" if len(present) > 1 else "%s is up" % NAMES.get(present[0], present[0])}
