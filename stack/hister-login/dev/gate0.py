"""Gate 0 of the Hister sign-in, in Playwright, against the dev stack (compose.yml) on 127.0.0.1:19043.

    DEV_DATA=/var/tmp/machiya-login/dev-data SHOTS=/var/tmp/machiya-login/shots python gate0.py

Both engines reach *.machiya.test through a small CONNECT proxy to 127.0.0.1:19043 (Chromium's
--host-resolver-rules would not cover Playwright's API request context). TLS is the throwaway CA's (ignore_https_errors). Containers are stopped and started with podman
(DOCKER_HOST for compose). Each check prints PASS/FAIL with its evidence; the exit code is the number of failures.
Dev only: dummy data, a dummy owner whose password is read from $DEV_DATA/secrets/owner-password and never printed."""
import json
import os
import socket
import subprocess
import sys
import threading
import time
from urllib.parse import parse_qs, quote, urlsplit

from playwright.sync_api import sync_playwright

PORT = 19043
B = "https://%s.machiya.test:" + str(PORT)
HISTER, KURA, NIWA, KONBINI, SHIORI = (B % h for h in ("hister", "kura", "niwa", "konbini", "shiori"))
DEV_DATA = os.environ.get("DEV_DATA", "/var/tmp/machiya-login/dev-data")
SHOTS = os.environ.get("SHOTS", "/var/tmp/machiya-login/shots")
HERE = os.path.dirname(os.path.abspath(__file__))
PASSWORD = open(os.path.join(DEV_DATA, "secrets", "owner-password")).read().strip()
TS = {"Tailscale-User-Login": "owner@passkey"}      # what Tailscale Serve would send for the owner's devices
ONLY = set(sys.argv[1:])
RESULTS = []


def record(name, ok, evidence):
    RESULTS.append((name, ok, evidence))
    print("%s  %s\n      %s" % ("PASS" if ok else "FAIL", name, evidence.replace("\n", "\n      ")), flush=True)


def podman(*args):
    return subprocess.run(["podman", *args], capture_output=True, text=True, timeout=120)


def compose(*args, **env):
    e = dict(os.environ, DEV_DATA=DEV_DATA, DOCKER_HOST=os.environ.get(
        "DOCKER_HOST", "unix:///var/tmp/machiya-login/podman.sock"), **env)
    return subprocess.run(["podman", "compose", *args], capture_output=True, text=True, timeout=180, cwd=HERE, env=e)


def wait_http(url, want=200, timeout=40, ctx=None):
    """Until `url` answers `want` (and, for the helper's /machiya/healthz, reports Hister "ok" too: that probe stays
    200 while Hister is down)."""
    t = time.time()
    while time.time() - t < timeout:
        try:
            r = ctx.get(url)
            if r.status == want and (not url.endswith("/machiya/healthz") or r.json().get("hister") == "ok"):
                return True
        except Exception:
            pass
        time.sleep(1)
    return False


# -- a CONNECT proxy for WebKit (it has no host-resolver rules) -------------------------------------------------------

def proxy(port=PORT + 1):
    srv = socket.socket()
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind(("127.0.0.1", port))
    srv.listen(64)

    def pipe(a, b):
        try:
            while True:
                data = a.recv(65536)
                if not data:
                    break
                b.sendall(data)
        except OSError:
            pass
        finally:
            for s in (a, b):
                try:
                    s.shutdown(socket.SHUT_RDWR)
                except OSError:
                    pass

    def handle(c):
        head = b""
        while b"\r\n\r\n" not in head:
            chunk = c.recv(4096)
            if not chunk:
                return c.close()
            head += chunk
        line = head.split(b"\r\n")[0].decode()
        method, target, _ = line.split(" ", 2)
        host = target.split(":")[0]
        if method != "CONNECT" or not host.endswith(".machiya.test"):
            c.sendall(b"HTTP/1.1 403 Forbidden\r\nContent-Length: 0\r\n\r\n")
            return c.close()
        up = socket.create_connection(("127.0.0.1", PORT))
        c.sendall(b"HTTP/1.1 200 Connection Established\r\n\r\n")
        threading.Thread(target=pipe, args=(c, up), daemon=True).start()
        threading.Thread(target=pipe, args=(up, c), daemon=True).start()

    def loop():
        while True:
            c, _ = srv.accept()
            threading.Thread(target=handle, args=(c,), daemon=True).start()
    threading.Thread(target=loop, daemon=True).start()
    return "http://127.0.0.1:%d" % port


