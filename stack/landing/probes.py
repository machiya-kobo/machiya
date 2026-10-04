"""What the landing page asks each app in the stack, and how it reads the answers (docs/services/landing.md).

Every app is optional. An app with no address is "absent" (shown as "Not in this stack", never as an error); one that
doesn't answer within the timeout is "down". Only GETs are sent, never with a redirect followed, and an answer larger
than LIMIT bytes is cut off and counted as unreadable. Nothing here renders HTML: it turns answers into plain values.
"""
import json
import re
import socket
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
]
NAMES = {k: n for k, n, _, _ in APPS}
ROOMS_WITH_TOKEN = ("kura", "niwa", "konbini")     # LANDING_TOKEN_FILE goes to these only (never Hister or SearXNG)

# Freshness, in seconds: (behind, broken). "Behind" is drawn calm (a yellow dot); only "broken" is drawn as an alert.
FRESH = {
    "pull": (15 * 60, 6 * 3600),        # the vault's last pull (Kura, the mirror): they poll every minute
    "livesync": (15 * 60, 2 * 3600),    # Konbini's LiveSync cycle
    "push": (60 * 60, 24 * 3600),       # Kura's last push of the notes into Hister
    "board": (15 * 60, None),           # Konbini's commit differs from Kura's for this long
}
STATES = ("up", "behind", "starting", "error", "down", "absent")
BAD = ("error", "down")


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


def plural(n, word, many=None):
    return "%s %s" % ("{:,}".format(n), word if n == 1 else (many or word + "s"))


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

def kura(base, now, timeout, headers):
    """GET /api/status (open; contracts/kura-api.md): the vault's head and last pull, the note count, the Hister push."""
    d = fetch_json(base + "/api/status", headers, timeout)
    synced, notes = num(d.get("synced_at")), num(d.get("notes"))
    push = d.get("push") if isinstance(d.get("push"), dict) else {}
    state = "starting" if d.get("ready") is False else (age_state(synced, now, "pull") or "up")
    if d.get("error"):
        state = "error"
    facts = [plural(int(notes), "note") if notes is not None else ""]
    return result(state, d.get("version"), vk(d.get("vaultkit")), facts, d.get("error"),
                  {"head": text(d.get("head"), 64), "synced_at": synced, "push": {
                      "at": num(push.get("at")), "docs": num(push.get("docs")), "failed": num(push.get("failed")),
                      "error": text(push.get("error")), "complete": push.get("complete")} if push else None})


