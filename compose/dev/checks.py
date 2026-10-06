"""The dev stack's checks (run by `./dev check [--browser]`): every service answers, the sign-in gates hold, the
synthetic seed is where it should be, the MCP and feed-import work. HTTP checks need only the standard library;
--browser adds Playwright (Chromium and WebKit) for the sign-in and the rooms, with screenshots.

    python3 checks.py --data DIR [--browser --shots DIR] [--only NAME,...]

Talks to DEV_ADDR:<port> (127.0.0.1, or the --bind of a second stack) for the HTTP checks and to DEV_URL (the public
base the stack was made with) in the browser. --browser also checks the settings that follow a person
(docs/contracts/prefs.md) and the automatic sign-in (MACHIYA_SIGNIN_PROVIDER), and stops and starts hister-login
once to show the rooms carry on without it. The dummy owner's password and token are read from DIR/secrets and never printed. Each check prints
PASS/FAIL with its evidence; the exit code is the number of failures.
"""
import argparse
import json
import os
import re
import ssl
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

RESULTS = []
OPTIONAL = set()                                   # services a native stack may leave out (--native: searxng)
FRONT = os.environ.get("DEV_FRONT_SCHEME", "https")
ADDR = os.environ.get("DEV_ADDR") or "127.0.0.1"   # where this machine reaches the published ports
UNVERIFIED = ssl._create_unverified_context()      # the throwaway dev CA; this machine to itself only


def record(name, ok, evidence=""):
    RESULTS.append((name, bool(ok)))
    print("%s  %s%s" % ("PASS" if ok else "FAIL", name, ("\n      " + evidence.replace("\n", "\n      ")) if evidence
                                                         else ""), flush=True)


def http(method, url, data=None, headers=None, form=False, timeout=20, follow=True):
    body = None
    h = dict(headers or {})
    if data is not None:
        body = (urllib.parse.urlencode(data) if form else json.dumps(data)).encode()
        h.setdefault("Content-Type", "application/x-www-form-urlencoded" if form else "application/json")
    req = urllib.request.Request(url, body, h, method=method)
    https = urllib.request.HTTPSHandler(context=UNVERIFIED)
    opener = urllib.request.build_opener(https) if follow else urllib.request.build_opener(https, NoRedirect)
    try:
        with opener.open(req, timeout=timeout) as r:
            raw, status, hdrs = r.read(), r.status, r.headers
    except urllib.error.HTTPError as e:
        raw, status, hdrs = e.read(), e.code, e.headers
    except (urllib.error.URLError, OSError) as e:
        return 0, str(e), {}
    try:
        return status, (json.loads(raw) if raw.strip() else None), hdrs
    except ValueError:
        return status, raw.decode("utf-8", "replace"), hdrs


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *a, **k):
        return None


def L(port, path=""):
    """The front's ports (19200-19208) in its scheme; the plain ones (Hister 19224, the MCP 19226, ...) over http."""
    return "%s://%s:%d%s" % (FRONT if 19200 <= port <= 19208 else "http", ADDR, port, path)


def hister_total(tok, q):
    st, body, _ = http("GET", L(19224, "/search?" + urllib.parse.urlencode({"q": q, "limit": 100})),
                       headers={"Origin": "hister://", "Accept": "application/json", "X-Access-Token": tok})
    if st != 200 or not isinstance(body, dict):
        return -1
    return body.get("total", len(body.get("documents") or []))


def mcp(port, method, params=None, headers=None):
    h = {"Accept": "application/json, text/event-stream", "X-Agent": "dev-check"}
    h.update(headers or {})
    st, body, _ = http("POST", L(port, "/mcp"), {"jsonrpc": "2.0", "id": 1, "method": method, "params": params or {}},
                       headers=h)
    if isinstance(body, str) and "data:" in body:                 # an SSE answer
        body = json.loads(body.split("data:", 1)[1].strip().splitlines()[0])
    return st, body


# -- HTTP ------------------------------------------------------------------------------------------------------------