# -- helpers -----------------------------------------------------------------------------------------------------------

def context(browser, **kw):
    kw.setdefault("ignore_https_errors", True)
    return browser.new_context(**kw)


def password_signin(page):
    page.wait_for_selector("#hister-signin")
    page.fill("input[name=username]", "owner")
    page.fill("input[name=password]", PASSWORD)
    page.click("#hister-signin button[type=submit]")


def who(page):
    el = page.query_selector("[data-who]")
    return el.get_attribute("data-who") if el else None


def cookie(ctx, name, domain=None):
    for c in ctx.cookies():
        if c["name"] == name and (domain is None or c["domain"] == domain):
            return c
    return None


def api(ctx, url, headers=None):
    r = ctx.request.get(url, headers=headers or {}, max_redirects=0)
    try:
        body = r.json()
    except Exception:
        body = r.text()[:120]
    return r.status, body


def poll_until(fn, want, limit):
    """Call fn() every second until it returns `want`; -> seconds taken, or None after `limit`."""
    t = time.time()
    while time.time() - t <= limit:
        if fn() == want:
            return round(time.time() - t, 1)
        time.sleep(1)
    return None


def shot(page, name):
    os.makedirs(SHOTS, exist_ok=True)
    path = os.path.join(SHOTS, name + ".png")
    page.screenshot(path=path, full_page=True)
    return path


def screenshots(browser, name, url, headers=None, before=None):
    """phone 390 and desktop 1280, light and dark."""
    out = []
    for label, vp in (("phone", {"width": 390, "height": 844}), ("desktop", {"width": 1280, "height": 800})):
        for scheme in ("light", "dark"):
            ctx = context(browser, viewport=vp, color_scheme=scheme, extra_http_headers=headers or {},
                          device_scale_factor=2 if label == "phone" else 1, is_mobile=label == "phone",
                          has_touch=label == "phone")
            if before:
                before(ctx)
            p = ctx.new_page()
            p.goto(url)
            p.wait_for_load_state("networkidle")
            out.append(shot(p, "%s-%s-%s" % (name, label, scheme)))
            ctx.close()
    return out


def signed_in_context(browser, **kw):
    ctx = context(browser, **kw)
    p = ctx.new_page()
    p.goto(KURA + "/")
    password_signin(p)
    p.wait_for_url(KURA + "/")
    return ctx, p


# -- the checks ------------------------------------------------------------------------------------------------------

def c1_one_signin(browser, engine):
    ctx = context(browser)
    p = ctx.new_page()
    hops = []
    p.on("response", lambda r: hops.append((r.status, r.url)) if r.request.is_navigation_request() else None)
    p.goto(KURA + "/n/Some%20Note?x=1")
    at_signin = p.url.startswith(HISTER + "/machiya/signin?return=")
    password_signin(p)
    p.wait_for_url(KURA + "/**")
    back = p.url
    first = who(p)
    seen = {}
    for name, url in (("niwa", NIWA + "/"), ("konbini", KONBINI + "/")):
        p.goto(url)
        seen[name] = (p.url == url, who(p), p.query_selector(".machiya-banner") is None)
    status_h, body_h = api(ctx, HISTER + "/api/profile")
    status_s, body_s = api(ctx, SHIORI + "/api/profile")              # hosted pages: nginx auth_request
    p.goto(SHIORI + "/")
    shiori_page = p.url == SHIORI + "/" and "machiya/signin" not in p.url
    status_sk, body_sk = api(ctx, SHIORI + "/kura/api/whoami")          # nginx forwards machiya_sso only
    sso = cookie(ctx, "machiya_sso")
    hister_c = cookie(ctx, "hister")
    hister_hosts = sorted(c["domain"] for c in ctx.cookies() if c["name"] == "hister")
    ok = (hister_hosts == ["hister.machiya.test"] and at_signin and back == KURA + "/n/Some%20Note?x=1" and first == "owner"
          and all(v == (True, "owner", True) for v in seen.values())
          and status_h == 200 and body_h.get("username") == "owner" and status_s == 200
          and body_s.get("username") == "owner" and shiori_page and status_sk == 200
          and sso and sso["domain"] == ".machiya.test" and sso["secure"] and sso["httpOnly"]
          and hister_c and hister_c["domain"] == "hister.machiya.test")
    record("[%s] one sign-in, then every room and the hosted pages with no prompt" % engine, ok,
           "kura -> helper sign-in: %s; back at %s as %s; niwa/konbini (same url, who, no banner): %s\n"
           "hister /api/profile %s %s; shiori (auth_request) /api/profile %s %s; shiori / loads without sign-in: %s; "
           "shiori /kura/api/whoami %s\nmachiya_sso domain=%s secure=%s httpOnly=%s sameSite=%s; hister cookie domain=%s"
           % (at_signin, back, first, seen, status_h, body_h.get("username"), status_s, body_s.get("username"),
              shiori_page, status_sk, sso and sso["domain"], sso and sso["secure"], sso and sso["httpOnly"],
              sso and sso["sameSite"], hister_hosts))
    return ctx, p