def konbini(base, now, timeout, headers):
    """GET /api/health (contracts/konbini-api.md; gated on the owner): cards, the vault head, sync and LiveSync."""
    d = fetch_json(base + "/api/health", headers, timeout)
    sync = d.get("sync") if isinstance(d.get("sync"), dict) else {}
    live = ((d.get("livesync") or {}).get("status") or {}) if isinstance(d.get("livesync"), dict) else {}
    cards = num(d.get("cards"))
    err = text(sync.get("error")) or ("" if d.get("ok", True) else "not ok")
    state = "error" if err else "up"
    return result(state, d.get("version"), vk(d.get("vaultkit")), [plural(int(cards), "card") if cards is not None else ""], err,
                  {"head": text(d.get("head"), 64), "pending": num(sync.get("pending")), "ahead": num(sync.get("ahead")),
                   "sync_error": text(sync.get("error")), "livesync": {
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
                  ["%s published" % "{:,}".format(int(pub)) if pub is not None else "",
                   plural(int(notes), "note") if notes is not None else ""], err,
                  {"head": text(d.get("head"), 64), "pending": num(sync.get("pending")), "ahead": num(sync.get("ahead")),
                   "sync_error": text(sync.get("error"))})


BUILD_RE = re.compile(rb"""(?:app|theme)\.(?:js|css)\?v=([0-9a-f]{6,40})""")


def shiori(base, now, timeout, headers):
    """GET / (the hosted search page): up when it answers with a page; its build is the assets' ?v= hash."""
    _, ctype, body = fetch(base + "/", dict(headers, Accept="text/html"), timeout)
    if "html" not in ctype.lower():
        raise FetchError("not a page")
    m = BUILD_RE.search(body)
    return result("up", ("build " + m.group(1).decode()[:7]) if m else "", "", ["hosted search page"])


def hister(base, now, timeout, headers):
    """GET /api/stats (page count), then the newest page by date (a search; vault notes left out: Kura's push covers
    them). Hister doesn't report its version."""
    h = dict(headers, Origin=HISTER_ORIGIN)
    d = fetch_json(base + "/api/stats", h, timeout)
    docs = num(d.get("doc_count"))
    newest = None
    try:
        s = fetch_json(base + "/search?format=json&sort=date&q=" + quote("* -label:vault -metadata.source:vault"), h, timeout)
        first = (s.get("documents") or [None])[0]
        if isinstance(first, dict):
            newest = num(first.get("updated")) or num(first.get("added"))
    except FetchError:
        pass                                            # the count is enough to say it's up
    return result("up", "", "", [plural(int(docs), "page") if docs is not None else ""], "",
                  {"docs": docs, "newest": newest})


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
                  ["%s saved" % "{:,}".format(int(saved)) if saved is not None and h.get("enabled") else ""], err)


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


READERS = {"kura": kura, "konbini": konbini, "niwa": niwa, "shiori": shiori, "hister": hister, "searxng": searxng,
           "machiya-mcp": machiya_mcp, "smallweb": smallweb}


def auth_headers(key, target, token):
    """LANDING_TOKEN_FILE's token, as a Bearer, for Kura, Niwa and Konbini over https only (identity.md); nothing for
    the engines or the stack services, and nothing over plain http."""
    if token and key in ROOMS_WITH_TOKEN and urlsplit(target).scheme == "https":
        return {"Authorization": "Bearer " + token}
    return {}


def probe(key, target, now, timeout=3.0, token=""):
    """One app's result (never raises). target: its base URL, or for vault-mirror a file path; "" = absent."""
    if not target:
        return dict(result("absent"), ms=0)
    started = time.monotonic()
    try:
        if key == "vault-mirror":
            out = vault_mirror(target, now)
        else:
            out = READERS[key](target.rstrip("/"), now, timeout, auth_headers(key, target, token))
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
    if h.get("state") == "up" and hd:
        rows.append({"key": "pages", "label": "Pages indexed", "source": "Hister", "state": "up", "at": hd.get("newest"),
                     "text": "newest page" if hd.get("newest") else "", "error": "", "quiet": True})
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


ROW_APP = {"pull": "kura", "mirror": "vault-mirror", "board": "konbini", "garden": "niwa", "push": "kura", "pages": "hister"}


def overall(apps, rows):
    """The page's one-line summary: {state, text}."""
    bad = [NAMES.get(k, k) for k, a in apps.items() if a.get("state") in BAD]
    bad += [r["label"] for r in rows if r["state"] == "error" and NAMES.get(ROW_APP.get(r["key"])) not in bad]
    behind = [NAMES.get(k, k) for k, a in apps.items() if a.get("state") in ("behind", "starting")]
    behind += [r["label"] for r in rows if r["state"] == "behind"]
    present = [k for k, a in apps.items() if a.get("state") != "absent"]
    if bad:
        return {"state": "error", "text": "%s need%s a look: %s" % (len(bad), "s" if len(bad) == 1 else "", ", ".join(bad))}
    if behind:
        return {"state": "behind", "text": "Everything answers; %s %s behind: %s" % (
            len(behind), "is" if len(behind) == 1 else "are", ", ".join(behind))}
    if not present:
        return {"state": "behind", "text": "No apps are configured yet"}
    return {"state": "up", "text": "Everything is up" if len(present) > 1 else "%s is up" % NAMES.get(present[0], present[0])}