def http_checks(data, tok, password, rtok="", denv=None):
    bearer = {"Authorization": "Bearer " + (rtok or tok)}        # the rooms: a room token (hister-login 0.3.0)
    probes = [("landing", 19200, "/healthz"), ("kura", 19201, "/api/status"), ("niwa", 19202, "/api/status"),
              ("konbini", 19203, "/api/health"), ("hister", 19204, "/health"), ("hister-login", 19204,
              "/machiya/healthz"), ("searxng", 19205, "/healthz"), ("machiya-mcp", 19206, "/healthz"),
              ("smallweb", 19207, "/api/status"), ("stub-oidc", 19208, "/.well-known/openid-configuration"),
              ("fixtures", 19209, "/healthz"), ("fake-newsblur", 19210, "/healthz"),
              ("fake-forgejo", 19213, "/healthz"), ("fake-github", 19214, "/healthz"),
              ("hister (plain)", 19224, "/health"), ("machiya-mcp (plain)", 19226, "/healthz")]
    seen = {}
    for name, port, path in probes:
        st, body, _ = http("GET", L(port, path), timeout=8)
        seen[name] = st
    optional = OPTIONAL & {n for n, st in seen.items() if st != 200}
    record("every service answers on its port (19200-19210, 19213, 19214, 19224, 19226)",
           all(v == 200 for n, v in seen.items() if n not in optional),
           ", ".join("%s %s" % kv for kv in seen.items()) + ("; optional here, not running: %s" % ", ".join(
               sorted(optional)) if optional else ""))

    st, body, _ = http("GET", L(19204, "/machiya/healthz"))
    record("hister-login: Hister has users (hister ok)", isinstance(body, dict) and body.get("hister") == "ok",
           str(body))

    gates = {}
    for name, port in (("kura", 19201), ("niwa", 19202), ("konbini", 19203), ("landing", 19200)):
        st, _, hdrs = http("GET", L(port, "/"), headers={"Accept": "text/html"}, follow=False)
        loc = (hdrs or {}).get("Location", "") if hdrs else ""
        st_api, body, _ = http("GET", L(port, {"kura": "/api/search?q=x", "konbini": "/api/cards",
                                               "landing": "/api/prefs"}.get(name, "/api/garden")))
        gates[name] = (st, "/machiya/signin" in loc, st_api, isinstance(body, dict) and "signin" in body)
    record("signed out: each room's (and landing's) page goes to the helper's sign-in, its API answers 401 {signin}",
           all(v[0] in (302, 303) and v[1] and v[2] == 401 and v[3] for v in gates.values()), str(gates))

    st, body, _ = http("GET", L(19201, "/api/search?q=bamboo"), headers=bearer)
    titles = [r.get("title") for r in (body or {}).get("results", [])] if isinstance(body, dict) else []
    record("Kura: the stack's room token works; search 'bamboo' finds notes", st == 200 and "Bamboo frames" in titles,
           "HTTP %s, %d result(s): %s" % (st, len(titles), titles[:5]))

    st, body, _ = http("GET", L(19201, "/api/vaults"), headers=bearer)
    vaults = {v["name"]: v.get("private") for v in (body or {}).get("vaults", [])} if isinstance(body, dict) else {}
    st2, b2, _ = http("GET", L(19201, "/api/search?q=quillwort&vault=work"), headers=bearer)
    st3, b3, _ = http("GET", L(19201, "/api/search?q=quillwort"), headers=bearer)
    n_work = (b2 or {}).get("total") if isinstance(b2, dict) else None
    n_default = (b3 or {}).get("total") if isinstance(b3, dict) else None
    record("Kura: three vaults (work private); the private note is found in work only",
           vaults == {"personal": False, "team": False, "work": True} and n_work == 1 and n_default == 0,
           "vaults %s; 'quillwort' in work %s, in the default %s" % (vaults, n_work, n_default))

    st, body, _ = http("GET", L(19203, "/api/cards"), headers=bearer)
    cards = body.get("cards", body) if isinstance(body, dict) else body if isinstance(body, list) else []
    cols = sorted({c.get("board") for c in cards if isinstance(c, dict)} - {None})
    record("Konbini: the board has the seed's cards in every column",
           st == 200 and len(cards) >= 10 and {"backlog", "ready", "wip", "blocked", "done", "archived"} <= set(cols),
           "HTTP %s, %d card(s), columns %s" % (st, len(cards), cols))

    st, body, _ = http("GET", L(19202, "/api/status"))
    published = body.get("published") if isinstance(body, dict) else None
    st2, page, _ = http("GET", L(19202, "/"), headers=bearer)
    record("Niwa: the garden is published (9 notes) and its home page loads with the room token",
           st == 200 and (published or 0) >= 9 and st2 == 200 and "Bamboo frames" in str(page),
           "published %s; GET / %s" % (published, st2))

    counts = {q: hister_total(tok, q) for q in ("@pages", "label:vault", "metadata.source:newsblur", "quillwort",
                                                 "label:travel")}
    record("Hister: the synthetic corpus, Kura's notes (personal + shared team, never work), feed-import's pages",
           counts["@pages"] >= 12 and counts["label:vault"] >= 29 and counts["quillwort"] == 0
           and counts["metadata.source:newsblur"] >= 4 and counts["label:travel"] >= 3, str(counts))

    h = {"Origin": "hister://", "Accept": "application/json", "X-Access-Token": tok}
    st, rules, _ = http("GET", L(19224, "/api/rules"), headers=h)
    aliases = sorted(((rules or {}).get("aliases") or {}).keys()) if isinstance(rules, dict) else []
    st2, hist, _ = http("GET", L(19224, "/api/history?opened=true"), headers=h)
    n_hist = len((hist or {}).get("documents") or []) if isinstance(hist, dict) else -1
    st3, anon, _ = http("GET", L(19224, "/api/rules"), headers={"Origin": "hister://", "Accept": "application/json"})
    record("Hister: collections (aliases) and history are the owner's; no token, no answer",
           {"@notes", "@pages", "@code", "@travel", "@workshop"} <= set(aliases) and n_hist >= 3 and st3 in (401, 403),
           "aliases %s; opened history %d; without a token HTTP %s" % (aliases, n_hist, st3))

    try:
        with open(os.path.join(data, "feed-import", "status.json")) as f:
            fs = json.load(f)
    except (OSError, ValueError) as e:
        fs = {"error": str(e)}
    st, nb, _ = http("GET", L(19210, "/healthz"))
    record("feed-import: imports the fake NewsBlur's read and starred stories into Hister",
           fs.get("ok") is True and isinstance(nb, dict) and nb.get("calls", 0) > 0
           and counts["metadata.source:newsblur"] >= 4,
           "status.json ok=%s; fake NewsBlur served %s call(s); Hister has %s newsblur page(s)" % (
               fs.get("ok"), (nb or {}).get("calls") if isinstance(nb, dict) else nb, counts["metadata.source:newsblur"]))

    code_checks(data, tok)

    st, body = mcp(19226, "tools/list")
    tools = sorted(t["name"] for t in ((body or {}).get("result") or {}).get("tools", [])) if isinstance(body, dict) \
        else []
    st2, status = mcp(19226, "tools/call", {"name": "machiya_status", "arguments": {}})
    rooms = (((status or {}).get("result") or {}).get("structuredContent") or {}).get("rooms", {}) \
        if isinstance(status, dict) else {}
    st3, found = mcp(19226, "tools/call", {"name": "notes_search", "arguments": {"q": "washi"}})
    text = json.dumps(found)[:4000]
    record("machiya-mcp: lists its tools; machiya_status sees every room; notes_search reaches Kura",
           len(tools) >= 10 and rooms and all(v.get("ok") for v in rooms.values()) and "Washi paper" in text,
           "%d tools; rooms %s; notes_search washi: %s" % (
               len(tools), {k: v.get("ok") for k, v in rooms.items()}, "Washi paper" in text))

    st, body = mcp(19224, "tools/list", headers={"X-Access-Token": tok})
    htools = sorted(t["name"] for t in ((body or {}).get("result") or {}).get("tools", [])) if isinstance(body, dict) \
        else []
    st2, anon = mcp(19224, "tools/list")
    record("Hister's MCP: tools with the token, refused without", {"search", "get_preview"} <= set(htools)
           and st2 in (401, 403), "with token HTTP %s %s; without HTTP %s" % (st, htools, st2))

    for _ in range(12):                               # landing polls every 20 s
        st, body, _ = http("GET", L(19200, "/api/status"), headers=bearer)
        states = {k: (v or {}).get("state") for k, v in ((body or {}).get("apps") or {}).items()} \
            if isinstance(body, dict) else {}
        if states and all(v == "up" for k, v in states.items() if k != "shiori" and k not in optional):
            break
        time.sleep(5)
    apps = (body or {}).get("apps") if isinstance(body, dict) else None
    if isinstance(apps, list):
        apps = {a.get("name") or a.get("key"): a for a in apps}
    up = {k: (v.get("state") if isinstance(v, dict) else v) for k, v in (apps or {}).items()}
    st2, page, _ = http("GET", L(19200, "/status"), headers=bearer)
    record("landing: /status page, and every app of the stack up in /api/status (Shiori is a client: absent)",
           st == 200 and st2 == 200 and up and all(v == "up" for k, v in up.items()
                                                   if k != "shiori" and k not in optional), str(up))

    st, body, _ = http("GET", L(19205, "/search?q=lantern&format=json"))
    if "searxng" in optional:
        print("SKIP  SearXNG (not part of this native stack)")
    else:
      record("SearXNG: JSON search answers (web results may be sparse)", st == 200 and isinstance(body, dict),
             "HTTP %s, %s result(s)" % (st, len((body or {}).get("results", [])) if isinstance(body, dict) else "?"))

    st, body, _ = http("GET", L(19207, "/api/status"))
    record("smallweb: up, saves into Hister", st == 200 and isinstance(body, dict) and body.get("ok"), str(body)[:300])

    forge_tokens = []
    for name in ("forgejo-token", "github-lantern-token", "github-workshop-token"):
        try:
            with open(os.path.join(data, "secrets", name)) as f:
                forge_tokens.append(f.readline().strip())
        except OSError:
            pass
    room_session_checks(data, password, tok, rtok, denv or {})

    leaks = scan_logs(data, [tok, password, rtok] + forge_tokens)
    record("no secret in any service log (the owner's password and Hister token, the room token, the fake forges' "
           "tokens)", not leaks, ", ".join(leaks))