def c2_room_signout(browser, engine):
    ctx, p = signed_in_context(browser)
    for url in (NIWA + "/api/whoami", KURA + "/api/whoami", KONBINI + "/api/whoami"):
        api(ctx, url)                                                   # every room has the id cached as good
    old_sso = cookie(ctx, "machiya_sso")["value"]
    old_hister = cookie(ctx, "hister")["value"]
    p.goto(KONBINI + "/")
    menu_row = p.query_selector(".rooms .menu form.signout") is not None   # machiya.js's opt-in Sign Out row
    p.click("main form[action='/signout'] button")
    p.wait_for_url(KONBINI + "/signed-out")
    t0 = time.time()
    gone = cookie(ctx, "machiya_sso") is None
    st = {u: api(ctx, u)[0] for u in (KURA + "/api/whoami", NIWA + "/api/whoami")}
    # another tab or device that still sends the old id: every room refuses it within the cache time
    other = context(browser)
    other.add_cookies([{"name": "machiya_sso", "value": old_sso, "domain": ".machiya.test", "path": "/",
                        "secure": True, "httpOnly": True, "sameSite": "Lax"}])
    times = {}
    for name, url in (("kura", KURA), ("niwa", NIWA), ("konbini", KONBINI)):
        times[name] = poll_until(lambda: api(other, url + "/api/whoami")[0], 401, 35)
    hister_after = api(context(browser, extra_http_headers={"Cookie": "hister=" + old_hister}),
                       HISTER + "/api/profile")[0]
    worst = max(t for t in times.values() if t is not None) if all(t is not None for t in times.values()) else None
    ok = gone and all(s == 401 for s in st.values()) and worst is not None and worst <= 30 + 2 \
        and hister_after == 403 and menu_row
    record("[%s] sign-out in one room, then 401 everywhere within 30 s" % engine, ok,
           "signed out in Konbini; this browser's machiya_sso cleared: %s; Kura/Niwa API now %s\n"
           "the old id from another tab: 401 after %s s (time from the sign-out: up to %.0f s); Hister's own session "
           "after: %s; machiya.js Sign Out row in the Rooms menu: %s"
           % (gone, st, times, time.time() - t0, hister_after, menu_row))
    other.close()
    ctx.close()


def c3_hister_ui_signout(browser, engine):
    ctx, p = signed_in_context(browser)
    for url in (NIWA, KURA, KONBINI):
        api(ctx, url + "/api/whoami")
    p.goto(HISTER + "/")
    p.wait_for_load_state("networkidle")
    # what Hister's own UI does on "Log out": a same-origin POST /api/logout from the hister.* page
    status = p.evaluate("fetch('/api/logout', {method: 'POST', credentials: 'same-origin'}).then(r => r.status)")
    t0 = time.time()
    times = {}
    for name, url in (("kura", KURA), ("niwa", NIWA), ("konbini", KONBINI)):
        left = 65 - (time.time() - t0)
        took = poll_until(lambda: api(ctx, url + "/api/whoami")[0], 401, max(left, 1))
        times[name] = None if took is None else round(time.time() - t0, 1)
    ok = status == 200 and all(t is not None and t <= 60 + 2 for t in times.values())
    record("[%s] a sign-out in Hister's own UI is noticed within 60 s" % engine, ok,
           "Hister POST /api/logout from its page: %s; rooms refused the id after (s from the logout): %s"
           % (status, times))
    ctx.close()


