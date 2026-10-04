"""The dev stack's checks (run by `./dev check [--browser]`): every service answers, the sign-in gates hold, the
synthetic seed is where it should be, the MCP and feed-import work. HTTP checks need only the standard library;
--browser adds Playwright (Chromium and WebKit) for the sign-in and the rooms, with screenshots.

    python3 checks.py --data DIR [--browser --shots DIR] [--only NAME,...]

Talks to 127.0.0.1:<port> for the HTTP checks and to DEV_URL (the public base the stack was made with) in the
browser. The dummy owner's password and token are read from DIR/secrets and never printed. Each check prints
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
    return "%s://127.0.0.1:%d%s" % (FRONT if 19200 <= port <= 19208 else "http", port, path)


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

def http_checks(data, tok, password):
    bearer = {"Authorization": "Bearer " + tok}
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
    record("Kura: the owner's Hister token works; search 'bamboo' finds notes", st == 200 and "Bamboo frames" in titles,
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
    record("Niwa: the garden is published (9 notes) and its home page loads with the token",
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
    leaks = scan_logs(data, [tok, password] + forge_tokens)
    record("no secret in any service log (the owner's password and token, the fake forges' tokens)", not leaks,
           ", ".join(leaks))


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
        names = subprocess.run([engine, "ps", "-a", "--format", "{{.Names}}"], capture_output=True, text=True).stdout
        for n in names.split():
            if n.startswith("machiya-dev-"):
                r = subprocess.run([engine, "logs", n], capture_output=True, text=True, errors="replace")
                texts[n] = r.stdout + r.stderr
    return [n for n, t in texts.items() if any(x and x in t for x in needles)]


# -- browser ---------------------------------------------------------------------------------------------------------

def browser_checks(data, password, shots, base):
    from playwright.sync_api import sync_playwright
    os.makedirs(shots, exist_ok=True)
    U = {n: "%s:%d" % (base, p) for n, p in (("landing", 19200), ("kura", 19201), ("niwa", 19202),
                                              ("konbini", 19203), ("hister", 19204))}

    def shot(page, name, engine):
        for scheme in ("light", "dark"):
            page.emulate_media(color_scheme=scheme)
            page.wait_for_timeout(250)
            page.screenshot(path=os.path.join(shots, "%s-%s-%s.png" % (engine, name, scheme)), full_page=False)

    def signin(page):
        page.wait_for_selector("#hister-signin", timeout=15000)
        page.fill("input[name=username]", "owner")
        page.fill("input[name=password]", password)
        page.click("#hister-signin button[type=submit]")

    with sync_playwright() as pw:
        for engine in ("chromium", "webkit"):
            browser = getattr(pw, engine).launch()
            for vw, label in (({"width": 1280, "height": 860}, ""), ({"width": 390, "height": 844}, "phone-")):
                if label and engine == "webkit":
                    continue                          # phone shots once (Chromium); WebKit runs the flows at desktop
                ctx = browser.new_context(viewport=vw, ignore_https_errors=True)
                p = ctx.new_page()
                p.goto(U["kura"] + "/n/Notes/Bamboo%20frames")
                at_signin = "/machiya/signin" in p.url
                if label == "":
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
                    record("[%s] sign in once at the helper, then Kura, Niwa, Konbini, landing and Hister with no prompt"
                           % engine, at_signin and back.startswith(U["kura"]) and ok_kura
                           and all(a and b for a, b in rooms.values()) and hister_in,
                           "kura -> sign-in %s, back at %s; rooms (no prompt, content) %s; Hister UI signed in %s"
                           % (at_signin, back, rooms, hister_in))
                    record("[%s] Kura's search page finds 'chochin'" % engine, found, "")
                    # sign out everywhere from the helper's sessions page; the rooms send you back to sign in
                    p.goto(U["hister"] + "/machiya/sessions")
                    p.click("button[value=all]")
                    p.wait_for_load_state("networkidle")
                    deadline, out = time.time() + 45, False
                    while time.time() < deadline and not out:
                        p.goto(U["konbini"] + "/")
                        out = "/machiya/signin" in p.url
                        if not out:
                            p.wait_for_timeout(3000)
                    record("[%s] sign out everywhere: Konbini asks to sign in again (cache <= 30 s)" % engine, out,
                           p.url)
                ctx.close()
            # the stub OIDC provider (tsidp's shape), bound to the owner
            ctx = browser.new_context(ignore_https_errors=True)
            p = ctx.new_page()
            p.goto(U["konbini"] + "/")
            p.click("text=Sign in with Tailscale")
            p.wait_for_url(U["konbini"] + "/**", timeout=20000)
            p.wait_for_load_state("networkidle")
            st = ctx.request.get(U["konbini"] + "/api/cards").status
            record("[%s] 'Sign in with Tailscale (dev stub)': the stub OIDC login is the owner" % engine,
                   p.url.startswith(U["konbini"]) and st == 200, "landed on %s; /api/cards %s" % (p.url, st))
            ctx.close()
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
    base = os.environ.get("DEV_URL", "http://localhost").rstrip("/")
    try:
        with open(os.path.join(a.data, "dev.env")) as f:
            if "DEV_ENGINE=native" in f.read():
                OPTIONAL.add("searxng")
    except OSError:
        pass
    http_checks(a.data, tok, password)
    if a.browser:
        browser_checks(a.data, password, a.shots or os.path.join(a.data, "shots"), base)
    failed = sum(1 for _, ok in RESULTS if not ok)
    print("\n%d check(s), %d failed" % (len(RESULTS), failed))
    return failed


if __name__ == "__main__":
    sys.exit(main())