# -- room sessions (hister-login 0.3.0, vaultkit 0.22: docs/identity.md "One cookie per room") -----------------------

def set_cookies(hdrs):
    return list(hdrs.get_all("Set-Cookie") or []) if hdrs else []


def cookie_named(cookies, name):
    for c in cookies:
        k, _, rest = c.partition("=")
        if k.strip() == name:
            return rest.split(";", 1)[0], c
    return None, None


def room_session_checks(data, password, tok, rtok, denv):
    """At the HTTP level, as a browser would: a room's code works once, only at its room, only with its browser's
    nonce; the room's cookie is host-only; a room session works in its own room only; Sign Out in one room ends the
    others; with HISTER_LOGIN_LEGACY=none Hister's raw token opens no room while the room token does."""
    import base64
    import hashlib
    pub = os.environ.get("DEV_URL", "https://localhost").rstrip("/")
    secure = pub.startswith("https://")
    base = (denv.get("DEV_SSO_COOKIE") or "machiya_sso")
    pre = "__Host-" if secure else ""
    room_cookie = lambda room: pre + base + "_" + room                      # noqa: E731
    ports = {"kura": 19201, "niwa": 19202, "konbini": 19203, "landing": 19200}
    s256 = lambda n: base64.urlsafe_b64encode(hashlib.sha256(n.encode()).digest()).rstrip(b"=").decode()  # noqa
    nonce = lambda: base64.urlsafe_b64encode(os.urandom(32)).rstrip(b"=").decode()                         # noqa
    # a browser's Hister session, as the helper's sign-in page gets it (its script posts to Hister's /api/login)
    st, _, hdrs = http("POST", L(19204, "/api/login"), {"username": "owner", "password": password},
                       headers={"Origin": "hister://", "Accept": "application/json"}, follow=False)
    hister, _ = cookie_named(set_cookies(hdrs), "hister")
    if not hister:
        record("room sessions: a Hister sign-in for the checks", False, "POST /api/login: HTTP %s" % st)
        return
    own = {}

    def trip(room, n):
        """The room's trip to the helper with state=S256(n) -> (Location, code)."""
        cookie = "hister=" + hister + ("; %s=%s" % (pre + base, own["sid"]) if own.get("sid") else "")
        st, _, h = http("GET", L(19204, "/machiya/signin?" + urllib.parse.urlencode(
            {"return": "%s:%d/" % (pub, ports[room]), "state": s256(n)})), headers={"Cookie": cookie}, follow=False)
        sid, _ = cookie_named(set_cookies(h), pre + base)
        if sid:
            own["sid"] = sid
        loc = (h or {}).get("Location", "") if h else ""
        return loc, (urllib.parse.parse_qs(urllib.parse.urlsplit(loc).query).get("code") or [""])[0]

    def callback(room, code, n):
        h = {"Accept": "text/html"}
        if n:
            h["Cookie"] = "%s_state=%s" % (room_cookie(room), n)
        st, _, hdrs = http("GET", L(ports[room], "/machiya/callback?" + urllib.parse.urlencode({"code": code})),
                           headers=h, follow=False)
        value, raw = cookie_named(set_cookies(hdrs), room_cookie(room))
        return st, value, raw, (hdrs or {}).get("Location", "") if hdrs else ""

    # one trip: the code comes back to Kura's callback; Kura trades it for its own host-only cookie
    n = nonce()
    loc, code = trip("kura", n)
    st, kura_rs, raw, where = callback("kura", code, n)
    attrs = [a.strip() for a in (raw or "").split(";")[1:]]
    record("room sessions: the helper sends the browser back to Kura's /machiya/callback with a one-time code; Kura "
           "trades it for its own cookie %s (host-only: Secure, HttpOnly, SameSite=Lax, Path=/, no Domain)"
           % room_cookie("kura"),
           loc.startswith("%s:19201/machiya/callback?code=mhc_" % pub) and n not in loc and st == 302
           and (kura_rs or "").startswith("mhr_") and where == "%s:19201/" % pub and "HttpOnly" in attrs
           and "SameSite=Lax" in attrs and "Path=/" in attrs and ("Secure" in attrs or not secure)
           and not any(a.lower().startswith("domain") for a in attrs),
           "Location %s…; callback HTTP %s -> %s; cookie attributes %s" % (loc[:60], st, where, attrs))
    k = {"Cookie": "%s=%s" % (room_cookie("kura"), kura_rs)}
    st_ok, _, _ = http("GET", L(19201, "/api/search?q=bamboo"), headers=k)
    st_replay, again, _, _ = callback("kura", code, n)
    record("room sessions: the code works once (a replay is refused and makes no cookie); the new cookie opens Kura",
           st_ok == 200 and st_replay == 401 and not again, "Kura with the cookie %s; replay HTTP %s, cookie %s" % (
               st_ok, st_replay, bool(again)))
    # bound to its room and to this browser's nonce
    n = nonce()
    _, code = trip("kura", n)
    st_other, other, _, _ = callback("niwa", code, n)                    # Kura's code at Niwa's door
    st_after, after, _, _ = callback("kura", code, n)                    # ... and it is gone
    n2 = nonce()
    _, code2 = trip("kura", n2)
    st_wrong, wrong, _, _ = callback("kura", code2, nonce())             # another browser's nonce
    _, code3 = trip("kura", nonce())
    st_none, none, _, _ = callback("kura", code3, "")                    # no state cookie at all
    record("room sessions: a code is bound: refused at another room (and then gone), with another browser's nonce, "
           "and with no state cookie",
           (st_other, st_after, st_wrong, st_none) == (401, 401, 401, 401) and not (other or after or wrong or none),
           "Niwa %s, Kura afterwards %s, wrong nonce %s, no state %s" % (st_other, st_after, st_wrong, st_none))
    # a room session works in its own room only
    st_niwa, b_niwa, _ = http("GET", L(19202, "/api/suggestions"), headers={"Cookie": "%s=%s" % (room_cookie("niwa"),
                                                                                           kura_rs)})
    st_bearer, _, _ = http("GET", L(19203, "/api/cards"), headers={"Authorization": "Bearer " + kura_rs})
    record("room sessions: Kura's session opens no other room (as Niwa's cookie, or as a Bearer at Konbini)",
           st_niwa == 401 and st_bearer == 401 and isinstance(b_niwa, dict) and b_niwa.get("reason") == "wrong-room",
           "Niwa %s %s; Konbini %s" % (st_niwa, b_niwa if isinstance(b_niwa, dict) else "", st_bearer))
    # Hister's raw token in the rooms, and the room token
    legacy = (denv.get("DEV_AUTH_LEGACY") or "").strip()
    raw_tok = {name: http("GET", L(port, path), headers={"Authorization": "Bearer " + tok})[0]
               for name, port, path in (("kura", 19201, "/api/search?q=x"), ("konbini", 19203, "/api/cards"),
                                        ("niwa", 19202, "/api/suggestions"))}
    xat = http("GET", L(19201, "/api/search?q=x"), headers={"X-Access-Token": tok})
    room_tok = {name: http("GET", L(port, path), headers={"Authorization": "Bearer " + rtok})[0]
                for name, port, path in (("kura", 19201, "/api/search?q=x"), ("konbini", 19203, "/api/cards"),
                                         ("niwa", 19202, "/api/suggestions"))} if rtok else {}
    if legacy == "none":
        record("the switch (HISTER_LOGIN_LEGACY=none): Hister's raw owner token opens no room (Bearer or "
               "X-Access-Token: 401 legacy-off); the room token opens every room",
               set(raw_tok.values()) == {401} and xat[0] == 401 and isinstance(xat[1], dict)
               and xat[1].get("reason") == "legacy-off" and room_tok and set(room_tok.values()) == {200},
               "raw token %s, X-Access-Token %s %s; room token %s" % (raw_tok, xat[0], xat[1] if isinstance(
                   xat[1], dict) else "", room_tok))
    else:
        record("before the switch (HISTER_LOGIN_LEGACY=%s): Hister's raw token still opens the rooms, and so does the "
               "room token" % (legacy or "default"), set(raw_tok.values()) == {200} and set(room_tok.values()) == {200},
               "raw token %s; room token %s" % (raw_tok, room_tok))
    # Sign Out in Kura ends Niwa's session of the same browser (the helper session and every room session made from it)
    n = nonce()
    _, code = trip("niwa", n)
    _, niwa_rs, _, _ = callback("niwa", code, n)
    nh = {"Cookie": "%s=%s" % (room_cookie("niwa"), niwa_rs)}
    before = http("GET", L(19202, "/api/suggestions"), headers=nh)[0]
    st_out, _, hdrs = http("POST", L(19201, "/signout"), {}, headers=dict(k, Origin="%s:19201" % pub), form=True,
                           follow=False)
    marker, mraw = cookie_named(set_cookies(hdrs), room_cookie("kura") + "_out")
    deadline, after = time.time() + 40, before
    while time.time() < deadline:
        after = http("GET", L(19202, "/api/suggestions"), headers=nh)[0]
        if after == 401:
            break
        time.sleep(3)
    record("room sessions: Sign Out in Kura ends the browser's Niwa session too (within Niwa's 30 s cache), and sets "
           "Kura's own host-only marker", before == 200 and st_out == 303 and after == 401 and marker == "1"
           and "domain" not in (mraw or "").lower(), "Niwa before %s, Kura sign-out %s, Niwa after %s; marker %s" % (
               before, st_out, after, mraw))