def c4_open_redirect(browser, engine):
    ctx, p = signed_in_context(browser)
    out = {}
    for bad in ("https://evil.example/", "https://searxng.machiya.test:%d/" % PORT, "//evil.example/",
                "http://kura.machiya.test:%d/" % PORT, "https://kura.machiya.test:%d@evil.example/" % PORT,
                "javascript:alert(1)", "https://kura.machiya.test:%d.evil.example/" % PORT,
                "https://kura.machiya.test/"):
        r = ctx.request.get(HISTER + "/machiya/signin?return=" + quote(bad, safe=""), max_redirects=0)
        out[bad] = r.headers.get("location")
    good = ctx.request.get(HISTER + "/machiya/signin?return=" + quote(NIWA + "/x", safe=""), max_redirects=0)
    ok = all(v == HISTER + "/" for v in out.values()) and good.headers.get("location") == NIWA + "/x"
    record("[%s] an open redirect is refused" % engine, ok,
           "refused returns all went to Hister's own /: %s\nan allowed room: %s -> %s"
           % (json.dumps(out), NIWA + "/x", good.headers.get("location")))
    ctx.close()


def c5_loop_guard(browser, engine):
    """A machiya_sso that never reaches the room (here: the helper misconfigured with no cookie domain, so the cookie
    stays on hister.*): one trip to the helper, then a page with a Sign In link, not a redirect loop."""
    compose("up", "-d", "hister-login", HELPER_COOKIE_DOMAIN="")
    time.sleep(4)
    try:
        ctx = context(browser)
        p = ctx.new_page()
        p.goto(HISTER + "/machiya/signin")                              # signed in at Hister itself
        password_signin(p)
        p.wait_for_url(HISTER + "/")
        hops = []
        p.on("response", lambda r: hops.append("%d %s" % (r.status, urlsplit(r.url).netloc.split(".")[0]))
             if r.request.is_navigation_request() and r.frame == p.main_frame else None)
        p.goto(NIWA + "/")
        p.wait_for_load_state("networkidle")
        to_helper = sum(1 for h in hops if h.endswith("hister"))
        link = p.query_selector("main a.button.primary")
        href = link.get_attribute("href") if link else ""
        guard = cookie(ctx, "machiya_sso_try")
        sso = cookie(ctx, "machiya_sso")
        ok = to_helper == 1 and p.url == NIWA + "/" and href.startswith(HISTER + "/machiya/signin?return=") \
            and guard is not None and guard["domain"] == "niwa.machiya.test"
        record("[%s] the loop guard: one trip to the helper, then a page with a link" % engine, ok,
               "navigations: %s; ended on %s; the page's Sign In link: %s; guard cookie host-only on %s; "
               "machiya_sso landed on %s" % (hops, p.url, href[:70], guard and guard["domain"], sso and sso["domain"]))
        ctx.close()
    finally:
        compose("up", "-d", "hister-login")
        time.sleep(4)