def code_checks(data, tok):
    """code-import: the fake forges' synthetic repos are in Hister as metadata.source:code, found by the Code area's
    query and its filters, and kept out of the pages. The first run may still be going: wait up to a minute."""
    fs = {}
    for _ in range(30):
        try:
            with open(os.path.join(data, "code-import", "status.json")) as f:
                fs = json.load(f)
        except (OSError, ValueError) as e:
            fs = {"error": str(e)}
        if fs.get("last_success"):
            break
        time.sleep(2)
    q = {name: hister_total(tok, query) for name, query in (
        ("code", "metadata.source:code"),
        ("lamp", "metadata.source:code metadata.code_repo:workshop_kobo__lamp"),
        ("open issues and PRs", "metadata.source:code metadata.code_kind:(issue|pr) metadata.code_state:open"),
        ("merged", "metadata.source:code metadata.code_state:merged"),
        ("@code dusk", "@code dusk"),
        ("@pages dusk", "@pages dusk"),
        ("left out", "metadata.source:code never imported"))}
    st, body, _ = http("GET", L(19224, "/search?" + urllib.parse.urlencode({"q": "metadata.source:code", "limit": 100})),
                       headers={"Origin": "hister://", "Accept": "application/json", "X-Access-Token": tok})
    urls = {d.get("url") for d in (body or {}).get("documents") or []} if isinstance(body, dict) else set()
    want = {"https://github.example/workshop-kobo/lamp", "http://forgejo.example/lantern/garden-notes/pulls/2",
            "https://github.example/lantern/pixel-font/issues/4", "http://forgejo.example/lantern/dotfiles"}
    record("code-import: a Code query finds the fake forges' synthetic repos, issues, PRs and releases (not the "
           "forks, mirrors, twins or excluded ones), and @pages leaves them out",
           fs.get("ok") is True and q["code"] >= 20 and q["lamp"] >= 7 and q["open issues and PRs"] >= 4
           and q["merged"] >= 2 and q["@code dusk"] >= 1 and q["@pages dusk"] == 0 and q["left out"] == 0
           and want <= urls,
           "status.json ok=%s; %s; missing %s" % (fs.get("ok"), q, sorted(want - urls)))