def c6_outages(browser, engine):
    ctx, p = signed_in_context(browser, extra_http_headers=TS)
    fresh = context(browser, extra_http_headers=TS)                    # a device that never signed in
    out = {}
    for what, container in (("helper", "machiya-login-hister-login-1"), ("hister", "machiya-login-hister-1")):
        podman("stop", "-t", "2", container)
        time.sleep(31)                                                  # past every cached "signed in" (30 s)
        row = {}
        for label, c in (("signed-in", ctx), ("never-signed-in", fresh)):
            for room, url in (("kura", KURA), ("niwa", NIWA), ("konbini", KONBINI)):
                pg = c.new_page()
                r = pg.goto(url + "/")
                banner = pg.query_selector(".machiya-banner")
                row["%s %s" % (label, room)] = (r.status, "banner" if banner else "", who(pg))
                pg.close()
            row["%s kura-api" % label] = api(c, KURA + "/api/whoami")[0]
        out[what] = row
        podman("start", container)
        wait_http(HISTER + "/machiya/healthz", 200, 60, ctx.request)
        time.sleep(11)                                                  # the rooms' health flag
    ok = True
    for what, row in out.items():
        for label in ("signed-in", "never-signed-in"):
            ok &= row["%s kura" % label][0] == 503 and row["%s kura-api" % label] == 503
            for room in ("niwa", "konbini"):
                ok &= row["%s %s" % (label, room)] == (200, "banner", "owner@passkey")
    status = {u: api(fresh, u + "/api/status")[1].get("fallback_total") for u in (NIWA, KONBINI)}
    record("[%s] a helper or Hister stop: Niwa/Konbini fall back with the banner, Kura answers 503" % engine, ok,
           "\n".join("%s stopped: %s" % (k, json.dumps(v)) for k, v in out.items())
           + "\nfallback_total: %s" % status)
    ctx.close()
    fresh.close()


def c7_401_never_falls_back(browser, engine):
    ctx, p = signed_in_context(browser, extra_http_headers=TS)
    old = cookie(ctx, "machiya_sso")["value"]
    p.goto(NIWA + "/")
    p.click("main form[action='/signout'] button")
    p.wait_for_url(NIWA + "/signed-out")
    other = context(browser, extra_http_headers=TS)
    other.add_cookies([{"name": "machiya_sso", "value": old, "domain": ".machiya.test", "path": "/", "secure": True,
                        "httpOnly": True, "sameSite": "Lax"}])
    res = {}
    for room, url in (("niwa", NIWA), ("konbini", KONBINI)):
        poll_until(lambda: api(other, url + "/api/whoami")[0], 401, 35)
        r = other.request.get(url + "/", max_redirects=0)
        res[room] = (r.status, (r.headers.get("location") or "")[:60], api(other, url + "/api/whoami")[0])
    nocred = context(browser, extra_http_headers=TS)
    r = nocred.request.get(NIWA + "/", max_redirects=0)
    ok = all(v[0] == 302 and v[1].startswith(HISTER + "/machiya/signin") and v[2] == 401 for v in res.values()) \
        and r.status == 302
    record("[%s] a 401 never falls back (Tailscale identity present)" % engine, ok,
           "signed-out id + Tailscale-User-Login: %s\nno credential + Tailscale-User-Login, sign-in up: %s -> %s"
           % (res, r.status, (r.headers.get("location") or "")[:60]))
    for c in (ctx, other, nocred):
        c.close()


def c8_user_handling_off(browser, engine):
    ctx, p = signed_in_context(browser)
    tok = open(os.path.join(DEV_DATA, "secrets", "owner-token")).read().strip()
    compose("up", "-d", "hister", HISTER_USER_HANDLING="false")
    time.sleep(6)
    time.sleep(31)
    res = {}
    for room, url in (("kura", KURA), ("niwa", NIWA), ("konbini", KONBINI)):
        res[room] = (api(ctx, url + "/api/whoami")[0], api(ctx, url + "/api/whoami", {"X-Access-Token": tok})[0])
    fresh = context(browser)
    pg = fresh.new_page()
    r = pg.goto(HISTER + "/machiya/signin?return=" + quote(KURA + "/", safe=""))
    signin_text = pg.inner_text("main")[:90]
    health = api(fresh, HISTER + "/machiya/healthz")             # the probe's: 200, Hister's state inside
    with_ts = api(context(browser, extra_http_headers=TS), NIWA + "/api/whoami")
    compose("up", "-d", "hister", HISTER_USER_HANDLING="true")
    wait_http(HISTER + "/machiya/healthz", 200, 60, fresh.request)
    ok = all(v == (503, 503) for v in res.values()) and r.status == 503 and health[0] == 200 \
        and health[1].get("hister") == "user-handling-off" and with_ts[0] == 200 and with_ts[1].get("banner")
    record("[%s] with user_handling off nobody is admitted through Hister" % engine, ok,
           "rooms (session, owner token) without a Tailscale identity: %s\nhelper sign-in page: %s %r\n"
           "healthz: %s\nNiwa with the owner's Tailscale identity: %s (the tailnet fallback, by design)"
           % (res, r.status, signin_text, health, with_ts))
    ctx.close()
    fresh.close()