def scan_logs(data, needles):
    env = {}
    with open(os.path.join(data, "dev.env")) as f:
        for line in f:
            if "=" in line and not line.startswith("#"):
                k, _, v = line.strip().partition("=")
                env[k] = v
    texts = {}
    if env.get("DEV_ENGINE") == "native":
        logs = os.path.join(data, "logs")
        for name in os.listdir(logs):
            with open(os.path.join(logs, name), errors="replace") as f:
                texts[name] = f.read()
    else:
        engine = env.get("DEV_ENGINE", "podman")
        project = env.get("DEV_PROJECT") or "machiya-dev"          # this stack's containers only, not a neighbour's
        names = subprocess.run([engine, "ps", "-a", "--filter", "label=com.docker.compose.project=" + project,
                                "--format", "{{.Names}}"], capture_output=True, text=True).stdout
        for n in names.split():
            if n:
                r = subprocess.run([engine, "logs", n], capture_output=True, text=True, errors="replace")
                texts[n] = r.stdout + r.stderr
    return [n for n, t in texts.items() if any(x and x in t for x in needles)]


# -- browser ---------------------------------------------------------------------------------------------------------

def browser_checks(data, password, shots, base, sso_cookie="machiya_sso", denv=None, tok=""):
    from playwright.sync_api import sync_playwright
    denv = denv or {}
    auto = bool(denv.get("DEV_SIGNIN_PROVIDER"))     # MACHIYA_SIGNIN_PROVIDER: pages sign themselves in (stub OIDC)
    os.makedirs(shots, exist_ok=True)
    U = {n: "%s:%d" % (base, p) for n, p in (("landing", 19200), ("kura", 19201), ("niwa", 19202),
                                              ("konbini", 19203), ("hister", 19204))}

    def shot(page, name, engine):
        for scheme in ("light", "dark"):
            page.emulate_media(color_scheme=scheme)
            page.wait_for_timeout(250)
            page.screenshot(path=os.path.join(shots, "%s-%s-%s.png" % (engine, name, scheme)), full_page=False)

    def signin(page):
        """The helper's page, when it shows: name and password. With the automatic sign-in it doesn't (-> False)."""
        try:
            page.wait_for_selector("#hister-signin", timeout=5000 if auto else 15000)
        except Exception:
            if auto:
                return False
            raise
        page.fill("input[name=username]", "owner")
        page.fill("input[name=password]", password)
        page.click("#hister-signin button[type=submit]")
        return True

    def page_shown(p):
        """Whether the helper's sign-in page was shown to this page (not just passed through, as the automatic
        sign-in does)."""
        shown = []
        p.on("response", lambda r: shown.append(r.url) if "/machiya/signin" in r.url and r.status == 200
             and r.request.resource_type == "document" else None)
        return shown

    with sync_playwright() as pw:
        for engine in ("chromium", "webkit"):
            browser = getattr(pw, engine).launch()
            for vw, label in (({"width": 1280, "height": 860}, ""), ({"width": 390, "height": 844}, "phone-")):
                if label and engine == "webkit":
                    continue                          # phone shots once (Chromium); WebKit runs the flows at desktop
                ctx = browser.new_context(viewport=vw, ignore_https_errors=True)
                p = ctx.new_page()
                shown = page_shown(p)
                p.goto(U["kura"] + "/n/Notes/Bamboo%20frames")
                at_signin = "/machiya/signin" in p.url
                if label == "" and at_signin:
                    shot(p, "helper-signin", engine)
                signin(p)
                p.wait_for_url(U["kura"] + "/**", timeout=20000)
                back = p.url
                p.wait_for_load_state("networkidle")
                ok_kura = "Bamboo frames" in p.content()
                shot(p, label + "kura-note", engine)
                rooms = {}
                for name, path, want in (("niwa", "/", "Bamboo frames"), ("konbini", "/", "Lantern"),
                                         ("landing", "/status", "Kura")):
                    p.goto(U[name] + path)
                    p.wait_for_load_state("networkidle")
                    rooms[name] = (p.url.startswith(U[name]), want in p.content())
                    shot(p, label + name, engine)
                p.goto(U["kura"] + "/search?q=chochin")
                p.wait_for_load_state("networkidle")
                found = "Chochin folding" in p.content()
                shot(p, label + "kura-search", engine)
                p.goto(U["hister"] + "/")
                p.wait_for_load_state("networkidle")
                hister_in = "/machiya/signin" not in p.url and "login" not in p.url
                shot(p, label + "hister", engine)
                if label == "":
                    first = ("signed in automatically through the stub OIDC provider (no page, no click)" if auto
                             else "sign in once at the helper")
                    record("[%s] %s, then Kura, Niwa, Konbini, landing and Hister with no prompt" % (engine, first),
                           (not shown if auto else at_signin) and back.startswith(U["kura"]) and ok_kura
                           and all(a and b for a, b in rooms.values()) and hister_in,
                           "kura -> sign-in page shown %s, back at %s; rooms (no prompt, content) %s; Hister UI signed "
                           "in %s" % (bool(shown) or at_signin, back, rooms, hister_in))
                    record("[%s] Kura's search page finds 'chochin'" % engine, found, "")
                    jar = {c["name"]: c for c in ctx.cookies()}
                    pre = "__Host-" if base.startswith("https://") else ""
                    host = urllib.parse.urlsplit(base).hostname
                    want = [pre + sso_cookie] + [pre + sso_cookie + "_" + r for r in ("kura", "niwa", "konbini",
                                                                                     "landing")]
                    host_only = {n: (jar[n]["value"][:4], jar[n]["domain"], jar[n]["secure"], jar[n]["httpOnly"],
                                     jar[n]["sameSite"]) for n in want if n in jar}
                    legacy = (denv.get("DEV_AUTH_LEGACY") or "").strip() == "none"
                    other = {"machiya_sso", "machiya_dev_sso"} - ({sso_cookie} if not legacy else set())
                    record("[%s] one cookie per room, host-only: the helper's own %s (mhs_) and %s_<room> for Kura, "
                           "Niwa, Konbini and landing (mhr_), each Secure, HttpOnly, Lax, on %s alone (no Domain)%s"
                           % (engine, pre + sso_cookie, pre + sso_cookie, host,
                              "; no shared machiya_sso (the switch)" if legacy else ""),
                           len(host_only) == 5 and host_only[pre + sso_cookie][0] == "mhs_"
                           and all(v[0] == "mhr_" for n, v in host_only.items() if n != pre + sso_cookie)
                           and all(v[1] == host and (v[2] or not pre) and v[3] and v[4] == "Lax"
                                   for v in host_only.values())
                           and not (other & set(jar)),
                           "cookies %s; %s" % (sorted(jar), host_only))
                    # sign out everywhere from the helper's sessions page; the rooms send you back to sign in, and
                    # (the automatic sign-in) the helper shows its page instead of signing you straight back in
                    p.goto(U["hister"] + "/machiya/sessions")
                    p.click("button[value=all]")
                    p.wait_for_load_state("networkidle")
                    deadline, out = time.time() + 45, False
                    while time.time() < deadline and not out:
                        p.goto(U["konbini"] + "/")
                        out = "/machiya/signin" in p.url and p.locator("#hister-signin").count() == 1
                        if not out:
                            p.wait_for_timeout(3000)
                    record("[%s] sign out everywhere: Konbini shows the sign-in page again (cache <= 30 s; no "
                           "automatic way back in)" % engine, out, p.url)
                ctx.close()
            # a browser that also holds a production sign-in cookie for this host (the shared-tailnet case): with the dev
            # stack's own name it is ignored and left alone, and the sign-in doesn't loop
            if sso_cookie != "machiya_sso":
                ctx = browser.new_context(ignore_https_errors=True)
                prod = "mhs_" + "p" * 40
                ctx.add_cookies([{"name": "machiya_sso", "value": prod, "url": U["kura"] + "/"}])
                p = ctx.new_page()
                signins = []
                p.on("request", lambda r: signins.append(r.url) if "/machiya/signin" in r.url and
                     r.resource_type == "document" else None)
                p.goto(U["kura"] + "/n/Notes/Bamboo%20frames")
                signin(p)
                p.wait_for_url(U["kura"] + "/**", timeout=20000)
                p.wait_for_load_state("networkidle")
                ok = "Bamboo frames" in p.content()
                visits = {}
                for name, path in (("niwa", "/"), ("konbini", "/"), ("landing", "/status")):
                    p.goto(U[name] + path)
                    p.wait_for_load_state("networkidle")
                    visits[name] = p.url.startswith(U[name])
                names = {c["name"]: c["value"] for c in ctx.cookies()}
                record("[%s] with a production machiya_sso cookie too: one sign-in, no loop, the production cookie "
                       "untouched" % engine, ok and all(visits.values()) and len(signins) <= 2
                       and names.get("machiya_sso") == prod
                       and names.get(("__Host-" if base.startswith("https://") else "") + sso_cookie + "_kura",
                                     "").startswith("mhr_"),
                       "sign-in page loads %d; rooms %s; machiya_sso kept %s; Kura's own cookie set %s" % (
                           len(signins), visits, names.get("machiya_sso") == prod,
                           any(n.endswith(sso_cookie + "_kura") for n in names)))
                ctx.close()
            # the stub OIDC provider (tsidp's shape), bound to the owner
            ctx = browser.new_context(ignore_https_errors=True)
            p = ctx.new_page()
            shown = page_shown(p)
            p.goto(U["konbini"] + "/")
            if auto:
                p.wait_for_url(U["konbini"] + "/**", timeout=20000)
            else:
                p.click("text=Sign in with Tailscale")
                p.wait_for_url(U["konbini"] + "/**", timeout=20000)
            p.wait_for_load_state("networkidle")
            st = ctx.request.get(U["konbini"] + "/api/cards").status
            record("[%s] %s: the stub OIDC login is the owner" % (
                       engine, "a fresh browser opening Konbini lands signed in with no clicks" if auto
                       else "'Sign in with Tailscale (dev stub)'"),
                   p.url.startswith(U["konbini"]) and st == 200 and not (auto and shown),
                   "landed on %s; /api/cards %s; the sign-in page shown %s" % (p.url, st, bool(shown)))
            if auto:
                # Sign Out in a room (the form machiya.js adds to the Rooms menu): the next page shows the helper's
                # page, not an automatic sign-in; one tap on "Sign in with Tailscale" brings you back, and then the
                # sign-in is automatic again
                with p.expect_response(lambda r: r.url.endswith("/signout") and r.request.method == "POST"):
                    p.evaluate("() => document.querySelector('form.signout, form[action=\"/signout\"]').submit()")
                p.wait_for_timeout(500)
                pre = "__Host-" if base.startswith("https://") else ""
                marker = any(c["name"] == pre + sso_cookie + "_konbini_out" for c in ctx.cookies())   # Konbini's own
                deadline, page_after = time.time() + 45, False         # Niwa's cached answer lasts up to 30 s
                while time.time() < deadline and not page_after:
                    p.goto(U["niwa"] + "/")
                    p.wait_for_load_state("networkidle")
                    page_after = "/machiya/signin" in p.url and p.locator("#hister-signin").count() == 1
                    if not page_after:
                        p.wait_for_timeout(3000)
                if not page_after:
                    record("[%s] after Sign Out the helper's page shows" % engine, False, "marker %s; at %s" % (
                        marker, p.url))
                    ctx.close()
                    browser.close()
                    continue
                if engine == "chromium":
                    shot(p, "helper-signin-after-signout", engine)
                p.click("text=Sign in with Tailscale")
                p.wait_for_url(U["niwa"] + "/**", timeout=20000)
                p.wait_for_load_state("networkidle")
                back_in = p.url.startswith(U["niwa"])
                cleared = not any(c["name"] in (pre + sso_cookie + "_out", pre + sso_cookie + "_niwa_out")
                                  for c in ctx.cookies())
                record("[%s] after Sign Out the helper's page shows (no automatic way back in); one tap signs in, "
                       "and clears the marker" % engine, marker and page_after and back_in and cleared,
                       "marker set %s; page after sign-out %s; back in after a tap %s; marker cleared %s" % (
                           marker, page_after, back_in, cleared))
            ctx.close()
            browser.close()
        prefs_checks(pw, U, signin, shots, denv, tok)