def c9_app_flow(browser, engine):
    ctx = context(browser)
    # password: the app's own Hister session, traded for an id
    r = ctx.request.post(HISTER + "/api/login", headers={"Origin": "hister://", "Content-Type": "application/json"},
                         data=json.dumps({"username": "owner", "password": PASSWORD}))
    s = next((c["value"] for c in ctx.cookies() if c["name"] == "hister"), None)
    ctx.clear_cookies()
    r2 = ctx.request.post(HISTER + "/machiya/api/app-session", headers={"Content-Type": "application/json"},
                          data=json.dumps({"hister": s, "label": "Dev iPhone"}))
    sid = r2.json().get("sid") if r2.status == 200 else None
    bearer = {"Authorization": "Bearer %s" % sid}
    who_app = api(ctx, KURA + "/api/whoami", bearer)
    out = ctx.request.post(HISTER + "/machiya/signout", headers=bearer)
    after = poll_until(lambda: api(ctx, KURA + "/api/whoami", bearer)[0], 401, 35)
    hister_after = api(context(browser, extra_http_headers={"Cookie": "hister=" + s}), HISTER + "/api/profile")[0]
    # tsidp in the app: an ephemeral browser session goes to the helper with app=1 and return=shiori://
    eph = context(browser)
    pg = eph.new_page()
    seen = {}
    pg.on("response", lambda resp: seen.setdefault("loc", resp.headers.get("location"))
          if "/api/oauth/callback" in resp.url else None)
    pg.goto(HISTER + "/machiya/signin?app=1&return=" + quote("shiori://signed-in", safe=""))
    try:
        pg.click("text=Sign in with Tailscale")
        pg.wait_for_timeout(2500)
    except Exception:
        pass
    loc = seen.get("loc") or ""
    frag = parse_qs(urlsplit(loc).fragment) if loc else {}
    tsid = (frag.get("sid") or [""])[0]
    ts_who = api(ctx, KURA + "/api/whoami", {"Authorization": "Bearer " + tsid}) if tsid else (None, None)
    ok = r.status == 200 and sid and who_app[0] == 200 and who_app[1].get("actor") == "app:owner" \
        and out.status == 204 and after is not None and hister_after == 403 \
        and loc.startswith("shiori://signed-in#sid=mhs_") and ts_who[0] == 200
    record("[%s] the app flow (password + app-session, tsidp with return=shiori://)" % engine, ok,
           "password: /api/login %s, app-session %s, Kura as %s; sign-out (Bearer) %s; Kura 401 after %s s; "
           "the app's Hister session after: %s\ntsidp app=1: callback Location %s…; that id at Kura: %s %s"
           % (r.status, r2.status, who_app, out.status, after, hister_after, loc[:40], ts_who[0],
              (ts_who[1] or {}).get("actor") if isinstance(ts_who[1], dict) else ts_who[1]))
    ctx.close()
    eph.close()


def c10_tsidp(browser, engine, phase):
    """tsidp through the browser from a room. Before the bind (§7) the OIDC login is its own new Hister account, which
    the rooms refuse (403, no fallback); after it, the owner."""
    ctx = context(browser, extra_http_headers=TS)
    p = ctx.new_page()
    p.goto(KONBINI + "/x?y=1")
    p.click("text=Sign in with Tailscale")
    p.wait_for_load_state("networkidle")
    status = None
    r = ctx.request.get(KONBINI + "/api/whoami")
    status = r.status
    prof = api(ctx, HISTER + "/api/profile")
    if phase == "before":
        ok = p.url.startswith(KONBINI + "/x?y=1") and status == 403 and prof[1].get("username") != "owner"
        record("[%s] tsidp before the bind: a new Hister account, refused by the rooms (403, no fallback)" % engine,
               ok, "landed on %s; Konbini API %s; Hister says %s" % (p.url, status, prof))
    else:
        ok = p.url == KONBINI + "/x?y=1" and status == 200 and who(p) == "owner" and prof[1].get("username") == "owner"
        record("[%s] tsidp after the bind: the owner, back where the sign-in started (callback shim)" % engine, ok,
               "landed on %s as %s; Konbini API %s; Hister says %s" % (p.url, who(p), status, prof))
    ctx.close()