# -- the settings that follow a person (docs/contracts/prefs.md) -----------------------------------------------------

def account(tok):
    st, body, _ = http("GET", L(19204, "/machiya/api/prefs"), headers={"X-Access-Token": tok,
                                                                       "Accept": "application/json"})
    return (body or {}).get("prefs", {}) if st == 200 and isinstance(body, dict) else {"error": st}


def body_state(p):
    return p.evaluate("() => ({cls: document.body.className, text: document.body.dataset.text})")


def prefs_checks(pw, U, signin, shots, denv, tok):
    """Theme changed in Kura shows in Konbini, Niwa and landing on the next load and on another device; only the
    changed key is sent; Use This Device's Size stays on its device; with the helper down the rooms keep working on
    their cookies, and the change waits and lands when it is back. Screenshots of the Shared section (landing's
    Settings: desktop and phone, light and dark)."""
    engine_cmd = denv.get("DEV_ENGINE", "podman")
    helper = "%s-hister-login-1" % (denv.get("DEV_PROJECT") or "machiya-dev")
    clear = {"theme": None, "palette": None, "text_size": None, "apps_hidden": None}
    http("PUT", L(19204, "/machiya/api/prefs"), {"prefs": clear}, headers={"X-Access-Token": tok})
    browser = pw.chromium.launch()

    def new(vw=None, **kw):
        ctx = browser.new_context(viewport=vw or {"width": 1280, "height": 900}, ignore_https_errors=True, **kw)
        p = ctx.new_page()
        p.goto(U["landing"] + "/status")
        signin(p)
        p.wait_for_url(U["landing"] + "/**", timeout=20000)
        p.wait_for_load_state("networkidle")
        return ctx, p

    def visit(p, room, path="/"):
        p.goto(U[room] + path)
        p.wait_for_load_state("networkidle")
        p.wait_for_timeout(300)
        return body_state(p)

    # the Appearance section, as the owner sees it (the account's defaults: theme System, so the shots follow the scheme)
    for vw, name, extra in (({"width": 1280, "height": 1100}, "desktop", {}),
                            ({"width": 390, "height": 844}, "phone", {"device_scale_factor": 2, "is_mobile": True,
                                                                      "has_touch": True})):
        ctx, p = new(vw, **extra)
        visit(p, "landing", "/settings")
        for scheme in ("light", "dark"):
            p.emulate_media(color_scheme=scheme)
            p.wait_for_timeout(300)
            p.screenshot(path=os.path.join(shots, "shared-section-%s-%s.png" % (name, scheme)), full_page=True)
        html = p.content()
        first = max(html.find('id="appearance"'), html.find('id="shared"'))     # "Shared" before vaultkit 0.23
        record("[prefs] landing's Settings starts with Appearance (%s): the rows with Use This Device's Size, "
               "'Follows you on every Machiya app when signed in.', the state line, then Account" % name,
               0 < first < html.find('id="account"') and "Follows you on every Machiya app when signed in." in html
               and "Saved to your account." in html and "Use This Device" in html,
               "shots/shared-section-%s-{light,dark}.png" % name)
        ctx.close()

    # A: the laptop. Theme and Appearance changed in Kura's Settings: one PUT per change, one key each
    a_ctx, a = new()
    puts = []
    a.on("request", lambda r: puts.append(r.post_data) if r.method == "PUT" and "/api/prefs" in r.url else None)
    visit(a, "kura", "/settings")
    with a.expect_response(lambda r: r.request.method == "PUT" and "/api/prefs" in r.url):
        a.select_option('select[data-set="palette"]', "nord")
    with a.expect_response(lambda r: r.request.method == "PUT" and "/api/prefs" in r.url):
        a.select_option('select[data-set="theme"]', "day")
    sent = [json.loads(x or "{}").get("prefs") for x in puts]
    acct = account(tok)
    record("[prefs] Theme and Appearance changed in Kura: each change PUTs its own key only, and the account has them",
           sent == [{"palette": "nord"}, {"theme": "day"}] and acct.get("palette") == "nord"
           and acct.get("theme") == "day", "PUTs %s; account %s" % (sent, acct))
    seen = {room: body_state(a) if False else visit(a, room) for room in ("konbini", "niwa", "landing")}
    record("[prefs] ... Konbini, Niwa and landing show it on the next load (this browser)",
           all("palette-nord" in s["cls"] and "theme-day" in s["cls"] for s in seen.values()),
           str({k: v["cls"] for k, v in seen.items()}))

    # B: another device (a fresh browser, signed in automatically): the account's theme, and landing's first render
    b_ctx = browser.new_context(viewport={"width": 1280, "height": 900}, ignore_https_errors=True)
    b = b_ctx.new_page()
    resp = b.goto(U["landing"] + "/status")
    signin(b)
    b.wait_for_url(U["landing"] + "/**", timeout=20000)
    first = resp.text() if resp is not None and resp.url.startswith(U["landing"]) else ""
    b.wait_for_load_state("networkidle")
    first_render = 'palette-nord' in first.split("<body", 1)[-1][:300] if first else None
    seen = {room: visit(b, room) for room in ("konbini", "niwa", "kura", "landing")}
    record("[prefs] another device: Konbini, Niwa, Kura and landing show the account's theme",
           all("palette-nord" in s["cls"] and "theme-day" in s["cls"] for s in seen.values()),
           "%s; landing's very first page drawn in it (no cookies yet) %s" % (
               {k: v["cls"] for k, v in seen.items()}, first_render))

    # Use This Device's Size on A: local only (no PUT); B keeps the account's size; the Shared Text Size still syncs
    visit(a, "landing", "/settings")
    n = len(puts)
    a.check("[data-device-size]")
    a.select_option("[data-device-size-value]", "xlarge")
    a.wait_for_timeout(500)
    local_only = len(puts) == n
    a_text = {room: visit(a, room)["text"] for room in ("konbini", "kura")}
    b_text = visit(b, "konbini")["text"]
    visit(a, "landing", "/settings")
    with a.expect_response(lambda r: r.request.method == "PUT" and "/api/prefs" in r.url):
        a.select_option('select[data-set="textSize"]', "large")
    a_after = visit(a, "niwa")["text"]
    b_after = visit(b, "niwa")["text"]
    record("[prefs] Use This Device's Size stays on its device: A shows xlarge everywhere, sends nothing; B keeps the "
           "account's size; a Shared Text Size change still reaches B but not A's screen",
           local_only and set(a_text.values()) == {"xlarge"} and b_text == "standard" and a_after == "xlarge"
           and b_after == "large" and account(tok).get("text_size") == "large",
           "no PUT %s; A %s; B %s; after Shared=large: A %s, B %s" % (local_only, a_text, b_text, a_after, b_after))

    # the helper down: the rooms keep working on their cookies (Niwa, Konbini and landing through the Tailscale
    # fallback, as Serve's header would let them; Kura, with no fallback, says sign-in is unavailable)
    if engine_cmd == "native":
        print("SKIP  [prefs] the helper down (a native stack: stop hister-login by hand)")
        browser.close()
        return
    login = (denv.get("DEV_TAILNET_USERS") or "owner@dev").split(",")[0].strip()
    a_ctx.set_extra_http_headers({"Tailscale-User-Login": login})
    subprocess.run([engine_cmd, "stop", "-t", "2", helper], capture_output=True)
    try:
        time.sleep(32)                                 # the rooms' cached "signed in" (30 s) runs out
        down = {room: visit(a, room) for room in ("konbini", "niwa", "landing")}
        banner = {room: a.locator(".machiya-banner").count() for room in ("landing",)}
        kura = a.goto(U["kura"] + "/")
        kura_status = kura.status if kura is not None else 0
        visit(a, "landing", "/settings")
        state = a.get_attribute(".prefs-state", "data-prefs-state")
        a.screenshot(path=os.path.join(shots, "shared-section-unavailable-desktop-light.png"), full_page=True)
        a.select_option('select[data-set="palette"]', "dracula")
        a.wait_for_timeout(800)
        pending = a.evaluate("() => localStorage.getItem('machiyaPrefsPending')")
        cookie_now = a.evaluate("() => document.body.className")
        record("[prefs] the helper down: Konbini, Niwa and landing keep working with their cookies (theme kept); the "
               "Shared line says sign-in is unavailable; a change applies here and waits",
               all(s["cls"] and "palette-nord" in s["cls"] for s in down.values()) and state == "unavailable"
               and "palette-dracula" in cookie_now and "dracula" in (pending or ""),
               "rooms %s; landing banner %s; Kura %s (no fallback there); state line %s; pending %s" % (
                   {k: v["cls"] for k, v in down.items()}, banner, kura_status, state, pending))
    finally:
        subprocess.run([engine_cmd, "start", helper], capture_output=True)
    deadline = time.time() + 90
    while time.time() < deadline:
        st, body, _ = http("GET", L(19204, "/machiya/healthz"), timeout=5)
        if st == 200 and isinstance(body, dict) and body.get("hister") == "ok":
            break
        time.sleep(2)
    time.sleep(12)                                     # the rooms' health flag (10 s)
    a_ctx.set_extra_http_headers({})
    visit(a, "landing")
    a.wait_for_timeout(1500)
    acct = account(tok)
    b_back = visit(b, "konbini")["cls"]
    record("[prefs] the helper back: the waiting change lands in the account (sent first), and the other device "
           "follows on its next load", acct.get("palette") == "dracula" and "palette-dracula" in b_back,
           "account %s; B %s" % (acct, b_back))
    http("PUT", L(19204, "/machiya/api/prefs"), {"prefs": clear}, headers={"X-Access-Token": tok})
    a_ctx.close()
    b_ctx.close()
    browser.close()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", required=True)
    ap.add_argument("--browser", action="store_true")
    ap.add_argument("--shots")
    a = ap.parse_args()
    sec = os.path.join(a.data, "secrets")
    with open(os.path.join(sec, "owner-token")) as f:
        tok = f.readline().strip()
    with open(os.path.join(sec, "owner-password")) as f:
        password = f.readline().strip()
    rtok = ""
    if os.path.exists(os.path.join(sec, "room-token")):
        with open(os.path.join(sec, "room-token")) as f:
            rtok = f.readline().strip()
    base = os.environ.get("DEV_URL", "http://localhost").rstrip("/")
    sso_cookie = "machiya_sso"
    denv = {}
    try:
        with open(os.path.join(a.data, "dev.env")) as f:
            for line in f:
                if "=" in line and not line.startswith("#"):
                    k, _, v = line.strip().partition("=")
                    denv[k] = v
        if denv.get("DEV_ENGINE") == "native":
            OPTIONAL.add("searxng")
        sso_cookie = denv.get("DEV_SSO_COOKIE") or sso_cookie
    except OSError:
        pass
    http_checks(a.data, tok, password, rtok, denv)
    if a.browser:
        browser_checks(a.data, password, a.shots or os.path.join(a.data, "shots"), base, sso_cookie, denv, tok)
    failed = sum(1 for _, ok in RESULTS if not ok)
    print("\n%d check(s), %d failed" % (len(RESULTS), failed))
    return failed


if __name__ == "__main__":
    sys.exit(main())