def bind_oidc():
    compose("stop", "hister")
    email = "owner@passkey.idp.machiya.test"
    r = podman("run", "--rm", "-v", "machiya-login_hister-data:/hister/data", "-v", HERE + "/move.py:/move.py:ro",
               "public.ecr.aws/docker/library/python:3.13-slim", "python3", "/move.py", "bind", "owner",
               "oidc-" + email)
    compose("up", "-d", "hister", HISTER_USER_HANDLING="true")
    time.sleep(6)
    return r.stdout.strip()


def sessions_and_shots(browser):
    files = []
    files += screenshots(browser, "signin", HISTER + "/machiya/signin?return=" + quote(KURA + "/", safe=""))

    def sign_in(ctx):
        p = ctx.new_page()
        p.goto(KURA + "/")
        password_signin(p)
        p.wait_for_url(KURA + "/")
        r = ctx.request.post(HISTER + "/api/login", headers={"Origin": "hister://",
                                                             "Content-Type": "application/json"},
                             data=json.dumps({"username": "owner", "password": PASSWORD}))
        app = [c["value"] for c in ctx.cookies() if c["name"] == "hister"]
        p.close()
    files += screenshots(browser, "sessions", HISTER + "/machiya/sessions", before=sign_in)
    return files


def outage_shots(browser):
    podman("stop", "-t", "2", "machiya-login-hister-login-1")
    time.sleep(12)
    files = screenshots(browser, "fallback-banner-niwa", NIWA + "/", headers=TS)
    files += screenshots(browser, "kura-503", KURA + "/", headers=TS)
    podman("start", "machiya-login-hister-login-1")
    time.sleep(12)
    return files


def main():
    with sync_playwright() as pw:
        server = proxy()            # both engines (and their API request contexts) reach *.machiya.test through it
        chromium = pw.chromium.launch(proxy={"server": server})
        webkit = pw.webkit.launch(proxy={"server": server})
        engines = [("chromium", chromium), ("webkit", webkit)]

        def run(key, fn, *args):
            if ONLY and key not in ONLY:
                return
            try:
                fn(*args)
            except Exception as e:
                record("%s %s" % (key, args[1:] if len(args) > 1 else ""), False, "error: %r" % e)

        for name, br in engines:
            run("c1", c1_one_signin, br, name)
            run("c2", c2_room_signout, br, name)
            run("c4", c4_open_redirect, br, name)
            run("c5", c5_loop_guard, br, name)
        run("c3", c3_hister_ui_signout, chromium, "chromium")
        run("c7", c7_401_never_falls_back, chromium, "chromium")
        run("c10", c10_tsidp, chromium, "chromium", "before")
        if not ONLY or "c10" in ONLY:
            print("bind:", bind_oidc(), flush=True)
        run("c10", c10_tsidp, chromium, "chromium", "after")
        run("c10", c10_tsidp, webkit, "webkit", "after")
        run("c9", c9_app_flow, chromium, "chromium")
        run("c6", c6_outages, chromium, "chromium")
        run("c8", c8_user_handling_off, chromium, "chromium")
        if not ONLY or "shots" in ONLY:
            files = sessions_and_shots(chromium) + outage_shots(chromium)
            record("screenshots", len(files) == 16, "\n".join(files))
        chromium.close()
        webkit.close()
    failed = [r for r in RESULTS if not r[1]]
    print("\n%d checks, %d failed" % (len(RESULTS), len(failed)))
    with open(os.path.join(os.path.dirname(SHOTS.rstrip("/")), "gate0-results.json"), "w") as f:
        json.dump([{"check": n, "pass": ok, "evidence": ev} for n, ok, ev in RESULTS], f, indent=1)
    return len(failed)


if __name__ == "__main__":
    sys.exit(main())
