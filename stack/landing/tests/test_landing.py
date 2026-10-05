"""machiya-landing tests: the readers against fake apps on local ports (no network beyond 127.0.0.1), freshness,
the deploy history and changelogs, and the HTTP gate. Run from stack/landing: python3 -m unittest discover -s tests"""
import json
import re
import os
import sys
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, ".."))
import deploys    # noqa: E402
import landing    # noqa: E402
import probes     # noqa: E402
import render     # noqa: E402
from vaultkit import verify   # noqa: E402

NOW = 1767225600


class Fake:
    """An app on a local port. routes: {"/path": JSON object | (status, body, ctype) | callable() -> either}."""

    def __init__(self, routes):
        self.routes, self.seen = routes, []
        fake = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_POST(self):
                self.do_GET("POST")

            def do_PUT(self):
                self.do_GET("PUT")

            def do_GET(self, method="GET"):
                u = urlsplit(self.path)
                raw = self.rfile.read(int(self.headers.get("Content-Length") or 0)) if method in ("POST", "PUT") else b""
                fake.seen.append({"method": method, "path": u.path, "query": u.query, "body": raw,
                                  "headers": {k.lower(): v for k, v in self.headers.items()}})
                r = fake.routes.get(u.path if method == "GET" else method + " " + u.path)
                r = r() if callable(r) else r
                if r is None:
                    r = (404, b"not found", "text/plain")
                if isinstance(r, dict):
                    r = (200, json.dumps(r).encode(), "application/json")
                code, body, ctype, extra = (r + ({},))[:4]
                body = body.encode() if isinstance(body, str) else body
                self.send_response(code)
                self.send_header("Content-Type", ctype)
                for k, v in extra.items():
                    self.send_header(k, v)
                self.send_header("Content-Length", str(len(body)))
                if code in (301, 302) and "Location" not in extra:
                    self.send_header("Location", "http://127.0.0.1:9/elsewhere")
                self.end_headers()
                try:
                    self.wfile.write(body)
                except (BrokenPipeError, ConnectionResetError):
                    pass                        # the client gave up (the timeout test)

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.url = "http://127.0.0.1:%d" % self.server.server_address[1]
        threading.Thread(target=self.server.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True).start()

    def close(self):
        self.server.shutdown()
        self.server.server_close()


KURA = {"head": "4ca69a1865fc8e95518139d059e79208b701635a", "synced_at": NOW - 30, "notes": 314, "ready": True,
        "version": "0.6.8", "vaultkit": "v0.17.2", "error": None, "vaults": {"personal": {"error": None}},
        "push": {"pushed": 0, "failed": 0, "at": NOW - 120, "docs": 314, "complete": True, "error": None}, "auth": "tailscale"}
KONBINI = {"ok": True, "version": "0.11.6", "head": KURA["head"], "cards": 78,
           "sync": {"pending": 1, "ahead": 0, "error": ""},
           "livesync": {"status": {"daemon": "running", "errors": [], "conflicts": [], "last_cycle_ts": NOW - 60}}}
NIWA = {"version": "0.4.8", "vaultkit": "v0.17.2", "head": "a6857ce8c2", "notes": 314, "published": 1,
        "sync": {"pending": 0, "ahead": 0, "error": None}, "ready": True, "error": None}
SHIORI = (200, '<link rel="stylesheet" href="/_shiori/app.css?v=7598f33efebd">', "text/html")
HISTER = {"/health": (200, "", "text/plain"), "/api/stats": {"doc_count": 1968, "alias_count": 14},
          "/search": {"total": 1968, "documents": [{"url": "https://example.com/", "added": NOW - 9000, "updated": NOW - 600}]}}
SEARXNG = {"/healthz": (200, "OK", "text/plain"), "/config": {"version": "2026.9.25+12f8b6515"}}
MCP = {"ok": True, "version": "0.7.0", "auth": "tailscale", "rooms": ["hister", "konbini", "kura", "niwa"], "tools": 36}
SMALLWEB = {"ok": True, "ready": True, "error": None, "version": "0.2.0", "hister": {"enabled": True, "saved": 12}}


class Stack(unittest.TestCase):
    """A whole fake stack."""

    def setUp(self):
        self.fakes = {
            "kura": Fake({"/api/status": lambda: dict(KURA)}),
            "konbini": Fake({"/api/health": lambda: dict(KONBINI)}),
            "niwa": Fake({"/api/status": lambda: dict(NIWA)}),
            "shiori": Fake({"/": SHIORI}),
            "hister": Fake(dict(HISTER)),
            "searxng": Fake(SEARXNG),
            "machiya-mcp": Fake({"/api/status": MCP}),
            "smallweb": Fake({"/api/status": SMALLWEB}),
        }
        hister = self.fakes["hister"]
        pages = HISTER["/search"]
        hister.routes["/search"] = lambda: {"total": 0, "documents": []} if "code_kind" in hister.seen[-1]["query"] else pages
        self.tmp = tempfile.mkdtemp(prefix="landing-test-")

    def tearDown(self):
        for f in self.fakes.values():
            f.close()

    def env(self, **kw):
        rooms = ",".join("%s=%s" % (k, self.fakes[k].url) for k in ("shiori", "konbini", "niwa", "kura", "hister", "searxng"))
        env = {"LANDING_AUTH": "open", "LANDING_BIND": "127.0.0.1", "MACHIYA_ROOMS": rooms,
               "LANDING_APPS": "machiya-mcp=%s,smallweb=%s" % (self.fakes["machiya-mcp"].url, self.fakes["smallweb"].url),
               "LANDING_STATE": os.path.join(self.tmp, "landing.json")}
        env.update(kw)
        return env

    def landing(self, now=NOW, **kw):
        return landing.Landing(landing.Config(self.env(**kw)), now=lambda: now)


class Readers(Stack):
    def test_every_app_up(self):
        snap = self.landing().poll()
        a = snap["apps"]
        self.assertEqual({k: v["state"] for k, v in a.items()},
                         {"shiori": "up", "konbini": "up", "niwa": "up", "kura": "up", "hister": "up", "searxng": "up",
                          "machiya-mcp": "up", "smallweb": "up", "vault-mirror": "absent",
                          "feed-import": "absent", "code-import": "absent"})
        self.assertEqual((a["kura"]["version"], a["kura"]["vaultkit"]), ("0.6.8", "0.17.2"))
        self.assertEqual(a["kura"]["facts"], ["314 notes"])
        self.assertEqual(a["konbini"]["facts"], ["78 cards"])
        self.assertEqual(a["niwa"]["facts"], ["1 published"])                       # 0.2.0: published only
        self.assertEqual(a["shiori"]["version"], "build 7598f33")
        self.assertEqual(a["hister"]["facts"], ["1.9k pages"])
        self.assertEqual(a["hister"]["data"]["newest"], NOW - 600)
        self.assertEqual(a["searxng"]["version"], "2026.9.25")
        self.assertEqual(a["machiya-mcp"]["facts"], ["36 tools", "4 rooms"])
        self.assertEqual(a["smallweb"]["facts"], ["12 saved"])
        self.assertEqual(snap["overall"], {"state": "up", "text": "Everything is up"})

    def test_hister_gets_its_origin_and_vault_notes_and_code_are_left_out(self):
        self.landing().poll()
        seen = [s for s in self.fakes["hister"].seen if s["method"] == "GET"]      # the MCP POST needs no Origin
        self.assertTrue(seen and all(s["headers"].get("origin") == "hister://" for s in seen))
        search = [s for s in seen if s["path"] == "/search"][0]
        self.assertIn("-label%3Avault", search["query"])
        self.assertIn("-metadata.source%3Acode", search["query"])           # code-import's documents aren't pages

    def test_only_gets(self):
        # every fake only answers GET; anything else would have failed the poll
        self.landing().poll()
        self.assertTrue(all(f.seen for k, f in self.fakes.items()))

    def test_absent_down_and_error(self):
        self.fakes["kura"].routes["/api/status"] = dict(KURA, error="sync failed")
        self.fakes["searxng"].close()
        env = self.env(LANDING_APPS="")
        snap = landing.Landing(landing.Config(env), now=lambda: NOW).poll()
        a = snap["apps"]
        self.assertEqual(a["kura"]["state"], "error")
        self.assertEqual(a["kura"]["error"], "sync failed")
        self.assertEqual(a["searxng"]["state"], "down")
        self.assertIn(a["searxng"]["error"], ("connection refused", "connection failed", "unreachable"))
        self.assertEqual(a["machiya-mcp"]["state"], "absent")
        self.assertEqual(snap["overall"]["state"], "error")
        self.assertTrue(snap["overall"]["text"].startswith("2 need a look"))
        self.fakes["searxng"] = Fake(SEARXNG)          # tearDown closes it

    def test_timeout_redirect_and_garbage(self):
        def slow():
            time.sleep(1.5)
            return MCP
        self.fakes["machiya-mcp"].routes["/api/status"] = slow
        self.fakes["smallweb"].routes["/api/status"] = (302, b"", "text/plain")
        self.fakes["niwa"].routes["/api/status"] = (200, b"<html>not json", "text/html")
        a = self.landing(LANDING_TIMEOUT="0.5").poll()["apps"]
        self.assertEqual((a["machiya-mcp"]["state"], a["machiya-mcp"]["error"]), ("down", "timed out"))
        self.assertEqual((a["smallweb"]["state"], a["smallweb"]["error"]), ("down", "HTTP 302"))
        self.assertEqual((a["niwa"]["state"], a["niwa"]["error"]), ("down", "not JSON"))

    def test_konbini_refusal_is_down_with_the_status(self):
        self.fakes["konbini"].routes["/api/health"] = (403, b"forbidden", "text/plain")
        a = self.landing().poll()["apps"]
        self.assertEqual((a["konbini"]["state"], a["konbini"]["error"]), ("down", "HTTP 403"))

    def test_token_only_for_rooms_over_https(self):
        self.assertEqual(probes.auth_headers("kura", "https://kura.example.ts.net", "mch_x"), {"Authorization": "Bearer mch_x"})
        self.assertEqual(probes.auth_headers("kura", "http://kura:8080", "mch_x"), {})
        self.assertEqual(probes.auth_headers("hister", "https://hister.example.ts.net", "mch_x"), {})
        self.assertEqual(probes.auth_headers("machiya-mcp", "https://mcp.example.ts.net", "mch_x"), {})
        self.assertEqual(probes.auth_headers("kura", "https://kura.example.ts.net", ""), {})
        path = os.path.join(self.tmp, "token")
        with open(path, "w") as f:
            f.write("mch_abcd_secret\n")
        self.landing(LANDING_TOKEN_FILE=path).poll()
        self.assertTrue(all("authorization" not in s["headers"] for f in self.fakes.values() for s in f.seen))  # http fakes

    def test_vault_mirror_file(self):
        path = os.path.join(self.tmp, "status.json")
        a = self.landing(LANDING_MIRROR_STATUS=path).poll()["apps"]["vault-mirror"]
        self.assertEqual((a["state"], a["error"]), ("down", "no status file yet"))
        with open(path, "w") as f:
            json.dump({"head": "4ca69a1865fc", "synced_at": NOW - 20, "error": None}, f)
        a = self.landing(LANDING_MIRROR_STATUS=path).poll()["apps"]["vault-mirror"]
        self.assertEqual((a["state"], a["facts"]), ("up", ["at 4ca69a1"]))
        with open(path, "w") as f:
            json.dump({"head": "4ca69a1865fc", "synced_at": NOW - 7 * 3600, "error": None}, f)
        self.assertEqual(self.landing(LANDING_MIRROR_STATUS=path).poll()["apps"]["vault-mirror"]["state"], "error")


class HisterWithUsers(Stack):
    """Hister with user handling on: /health stays open (up or down), /api/stats and /search need the owner's token
    (LANDING_HISTER_TOKEN_FILE, X-Access-Token, Hister only); without it the page is up with no count, never down."""

    TOKEN = "LandingHisterToken0123456789"

    def gated(self, body):
        def route():
            seen = self.fakes["hister"].seen[-1]["headers"]
            if seen.get("x-access-token") != self.TOKEN:
                return (403, b"", "text/plain")
            return body() if callable(body) else body
        return route

    def setUp(self):
        super().setUp()
        self.fakes["hister"].routes.update({"/api/stats": self.gated(HISTER["/api/stats"]),
                                            "/search": self.gated(self.fakes["hister"].routes["/search"])})
        self.file = os.path.join(self.tmp, "hister.token")
        with open(self.file, "w") as f:
            f.write(self.TOKEN + "\n")

    def test_without_a_token_up_and_no_count(self):
        a = self.landing().poll()["apps"]["hister"]
        self.assertEqual((a["state"], a["facts"], a["data"]["docs"]), ("up", ["page count needs LANDING_HISTER_TOKEN_FILE"], None))
        self.assertTrue(all("x-access-token" not in s["headers"] for s in self.fakes["hister"].seen))

    def test_with_the_token_the_count_and_nowhere_else(self):
        land = self.landing(LANDING_HISTER_TOKEN_FILE=self.file)
        snap = land.poll()
        a = snap["apps"]["hister"]
        self.assertEqual((a["state"], a["facts"], a["data"]["newest"]), ("up", ["1.9k pages"], NOW - 600))
        health = [s for s in self.fakes["hister"].seen if s["path"] == "/health"]
        self.assertTrue(health and all("x-access-token" not in s["headers"] for s in health))   # /health: no token
        owner_paths = {"/api/cards", "/api/vaults", "/api/recent", "/feed.xml"}       # 0.2.3: the rooms' owner reads
        for key, fake in self.fakes.items():
            if key != "hister":
                self.assertTrue(all("x-access-token" not in s["headers"] for s in fake.seen
                                    if not (key in ("kura", "konbini", "niwa") and s["path"] in owner_paths)
                                    and not (key == "konbini" and s["path"] == "/api/health")), key)
        self.assertNotIn(self.TOKEN, json.dumps(snap) + repr(land.config.hister_token))

    def test_a_refused_token_says_so(self):
        with open(self.file, "w") as f:
            f.write("SomeOtherToken\n")
        a = self.landing(LANDING_HISTER_TOKEN_FILE=self.file).poll()["apps"]["hister"]
        self.assertEqual((a["state"], a["facts"]), ("up", ["page count: the token was refused"]))

    def test_health_down_is_down(self):
        self.fakes["hister"].routes["/health"] = (502, b"bad gateway", "text/plain")
        self.assertEqual(self.landing(LANDING_HISTER_TOKEN_FILE=self.file).poll()["apps"]["hister"]["state"], "down")

    def test_bad_token_file_refuses_and_rotation_is_picked_up(self):
        empty = os.path.join(self.tmp, "empty")
        open(empty, "w").close()
        for path in (empty, os.path.join(self.tmp, "missing"), self.tmp):
            with self.assertRaises(SystemExit) as cm:
                landing.Config(self.env(LANDING_HISTER_TOKEN_FILE=path))
            self.assertNotIn(self.TOKEN, str(cm.exception))
        secret = probes.SecretFile(self.file)
        with open(self.file + ".new", "w") as f:
            f.write("RotatedToken-1\n")
        os.replace(self.file + ".new", self.file)
        self.assertEqual(secret.get(), "RotatedToken-1")
        os.unlink(self.file)
        self.assertEqual(secret.get(), "RotatedToken-1")


class Freshness(Stack):
    def rows(self, snap):
        return {r["key"]: r for r in snap["sync"]}

    def test_rows(self):
        rows = self.rows(self.landing().poll())
        self.assertEqual(list(rows), ["pull", "board", "garden", "push", "pages"])
        self.assertEqual(rows["pull"]["text"], "at 4ca69a1")
        self.assertEqual(rows["board"]["text"], "at 4ca69a1 (the vault's head) · 1 waiting")
        self.assertEqual(rows["garden"]["text"], "at a6857ce · all pushed")
        self.assertEqual(rows["push"]["text"], "314 notes in Hister")
        self.assertTrue(all(r["state"] == "up" for r in rows.values()))

    def test_behind_and_broken_by_age(self):
        self.fakes["kura"].routes["/api/status"] = dict(KURA, synced_at=NOW - 20 * 60)
        snap = self.landing().poll()
        self.assertEqual(self.rows(snap)["pull"]["state"], "behind")
        self.assertEqual(snap["apps"]["kura"]["state"], "behind")
        self.assertEqual(snap["overall"]["state"], "behind")
        self.fakes["kura"].routes["/api/status"] = dict(KURA, synced_at=NOW - 7 * 3600)
        self.assertEqual(self.rows(self.landing().poll())["pull"]["state"], "error")

    def test_board_behind_only_after_a_while(self):
        self.fakes["konbini"].routes["/api/health"] = dict(KONBINI, head="0000000aaaa")
        clock = [NOW]
        l = landing.Landing(landing.Config(self.env()), now=lambda: clock[0])
        self.assertEqual(self.rows(l.poll())["board"]["state"], "up")
        clock[0] += 16 * 60
        self.fakes["kura"].routes["/api/status"] = dict(KURA, synced_at=clock[0] - 30, push=dict(KURA["push"], at=clock[0]))
        self.fakes["konbini"].routes["/api/health"] = dict(KONBINI, head="0000000aaaa", livesync={
            "status": {"daemon": "running", "last_cycle_ts": clock[0] - 30}})
        self.assertEqual(self.rows(l.poll())["board"]["state"], "behind")

    def test_livesync_stopped_is_broken(self):
        self.fakes["konbini"].routes["/api/health"] = dict(KONBINI, livesync={"status": {"daemon": "stopped", "last_cycle_ts": NOW}})
        row = self.rows(self.landing().poll())["board"]
        self.assertEqual(row["state"], "error")
        self.assertIn("LiveSync stopped", row["text"])

    def test_writer_not_pushed_is_behind(self):
        self.fakes["niwa"].routes["/api/status"] = dict(NIWA, sync={"pending": 0, "ahead": 2, "error": None})
        row = self.rows(self.landing().poll())["garden"]
        self.assertEqual((row["state"], row["text"]), ("behind", "at a6857ce · 2 not pushed"))

    def test_rows_of_absent_apps_are_left_out(self):
        env = self.env(MACHIYA_ROOMS="kura=%s" % self.fakes["kura"].url, LANDING_APPS="")
        snap = landing.Landing(landing.Config(env), now=lambda: NOW).poll()
        self.assertEqual([r["key"] for r in snap["sync"]], ["pull", "push"])

    def test_nothing_configured(self):
        snap = landing.Landing(landing.Config({"LANDING_AUTH": "open", "LANDING_BIND": "127.0.0.1"}), now=lambda: NOW).poll()
        self.assertTrue(all(a["state"] == "absent" for a in snap["apps"].values()))
        self.assertEqual(snap["overall"]["text"], "No apps are configured yet")


CHANGELOG = """# Changelog

Intro text.

## 0.6.8

- A **search pill** under the header (vaultkit 0.17.2),
  on every page.
- See [the docs](https://example.com/x) and `KURA_X`.

## 0.6.7

The header sits lower.

### Notes

- nested
  - deeper

## v0.6.5

- Old.

## Unreleased notes

- not a version
"""


class History(unittest.TestCase):
    def test_parse(self):
        s = deploys.parse(CHANGELOG)
        self.assertEqual(list(s), ["0.6.8", "0.6.7", "0.6.5"])
        self.assertEqual(s["0.6.8"], ["A search pill under the header (vaultkit 0.17.2), on every page.", "See the docs and KURA_X."])
        self.assertEqual(s["0.6.7"], ["The header sits lower.", "nested - deeper"])
        self.assertEqual(s["0.6.5"], ["Old."])

    def test_between(self):
        s = deploys.parse(CHANGELOG)
        self.assertEqual([v for v, _ in deploys.between(s, "0.6.5", "0.6.8")], ["0.6.8", "0.6.7"])
        self.assertEqual([v for v, _ in deploys.between(s, None, "0.6.8")], ["0.6.8"])
        self.assertEqual([v for v, _ in deploys.between(s, "0.6.8", "0.6.5")], ["0.6.5"])     # a rollback: just its own
        self.assertEqual(deploys.between(s, "0.6.8", "0.7.0"), [])
        self.assertEqual(deploys.between(s, "build a", "build b"), [])

    def test_observe_and_persist(self):
        path = os.path.join(tempfile.mkdtemp(prefix="landing-test-"), "h.json")
        h = deploys.History(path)
        apps = {"kura": {"state": "up", "version": "0.6.7", "vaultkit": "0.17.1"}, "hister": {"state": "up", "version": ""},
                "niwa": {"state": "down", "version": ""}}
        self.assertTrue(h.observe(apps, NOW))
        self.assertEqual(h.events, [])                                   # first sight is not a deploy
        self.assertFalse(h.observe(apps, NOW + 60))
        apps["kura"] = {"state": "up", "version": "0.6.8", "vaultkit": "0.17.2"}
        self.assertTrue(h.observe(apps, NOW + 120))
        h2 = deploys.History(path)
        self.assertEqual(h2.since, NOW)
        self.assertEqual(h2.events, [{"app": "kura", "from": "0.6.7", "to": "0.6.8", "vaultkit_from": "0.17.1",
                                      "vaultkit_to": "0.17.2", "at": NOW + 120}])
        self.assertEqual(h2.recent(NOW + 200), h2.events)
        self.assertEqual(h2.recent(NOW + 120 + 31 * 86400), [])
        apps["kura"] = {"state": "down", "version": ""}                  # an app that's down is not a deploy
        self.assertFalse(h2.observe(apps, NOW + 300))

    def test_bad_or_unwritable_file(self):
        d = tempfile.mkdtemp(prefix="landing-test-")
        bad = os.path.join(d, "bad.json")
        with open(bad, "w") as f:
            f.write("{nope")
        h = deploys.History(bad)
        self.assertIn("unreadable", h.error)
        h = deploys.History(os.path.join(d, "missing-dir", "h.json"))
        h.observe({"kura": {"state": "up", "version": "1.0.0"}}, NOW)
        self.assertIn("not saved", h.error)
        self.assertEqual(deploys.History("").observe({"kura": {"state": "up", "version": "1.0.0"}}, NOW), True)


class Page(Stack):
    def test_page_escapes_what_apps_say(self):
        self.fakes["kura"].routes["/api/status"] = dict(KURA, version="<script>x</script>", error="<b>bad</b>")
        l = self.landing()
        snap = l.poll()
        html = render.page(landing.house.prefs(""), snap, l.history, l.logs, l.config.links, l.config.targets, NOW)
        self.assertNotIn("<script>x", html)
        self.assertIn("&lt;script&gt;x&lt;/script&gt;", html)
        self.assertNotIn("<b>bad</b>", html)

    def test_deploy_with_changelog(self):
        cl = os.path.join(self.tmp, "kura.md")
        with open(cl, "w") as f:
            f.write(CHANGELOG)
        clfake = Fake({"/kura.md": (200, CHANGELOG, "text/markdown")})
        try:
            clock = [NOW]
            self.fakes["kura"].routes["/api/status"] = dict(KURA, version="0.6.5", vaultkit="v0.17.1")
            l = landing.Landing(landing.Config(self.env(LANDING_CHANGELOGS="kura=%s/kura.md" % clfake.url)), now=lambda: clock[0])
            l.poll()
            clock[0] += 60
            self.fakes["kura"].routes["/api/status"] = dict(KURA, synced_at=clock[0])
            snap = l.poll()
            html = render.main_html(snap, l.history, l.logs, l.config.links, l.config.targets, clock[0])
            self.assertIn("0.6.5 → 0.6.8", html)
            self.assertIn("vaultkit 0.17.1 → 0.17.2", html)
            self.assertIn("<b>0.6.8</b> A search pill under the header", html)
            self.assertIn("<b>0.6.7</b> The header sits lower.", html)
            self.assertIn('+1 more', html)
        finally:
            clfake.close()

    def test_absent_is_calm(self):
        env = self.env(MACHIYA_ROOMS="kura=%s" % self.fakes["kura"].url)
        l = landing.Landing(landing.Config(env), now=lambda: NOW)
        html = render.main_html(l.poll(), l.history, l.logs, l.config.links, l.config.targets, NOW)
        self.assertIn('data-app="niwa" data-state="absent"', html)
        self.assertIn("Not in this stack", html)
        self.assertNotIn('data-state="down"', html)
        self.assertNotIn('data-state="error"', html)


class Server(Stack):
    def serve(self, **env):
        l = landing.Landing(landing.Config(self.env(**env)), now=lambda: NOW)
        srv = ThreadingHTTPServer(("127.0.0.1", 0), landing.make_handler(l))
        threading.Thread(target=srv.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True).start()
        self.addCleanup(srv.server_close)
        self.addCleanup(srv.shutdown)
        return "http://127.0.0.1:%d" % srv.server_address[1]

    def get(self, url, headers=None, method="GET"):
        req = urllib.request.Request(url, headers=headers or {}, method=method)
        try:
            with urllib.request.urlopen(req, timeout=10) as r:
                return r.status, r.headers, r.read().decode()
        except urllib.error.HTTPError as e:
            return e.code, e.headers, e.read().decode()

    def test_tailscale_gate(self):
        base = self.serve(LANDING_AUTH="tailscale", LANDING_USERS="Owner@example.com")
        self.assertEqual(self.get(base + "/")[0], 403)
        self.assertEqual(self.get(base + "/", {"Tailscale-User-Login": "someone@example.com"})[0], 403)
        self.assertEqual(self.get(base + "/api/status", {"Tailscale-User-Login": "someone@example.com"})[0], 403)
        self.assertEqual(self.get(base + "/healthz")[0], 200)              # the container's check: no data
        code, headers, body = self.get(base + "/", {"Tailscale-User-Login": "owner@example.com"})
        self.assertEqual(code, 200)
        self.assertIn("default-src 'self'", headers["Content-Security-Policy"])
        self.assertEqual(headers["Cache-Control"], "no-store")
        self.assertIn("Everything is up", body)
        self.assertIn('class="tabbar"', body)
        self.assertIn('class="rooms"', body)                                 # the Rooms menu, from MACHIYA_ROOMS

    def test_star_and_empty_users(self):
        base = self.serve(LANDING_AUTH="tailscale", LANDING_USERS="*")
        self.assertEqual(self.get(base + "/", {"Tailscale-User-Login": "anyone@example.com"})[0], 200)
        base = self.serve(LANDING_AUTH="tailscale")
        self.assertEqual(self.get(base + "/", {"Tailscale-User-Login": "anyone@example.com"})[0], 403)

    def test_open_mode_host_check(self):
        base = self.serve()
        self.assertEqual(self.get(base + "/")[0], 200)                       # Host: 127.0.0.1:<port>
        self.assertEqual(self.get(base + "/", {"Host": "evil.example"})[0], 403)

    def test_routes(self):
        base = self.serve()
        code, headers, body = self.get(base + "/api/status")
        data = json.loads(body)
        self.assertEqual((code, data["version"], data["apps"]["kura"]["version"]), (200, landing.VERSION, "0.6.8"))
        self.assertNotIn("data", data["apps"]["kura"])
        self.assertEqual(self.get(base + "/settings")[0], 200)
        self.assertEqual(self.get(base + "/static/landing.css?v=1")[0], 200)
        self.assertEqual(self.get(base + "/static/machiya.css")[0], 200)
        self.assertEqual(self.get(base + "/static/icons/machiya.svg")[0], 200)
        self.assertEqual(self.get(base + "/static/../landing.py")[0], 404)
        self.assertEqual(self.get(base + "/nope")[0], 404)
        self.assertEqual(self.get(base + "/", method="POST")[0], 405)
        code, headers, body = self.get(base + "/manifest.webmanifest")
        self.assertEqual(json.loads(body)["name"], "Machiya")
        import http.client
        conn = http.client.HTTPConnection(urlsplit(base).netloc, timeout=10)
        conn.request("GET", "/theme?set=night")
        r = conn.getresponse()
        self.assertEqual((r.status, r.getheader("Location")), (302, "/settings"))
        self.assertIn("theme=night", r.getheader("Set-Cookie"))
        conn.close()

    def test_keep_alive_tailscale_gate_answers_each_request_alone(self):
        """0.3.2: Serve sends different people's requests down one kept-alive connection."""
        import http.client
        base = self.serve(LANDING_AUTH="tailscale", LANDING_USERS="owner@example.com")
        conn = http.client.HTTPConnection(urlsplit(base).netloc, timeout=10)
        got = []
        for login in (None, "owner@example.com", None, "someone@example.com", "owner@example.com", "", None):
            conn.request("GET", "/api/status", headers={"Tailscale-User-Login": login} if login is not None else {})
            r = conn.getresponse()
            got.append((r.status, r.read()))
        conn.close()
        self.assertEqual([g[0] for g in got], [403, 200, 403, 403, 200, 403, 403])

    def test_keep_alive_unread_body_never_becomes_a_request(self):
        """0.3.2: a body landing didn't read closes the connection: kept, its bytes would be the next request, one
        that never went through Serve (here, the owner's)."""
        import socket
        base = self.serve(LANDING_AUTH="tailscale", LANDING_USERS="owner@example.com")
        host, port = urlsplit(base).netloc.split(":")
        smuggled = b"GET /api/status HTTP/1.1\r\nHost: x\r\nTailscale-User-Login: owner@example.com\r\n\r\n"
        for head in (b"PUT /api/prefs HTTP/1.1\r\nHost: x\r\n",                                    # nobody: 403
                     b"POST / HTTP/1.1\r\nHost: x\r\n",                                             # 405
                     b"GET /healthz HTTP/1.1\r\nHost: x\r\n",                                       # open, body unread
                     b"GET /api/status HTTP/1.1\r\nHost: x\r\nTailscale-User-Login: someone@example.com\r\n"):
            s = socket.create_connection((host, int(port)), timeout=2)
            s.sendall(head + b"Content-Length: %d\r\n\r\n" % len(smuggled) + smuggled)
            out, closed = b"", False
            try:
                while True:
                    chunk = s.recv(65536)
                    if not chunk:
                        closed = True
                        break
                    out += chunk
            except socket.timeout:
                pass
            s.close()
            self.assertEqual(out.count(b"HTTP/1.1 "), 1, head)
            self.assertTrue(closed, head)
            self.assertIn(b"\r\nConnection: close\r\n", out)
            self.assertNotIn(b'"apps"', out)

    def test_keep_alive_chunked_body_never_becomes_a_request(self):
        """0.4.1 (sweep LEAD-2): a chunked body (no Content-Length) on PUT /api/prefs is refused with 413 and the
        connection closes: read as an empty body, its bytes were the next request (a 403, then a 200 with the status
        JSON). A duplicate Content-Length is refused the same way."""
        import socket
        base = self.serve(LANDING_PREFS=os.path.join(self.tmp, "prefs.sqlite3"))
        netloc = urlsplit(base).netloc
        host, port = netloc.split(":")
        smuggled = b"GET /api/status HTTP/1.1\r\nHost: %s\r\n\r\n" % netloc.encode()
        for head in (b"PUT /api/prefs HTTP/1.1\r\nHost: %s\r\nTransfer-Encoding: chunked\r\n" % netloc.encode(),
                     b"PUT /api/prefs HTTP/1.1\r\nHost: %s\r\nContent-Length: 0\r\nContent-Length: 0\r\n"
                     % netloc.encode()):
            s = socket.create_connection((host, int(port)), timeout=2)
            s.sendall(head + b"\r\n" + smuggled)
            out, closed = b"", False
            try:
                while True:
                    chunk = s.recv(65536)
                    if not chunk:
                        closed = True
                        break
                    out += chunk
            except socket.timeout:
                pass
            s.close()
            self.assertEqual(out.count(b"HTTP/1.1 "), 1, out)
            self.assertTrue(closed, head)
            self.assertNotIn(b'"apps"', out)
            self.assertTrue(out.startswith(b"HTTP/1.1 413"), out[:40])


IDENTITY = """
version = 1
session_key_file = "session.key"

[principals.owner]
id = "ownerid00000000a"
kind = "person"
owner = true
tailscale = ["owner@example.com"]

[principals.guest]
id = "guestid00000000a"
kind = "person"
tailscale = ["guest@example.com"]
grants = { kura = ["read"] }

[principals.viewer]
id = "viewerid0000000a"
kind = "person"
tailscale = ["viewer@example.com"]
grants = { landing = ["read"] }
"""


class IdentityFile(Server):
    """With MACHIYA_IDENTITY_FILE the page asks the file: the owner and `landing` `read` get in, others 403."""

    def test_grants(self):
        with open(os.path.join(self.tmp, "identity.toml"), "w") as f:
            f.write(IDENTITY)
        with open(os.path.join(self.tmp, "session.key"), "wb") as f:
            f.write(b"k" * 43)
        base = self.serve(LANDING_AUTH="tailscale", MACHIYA_IDENTITY_FILE=os.path.join(self.tmp, "identity.toml"))
        self.assertEqual(self.get(base + "/", {"Tailscale-User-Login": "owner@example.com"})[0], 200)
        self.assertEqual(self.get(base + "/", {"Tailscale-User-Login": "viewer@example.com"})[0], 200)
        self.assertEqual(self.get(base + "/", {"Tailscale-User-Login": "guest@example.com"})[0], 403)
        self.assertEqual(self.get(base + "/api/status", {"Tailscale-User-Login": "guest@example.com"})[0], 403)
        self.assertIn(self.get(base + "/")[0], (401, 403))
        self.assertEqual(self.get(base + "/", {"Authorization": "Bearer mch_nope_bad"})[0], 401)
        self.assertEqual(self.get(base + "/healthz")[0], 200)


def changelog_route(text, seen):
    """A fake app's GET /api/changelog, served by vaultkit.changelog (ETag, 304) from a file."""
    from vaultkit import changelog
    path = os.path.join(tempfile.mkdtemp(prefix="landing-cl-"), "CHANGELOG.md")
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)

    def route():
        h = seen[-1]["headers"] if seen else {}
        status, body, headers = changelog.handle(path, {"If-None-Match": h.get("if-none-match", "")})
        h = dict(headers)
        return (status, body, h.pop("Content-Type"), h)
    return route


class Changelogs(Stack):
    def test_from_each_apps_own_endpoint(self):
        self.fakes["kura"].routes["/api/changelog"] = changelog_route(CHANGELOG, self.fakes["kura"].seen)
        self.fakes["niwa"].routes["/api/changelog"] = (404, b"no changelog\n", "text/plain")
        self.fakes["konbini"].routes["/api/changelog"] = (200, b"<html>a page</html>", "text/html")   # not markdown
        clock = [NOW]
        self.fakes["kura"].routes["/api/status"] = dict(KURA, version="0.6.5", vaultkit="v0.17.1")
        l = landing.Landing(landing.Config(self.env()), now=lambda: clock[0])
        l.poll()
        self.assertEqual(list(l.logs), ["kura"])                     # niwa: 404, konbini: not markdown
        asked = lambda k: [x["path"] for x in self.fakes[k].seen].count("/api/changelog")
        self.assertEqual((asked("kura"), asked("niwa"), asked("konbini")), (1, 1, 1))
        self.assertEqual(asked("smallweb") + asked("machiya-mcp"), 2)
        self.assertEqual(asked("hister") + asked("searxng") + asked("shiori"), 0)   # engines don't serve one
        clock[0] += 120
        self.fakes["kura"].routes["/api/status"] = dict(KURA, synced_at=clock[0])     # 0.6.8: in the log already
        snap = l.poll()
        self.assertEqual((asked("kura"), asked("niwa")), (1, 1))     # nothing new to ask before the next round
        html = render.main_html(snap, l.history, l.logs, l.config.links, l.config.targets, clock[0])
        self.assertIn("0.6.5 → 0.6.8", html)
        self.assertIn("<b>0.6.8</b> A search pill under the header", html)
        self.assertNotIn("Niwa</b><span class=\"dver\">0.4.6", html)
        clock[0] += 901                                               # the next round: a 304 keeps the copy
        l.poll()
        self.assertEqual(asked("kura"), 2)
        self.assertTrue(self.fakes["kura"].seen[-1]["headers"].get("if-none-match", "").startswith('"'))
        self.assertIn("0.6.8", l.logs["kura"])

    def test_a_new_version_is_asked_for_at_once(self):
        text = [CHANGELOG]
        from vaultkit import changelog as vk_changelog   # noqa: F401 (the shape it serves)
        self.fakes["kura"].routes["/api/changelog"] = lambda: (200, text[0], "text/markdown; charset=utf-8")
        clock = [NOW]
        l = landing.Landing(landing.Config(self.env()), now=lambda: clock[0])
        l.poll()
        text[0] = "## 0.7.0\n\n- New.\n\n" + CHANGELOG
        clock[0] += 90
        self.fakes["kura"].routes["/api/status"] = dict(KURA, version="0.7.0", synced_at=clock[0])
        l.poll()
        self.assertIn("0.7.0", l.logs["kura"])

    def test_the_pages_own_changelog(self):
        base = Server.serve(self, LANDING_AUTH="tailscale", LANDING_USERS="owner@example.com")
        code, headers, body = Server.get(self, base + "/api/changelog", {"Tailscale-User-Login": "owner@example.com"})
        self.assertEqual((code, headers["Content-Type"]), (200, "text/markdown; charset=utf-8"))
        self.assertIn("## " + landing.VERSION, body)
        self.assertTrue(headers["ETag"])
        self.assertEqual(Server.get(self, base + "/api/changelog")[0], 200)          # 0.3.0: open, like every app's


SHIORI_STATUS = {"version": "0.1.0", "build": "9862094", "built": "2026-10-04T16:06:25Z"}
AI = {"enabled": True, "engine": "anthropic", "model": "m", "remaining": 87, "answer": True}
MCP_INIT = {"jsonrpc": "2.0", "id": 1, "result": {"protocolVersion": "2025-06-18", "serverInfo": {"name": "hister", "version": "v0.20.0"}}}
CARDS = {"cards": [
    {"slug": "a", "title": "Alpha", "board": "wip", "rank": None, "priority": 1, "updated": "2026-01-01", "next": "do a", "area": "tools", "due": ""},
    {"slug": "b", "title": "Beta", "board": "wip", "rank": None, "priority": None, "updated": "2026-01-03", "next": "", "area": "", "due": ""},
    {"slug": "c", "title": "Gamma", "board": "wip", "rank": None, "priority": None, "updated": "2026-01-02", "next": "", "area": "", "due": ""},
    {"slug": "late", "title": "Late", "board": "backlog", "rank": None, "priority": None, "updated": "", "next": "", "area": "", "due": "2025-12-30"},
    {"slug": "soon", "title": "Soon", "board": "ready", "rank": None, "priority": None, "updated": "", "next": "", "area": "", "due": "2026-01-03"},
    {"slug": "far", "title": "Far", "board": "ready", "rank": None, "priority": None, "updated": "", "next": "", "area": "", "due": "2026-03-01"},
    {"slug": "shut", "title": "Shut", "board": "done", "rank": None, "priority": None, "updated": "", "next": "", "area": "", "due": "2026-01-02"},
]}
RECENT = {"total": 2, "results": [{"title": "Lantern", "url": "https://kura.example.ts.net/n/Lantern", "folder": "Notes", "changed": NOW - 300},
                                  {"title": "<b>x</b>", "url": "https://kura.example.ts.net/n/X", "folder": "", "changed": NOW - 900}]}
FEED = (200, '<?xml version="1.0"?><rss version="2.0"><channel><title>Niwa</title>'
             '<item><title>Culture</title><link>https://niwa.example.ts.net/n/Culture</link><pubDate>Wed, 31 Dec 2025 00:00:00 GMT</pubDate></item>'
             '<item><title>Old</title><link>https://niwa.example.ts.net/n/Old</link><pubDate>Mon, 01 Sep 2025 00:00:00 GMT</pubDate></item>'
             '</channel></rss>', "application/rss+xml")
PAGES = {"total": 3, "documents": [
    {"url": "https://example.com/read", "title": "A Read Story", "domain": "example.com", "added": NOW - 9000, "updated": NOW - 60,
     "metadata": {"source": "newsblur", "via": "newsblur", "newsblur_stream": "read"}},
    {"url": "https://example.com/star", "title": "A Starred One", "domain": "example.com", "added": NOW - 9000, "updated": NOW - 120,
     "metadata": {"source": "newsblur", "newsblur_stream": "starred"}},
    {"url": "https://example.org/visit", "title": "", "domain": "example.org", "added": NOW - 400, "updated": NOW - 300, "metadata": {}}]}


class ZeroTwo(Stack):
    """0.2.0: Shiori's status, Hister's version, Niwa's published count, the mirror's and feed-import's files, Pages."""

    def test_shiori_status_ai_and_feed(self):
        self.fakes["shiori"].routes.update({"/_shiori/status.json": SHIORI_STATUS, "/shiori/ai/status": AI,
                                            "/shiori/healthz": (200, "ok", "text/plain")})
        a = self.landing().poll()["apps"]["shiori"]
        self.assertEqual((a["state"], a["version"], a["data"]["build"], a["vaultkit"]), ("up", "0.1.0", "9862094", ""))
        self.assertNotIn("build", a)                                            # 0.3.0: the card shows the version only
        self.assertEqual(a["facts"], ["AI on · 87 left today", "feed ok"])
        self.assertEqual(render.version_line(a), "0.1.0")
        self.fakes["shiori"].routes["/shiori/ai/status"] = (503, "<html>down</html>", "text/html")
        self.fakes["shiori"].routes["/shiori/healthz"] = (502, "bad", "text/plain")
        a = self.landing().poll()["apps"]["shiori"]
        self.assertEqual((a["state"], a["facts"]), ("up", ["AI down", "feed down"]))     # never makes Shiori down
        del self.fakes["shiori"].routes["/_shiori/status.json"]
        a = self.landing().poll()["apps"]["shiori"]
        self.assertEqual(a["version"], "build 7598f33")                               # no status.json: the build stamp

    def test_hister_version_from_its_mcp(self):
        self.fakes["hister"].routes["POST /mcp"] = MCP_INIT
        probes.HISTER_VERSION.clear()
        path = os.path.join(self.tmp, "hister-token")
        with open(path, "w") as f:
            f.write("owner-token\n")
        clock = [NOW]
        l = landing.Landing(landing.Config(self.env(LANDING_HISTER_TOKEN_FILE=path)), now=lambda: clock[0])
        self.assertEqual(l.poll()["apps"]["hister"]["version"], "0.20.0")
        posts = [x for x in self.fakes["hister"].seen if x["method"] == "POST"]
        self.assertEqual(len(posts), 1)
        self.assertEqual(json.loads(posts[0]["body"])["method"], "initialize")
        self.assertEqual(posts[0]["headers"].get("x-access-token"), "owner-token")
        clock[0] += 60
        l.poll()
        self.assertEqual(len([x for x in self.fakes["hister"].seen if x["method"] == "POST"]), 1)   # cached
        probes.HISTER_VERSION.clear()
        del self.fakes["hister"].routes["POST /mcp"]
        self.assertEqual(self.landing().poll()["apps"]["hister"]["version"], "")                  # unreadable: none

    def test_mirror_version_and_feed_import(self):
        mirror, feeds = os.path.join(self.tmp, "mirror.json"), os.path.join(self.tmp, "feeds.json")
        with open(mirror, "w") as f:
            json.dump({"head": "4ca69a1865fc", "synced_at": NOW - 20, "error": None, "version": "0.1.1"}, f)

        def write(added, **kw):
            with open(feeds, "w") as f:
                json.dump(dict({"version": "0.1.1", "ok": True, "running": False, "last_success": clock[0] - 100,
                                "failures_in_a_row": 0, "error": None,
                                "counts": {"newsblur": {"added": added, "known": 4}}}, **kw), f)
        clock = [NOW]
        write(100)
        l = landing.Landing(landing.Config(self.env(LANDING_MIRROR_STATUS=mirror, LANDING_FEED_STATUS=feeds)), now=lambda: clock[0])
        snap = l.poll()
        self.assertEqual(snap["apps"]["vault-mirror"]["version"], "0.1.1")
        f = snap["apps"]["feed-import"]
        self.assertEqual((f["state"], f["version"], f["facts"]), ("up", "0.1.1", ["NewsBlur"]))
        row = {r["key"]: r for r in snap["sync"]}["feeds"]
        self.assertEqual((row["label"], row["source"], row["state"]), ("Feeds read", "feed-import", "up"))
        self.assertEqual(row["text"], "0 added since the page started watching")
        clock[0] += 86400 + 60
        self.fakes["kura"].routes["/api/status"] = dict(KURA, synced_at=clock[0], push=dict(KURA["push"], at=clock[0]))
        write(130)
        row = {r["key"]: r for r in l.poll()["sync"]}["feeds"]
        self.assertEqual(row["text"], "30 added in the last day")
        write(130, ok=False, error="newsblur: HTTP 502", failures_in_a_row=3)
        snap = l.poll()
        row = {r["key"]: r for r in snap["sync"]}["feeds"]
        self.assertEqual((row["state"], row["error"]), ("error", "newsblur: HTTP 502"))
        self.assertIn("3 failed runs", row["text"])
        self.assertIn("feed-import", snap["overall"]["text"])
        self.assertNotIn("Feeds read", snap["overall"]["text"])                           # the app, not twice
        write(130, last_success=clock[0] - 2 * 3600)
        self.assertEqual(l.poll()["apps"]["feed-import"]["state"], "behind")

    def test_pages_row_is_judged(self):
        clock = [NOW]
        l = landing.Landing(landing.Config(self.env()), now=lambda: clock[0])
        rows = lambda: {r["key"]: r for r in l.poll()["sync"]}
        self.assertEqual(rows()["pages"]["state"], "up")
        clock[0] += 2 * 86400                                       # under three days: still calm
        self.fakes["kura"].routes["/api/status"] = dict(KURA, synced_at=clock[0], push=dict(KURA["push"], at=clock[0]))
        self.fakes["konbini"].routes["/api/health"] = dict(KONBINI, livesync={"status": {"daemon": "running", "last_cycle_ts": clock[0]}})
        self.assertEqual(rows()["pages"]["state"], "up")
        clock[0] += 2 * 86400
        self.fakes["kura"].routes["/api/status"] = dict(KURA, synced_at=clock[0], push=dict(KURA["push"], at=clock[0]))
        self.fakes["konbini"].routes["/api/health"] = dict(KONBINI, livesync={"status": {"daemon": "running", "last_cycle_ts": clock[0]}})
        self.assertEqual(rows()["pages"]["state"], "behind")
        self.fakes["hister"].routes["/health"] = (502, "", "text/plain")
        row = rows()["pages"]
        self.assertEqual((row["state"], row["error"]), ("error", "Hister is down"))


class ZeroTwoOne(Stack):
    """0.2.1: Konbini's WIP, Kura's vaults and total notes (counts only), Shiori's web searches."""

    def test_konbini_wip(self):
        self.fakes["konbini"].routes["/api/cards"] = CARDS
        a = self.landing().poll()["apps"]["konbini"]
        self.assertEqual(a["facts"], ["3 in WIP", "78 cards"])
        self.fakes["konbini"].routes["/api/health"] = dict(KONBINI, boards={"wip": 9, "done": 4})   # preferred when present
        self.fakes["konbini"].seen.clear()
        a = self.landing().poll()["apps"]["konbini"]
        self.assertEqual(a["facts"][0], "9 in WIP")
        self.assertEqual(self.fakes["konbini"].seen[0]["path"], "/api/health")           # the probe's own call
        probe_calls = [x["path"] for x in self.fakes["konbini"].seen[:1]]
        self.assertNotIn("/api/cards", probe_calls)

    def test_kura_vaults_counts_only(self):
        self.fakes["kura"].routes["/api/vaults"] = {"vaults": [
            {"name": "personal", "title": "Personal", "default": True, "private": False, "notes": 314},
            {"name": "secretclient", "title": "Secret Client", "default": False, "private": True, "notes": 1000},
            {"name": "acmecorp", "title": "Acme Corp", "default": False, "private": True, "notes": 108}]}
        l = self.landing()
        snap = l.poll()
        a = snap["apps"]["kura"]
        self.assertEqual(a["facts"], ["3 vaults · 1.4k notes"])
        dumped = json.dumps(snap) + json.dumps(l.public(snap))
        for name in ("secretclient", "Secret Client", "acmecorp", "Acme Corp"):
            self.assertNotIn(name, dumped)
        html = render.page(landing.house.prefs(""), snap, l.history, l.logs, l.config.links, l.config.targets, NOW) + \
            render.home_html(snap, l.config.links, l.config.targets, "", NOW)
        self.assertNotIn("Secret Client", html)
        self.assertIn("1.4k notes", html)                      # the launcher's tile counts every vault too
        self.fakes["kura"].routes["/api/vaults"] = (403, "forbidden", "text/plain")
        self.assertEqual(self.landing().poll()["apps"]["kura"]["facts"], ["314 notes"])     # quietly the default's

    def test_search_counts(self):
        path = os.path.join(self.tmp, "searches.json")
        a = self.landing(LANDING_SEARCH_COUNTS=path).poll()["apps"]["shiori"]
        self.assertFalse(a.get("more"))                                           # missing: left out
        with open(path, "w") as f:
            json.dump({"updated": "2026-01-01T00:00:00Z", "today": 312, "yesterday": 290, "month": 4210, "year": 51234,
                       "by_day": {"2026-01-01": 312}}, f)
        self.fakes["shiori"].routes["/_shiori/status.json"] = dict(SHIORI_STATUS, hister="v0.19.9")
        l = self.landing(LANDING_SEARCH_COUNTS=path)
        snap = l.poll()
        a = snap["apps"]["shiori"]
        self.assertEqual(a["more"], "Searches 312 today · 4.2k mo · 51.2k yr")
        html = render.main_html(snap, l.history, l.logs, l.config.links, l.config.targets, NOW)
        self.assertIn("Searches 312 today", html)
        self.assertNotIn("0.19.9", html)                        # Shiori's built-against Hister isn't shown
        self.assertEqual(probes.compact(1250000), "1.2M")          # cut, not rounded (0.3.0)
        with open(path, "w") as f:
            f.write("{broken")
        self.assertFalse(self.landing(LANDING_SEARCH_COUNTS=path).poll()["apps"]["shiori"].get("more"))


class OwnerToken(Stack):
    """0.2.3: the rooms run AUTH=hister, so their owner-only reads carry the owner's token (X-Access-Token, from
    LANDING_HISTER_TOKEN_FILE) to the configured room address only; the open probes stay credential-free."""

    def setUp(self):
        super().setUp()
        self.path = os.path.join(self.tmp, "owner-token")
        with open(self.path, "w") as f:
            f.write("owner-secret\n")

        def gated(fake, body):
            def route():              # the request being answered is the fake's last one seen
                ok = fake.seen and fake.seen[-1]["headers"].get("x-access-token") == "owner-secret"
                return body if ok else (401, "sign in", "text/plain")
            return route
        self.fakes["konbini"].routes["/api/cards"] = gated(self.fakes["konbini"], CARDS)
        self.fakes["kura"].routes["/api/vaults"] = gated(self.fakes["kura"], {"vaults": [{"name": "personal", "notes": 314},
                                                                                        {"name": "x", "notes": 10}]})
        self.fakes["kura"].routes["/api/recent"] = gated(self.fakes["kura"], RECENT)
        self.fakes["niwa"].routes["/feed.xml"] = gated(self.fakes["niwa"], FEED)
        self.fakes["kura"].routes["/api/status"] = dict(KURA, vault_count=2)

    def test_without_the_token_it_degrades_quietly(self):
        snap = self.landing().poll()
        a, t = snap["apps"], snap["today"]
        self.assertEqual(a["kura"]["facts"], ["314 notes", "2 vaults"])          # the open vault_count
        self.assertEqual(a["konbini"]["facts"], ["78 cards"])
        self.assertEqual((t["working"], t["notes"], t["garden"]), (None, None, None))
        self.assertEqual(snap["overall"]["state"], "up")

    def test_with_the_token_only_owner_reads_carry_it(self):
        snap = self.landing(LANDING_HISTER_TOKEN_FILE=self.path).poll()
        a, t = snap["apps"], snap["today"]
        self.assertEqual(a["kura"]["facts"], ["2 vaults · 324 notes"])
        self.assertEqual(a["konbini"]["facts"], ["3 in WIP", "78 cards"])
        self.assertEqual((t["wip"], len(t["notes"]), len(t["garden"])), (3, 2, 1))
        owner_paths = {"/api/cards", "/api/vaults", "/api/recent", "/feed.xml"}
        for key in ("kura", "konbini", "niwa"):
            for req in self.fakes[key].seen:
                has = req["headers"].get("x-access-token") == "owner-secret"
                owner_read = req["path"] in owner_paths or (key == "konbini" and req["path"] == "/api/health")
                self.assertEqual(has, owner_read, (key, req["path"]))   # open probes: none
        for key in ("shiori", "searxng", "machiya-mcp", "smallweb"):
            self.assertFalse(any("x-access-token" in r["headers"] for r in self.fakes[key].seen), key)

    def test_konbini_health_full_view_for_the_owner(self):
        full = dict(KONBINI, boards={"wip": 7, "done": 3})
        limited = {"ok": True, "version": "0.11.6", "head": KURA["head"], "cards": 78, "auth": "hister"}
        fake = self.fakes["konbini"]
        fake.routes["/api/health"] = lambda: full if fake.seen[-1]["headers"].get("x-access-token") == "owner-secret" else limited
        snap = self.landing(LANDING_HISTER_TOKEN_FILE=self.path).poll()
        self.assertEqual(snap["apps"]["konbini"]["facts"], ["7 in WIP", "78 cards"])
        self.assertTrue({r["key"]: r for r in snap["sync"]}["board"]["at"])          # LiveSync's cycle is back
        cards_calls = [x for x in fake.seen if x["path"] == "/api/cards"]
        self.assertEqual(len(cards_calls), 1)              # Today's listing only: with `boards` the probe doesn't list
        fake.seen.clear()
        snap = self.landing().poll()                                                    # no token: the limited view
        self.assertEqual(snap["apps"]["konbini"]["facts"], ["78 cards"])
        self.assertIsNone({r["key"]: r for r in snap["sync"]}["board"]["at"])

    def test_konbini_health_refused_token_falls_back(self):
        fake = self.fakes["konbini"]
        fake.routes["/api/health"] = lambda: (401, "bad token", "text/plain") if fake.seen[-1]["headers"].get("x-access-token") \
            else {"ok": True, "version": "0.11.6", "head": KURA["head"], "cards": 78}
        snap = self.landing(LANDING_HISTER_TOKEN_FILE=self.path).poll()
        self.assertEqual((snap["apps"]["konbini"]["state"], snap["apps"]["konbini"]["facts"]), ("up", ["3 in WIP", "78 cards"]))   # cards still take it
        tried = [bool(x["headers"].get("x-access-token")) for x in fake.seen if x["path"] == "/api/health"]
        self.assertEqual(tried, [True, False])

    def test_never_across_a_redirect(self):
        elsewhere = Fake({"/api/cards": CARDS})
        self.addCleanup(elsewhere.close)
        self.fakes["konbini"].routes["/api/cards"] = (302, "", "text/plain", {"Location": elsewhere.url + "/api/cards"})
        t = self.landing(LANDING_HISTER_TOKEN_FILE=self.path).poll()["today"]
        self.assertIsNone(t["working"])
        self.assertEqual(elsewhere.seen, [])

    def test_only_to_room_addresses(self):
        from probes import owner_headers, owner_url
        secret = probes.SecretFile(self.path)
        self.assertEqual(owner_headers("kura", "https://kura.example.ts.net", "", secret), {"X-Access-Token": "owner-secret"})
        self.assertEqual(owner_headers("kura", "http://127.0.0.1:8080", "", secret), {"X-Access-Token": "owner-secret"})
        self.assertEqual(owner_headers("kura", "http://kura:8080", "", secret), {})                 # plain http: never
        for key in ("hister", "searxng", "shiori", "machiya-mcp", "smallweb"):
            self.assertEqual(owner_headers(key, "https://%s.example.ts.net" % key, "", secret), {}, key)
        self.assertEqual(owner_headers("kura", "https://kura.example.ts.net", "", None), {})
        # 0.5.0: a room token in LANDING_TOKEN_FILE goes instead, and Hister's token never reaches a room then
        room = "mht_" + "L" * 43
        self.assertEqual(owner_headers("kura", "https://kura.example.ts.net", room, secret),
                         {"Authorization": "Bearer " + room})
        self.assertEqual(owner_headers("konbini", "http://127.0.0.1:8081", room, secret),
                         {"Authorization": "Bearer " + room})
        self.assertEqual(owner_headers("kura", "http://kura:8080", room, secret), {})
        self.assertEqual(owner_headers("hister", "https://hister.example.ts.net", room, secret), {})
        with self.assertRaises(probes.FetchError):
            owner_url("https://kura.example.ts.net", "//evil.example/x")
        self.assertEqual(owner_url("https://kura.example.ts.net/", "/api/vaults"), "https://kura.example.ts.net/api/vaults")


class Launcher(Stack):
    """0.2.0: / is the launcher (the rooms and Today); the status page is /status."""

    def setUp(self):
        super().setUp()
        self.fakes["konbini"].routes["/api/cards"] = CARDS
        self.fakes["kura"].routes["/api/recent"] = RECENT
        self.fakes["niwa"].routes["/feed.xml"] = FEED
        self.fakes["hister"].routes["/search"] = PAGES

    def today(self, **env):
        l = self.landing(**env)
        snap = l.poll()
        return l, snap, snap["today"]

    def test_today(self):
        _, _, t = self.today()
        self.assertEqual(t["wip"], 3)
        self.assertEqual([c["title"] for c in t["working"]], ["Alpha", "Beta", "Gamma"])     # priority, then newest
        self.assertTrue(t["working"][0]["url"].endswith("/p/a"))
        self.assertEqual([(c["title"], c["days"]) for c in t["due"]], [("Late", -2), ("Soon", 2)])   # not far, not done
        self.assertEqual([n["title"] for n in t["notes"]], ["Lantern", "<b>x</b>"])
        self.assertEqual([g["title"] for g in t["garden"]], ["Culture"])                      # the last 30 days
        self.assertEqual([(p["title"], p["reader"], p["starred"]) for p in t["pages"]],
                         [("A Read Story", "NewsBlur", False), ("A Starred One", "NewsBlur", True), ("example.org", "", False)])

    def test_today_pages_keep_only_link_schemes(self):
        """0.4.1 (sweep LEAD-5): a stored address that isn't http(s), gemini or gopher never becomes a link."""
        docs = [{"url": u, "title": "T " + u[:12], "domain": "x", "updated": NOW - 60, "metadata": {}}
                for u in ("javascript:alert(1)", " JavaScript:alert(2)", "data:text/html,x", "vbscript:x", "file:///etc/passwd",
                          "gemini://capsule.example/", "gopher://hole.example/1", "HTTPS://example.com/a")]
        self.fakes["hister"].routes["/search"] = {"total": len(docs), "documents": docs}
        l, _, t = self.today()
        self.assertEqual([p["url"] for p in t["pages"]],
                         ["gemini://capsule.example/", "gopher://hole.example/1", "HTTPS://example.com/a"])
        html = render.home_html(l.current(), l.config.links, l.config.targets, "", NOW)
        self.assertNotIn("javascript:", html.lower())
        self.assertNotIn("data:text", html)

    def test_sections_degrade_quietly(self):
        self.fakes["konbini"].routes["/api/cards"] = (403, "forbidden", "text/plain")
        self.fakes["niwa"].routes["/feed.xml"] = (200, "<html>not a feed", "text/html")
        env = self.env(MACHIYA_ROOMS="konbini=%s,niwa=%s" % (self.fakes["konbini"].url, self.fakes["niwa"].url))
        l = landing.Landing(landing.Config(env), now=lambda: NOW)
        t = l.poll()["today"]
        self.assertEqual((t["working"], t["due"], t["notes"], t["garden"], t["pages"]), (None, None, None, None, None))
        html = render.home_html(l.current(), l.config.links, l.config.targets, "", NOW)
        self.assertNotIn("Working On", html)
        self.assertNotIn('class="today"', html)
        self.assertIn('data-app="konbini"', html)                       # the tile stays, with the card count

    def test_home_page(self):
        l, snap, _ = self.today(LANDING_SEARCH_URL="https://search.example.ts.net/")
        html = render.home_html(snap, l.config.links, l.config.targets, l.config.search, NOW)
        self.assertIn('<a class="pill" href="/status" data-state="up"', html)
        self.assertIn(">All up</a>", html)
        self.assertIn('<form class="search launch" role="search" action="https://search.example.ts.net/" method="get">', html)
        self.assertIn('name="q"', html)
        for count in ("1.9k pages", "3 in WIP", "1 published", "314 notes"):
            self.assertIn(count, html)
        for head in ("Working On", "Due Soon", "Notes Changed", "Saved &amp; Read", "Garden"):
            self.assertIn(head, html)
        self.assertIn("&lt;b&gt;x&lt;/b&gt;", html)                     # escaped
        self.assertIn("2 days overdue", html)
        self.assertIn("Read in NewsBlur", html)
        self.assertIn("Starred in NewsBlur", html)
        self.assertNotIn("form class=\"search launch\"", render.home_html(snap, l.config.links, l.config.targets, "", NOW))

    def test_pill_counts(self):
        self.assertIn(">1 needs a look<", render.pill({"state": "error", "count": 1, "text": "x"}))
        self.assertIn(">3 need a look<", render.pill({"state": "error", "count": 3, "text": "x"}))
        self.assertIn(">2 behind<", render.pill({"state": "behind", "count": 2, "text": "x"}))

    def test_routes_and_csp(self):
        base = Server.serve(self, LANDING_SEARCH_URL="https://search.example.ts.net/")
        code, headers, body = Server.get(self, base + "/")
        self.assertEqual(code, 200)
        self.assertIn("form-action 'self' https://search.example.ts.net;", headers["Content-Security-Policy"])
        self.assertIn('class="landing home"', body)
        self.assertIn('<b class="here">Home</b>', body)
        code, _, body = Server.get(self, base + "/status")
        self.assertEqual(code, 200)
        self.assertIn('<b class="here">Status</b>', body)
        self.assertIn('id="sync"', body)
        self.assertIn("<title>Status - Machiya</title>", body)
        code, _, body = Server.get(self, base + "/api/today")
        self.assertEqual((code, json.loads(body)["wip"]), (200, 3))
        base = Server.serve(self)
        self.assertIn("form-action 'self';", Server.get(self, base + "/")[1]["Content-Security-Policy"])

    def test_search_url_must_be_plain(self):
        for bad in ("javascript:alert(1)", "https://x/\"onx", "ftp://x/"):
            with self.assertRaises(SystemExit):
                landing.Config({"LANDING_AUTH": "open", "LANDING_BIND": "127.0.0.1", "LANDING_SEARCH_URL": bad})


SID = "mhs_" + "a" * 43


class ZeroThree(Stack):
    """0.3.0: k counts, Shiori's version and rebuilds, Shiori's changelog."""

    def test_counts(self):
        cases = {0: "0", 999: "999", 1000: "1k", 1050: "1k", 1099: "1k", 1150: "1.1k", 1999: "1.9k", 12340: "12.3k",
                 999999: "999.9k", 1000000: "1M", 2000000: "2M", 1250000: "1.2M", 2500000000: "2.5B"}   # cut, not rounded
        for n, want in cases.items():
            self.assertEqual(probes.count(n), want, n)
        self.assertEqual(probes.plural(1, "note"), "1 note")
        self.assertEqual(probes.plural(12340, "note"), "12.3k notes")

    def test_shiori_rebuild_is_a_deploy(self):
        st = dict(SHIORI_STATUS, build="1a5633c")
        self.fakes["shiori"].routes["/_shiori/status.json"] = lambda: st
        clock = [NOW]
        l = landing.Landing(landing.Config(self.env()), now=lambda: clock[0])
        l.poll()
        st["build"] = "381f496"
        clock[0] += 60
        snap = l.poll()
        ev = l.history.recent(clock[0])[0]
        self.assertEqual((ev["app"], ev["from"], ev["to"], ev["build_from"], ev["build_to"]),
                         ("shiori", "0.1.0", "0.1.0", "1a5633c", "381f496"))
        html = render.main_html(snap, l.history, l.logs, l.config.links, l.config.targets, clock[0])
        self.assertIn("0.1.0 (build 1a5633c → 381f496)", html)
        st.update(version="0.2.0", build="9999999")
        clock[0] += 60
        l.poll()
        ev = l.history.recent(clock[0])[0]
        self.assertEqual((ev["from"], ev["to"], ev.get("build_from")), ("0.1.0", "0.2.0", None))

    def test_shiori_changelog(self):
        log = "# Changelog\n\n## 0.2.0 (2026-10-04)\n\n- Real versions.\n\n## 0.1.0 (2026-10-01)\n\n- First.\n"
        self.fakes["shiori"].routes["/_shiori/CHANGELOG.md"] = (200, log, "application/octet-stream")
        self.fakes["shiori"].routes["/_shiori/status.json"] = dict(SHIORI_STATUS, version="0.2.0")
        l = self.landing()
        l.poll()
        self.assertEqual(l.logs["shiori"]["0.2.0"], ["Real versions."])
        self.fakes["shiori"].routes["/_shiori/CHANGELOG.md"] = (404, "<html>404</html>", "text/html")
        l = self.landing()
        l.poll()
        self.assertNotIn("shiori", l.logs)


class CodeImport(Stack):
    """0.3.4: the owner's repos searchable as code: Hister's "N repos", the code-import row and Repos indexed."""

    STATUS = {"version": "0.1.0", "ok": True, "running": False, "started": NOW - 200, "last_success": NOW - 100,
              "failures_in_a_row": 0, "error": None, "last_full": NOW - 3600, "last_run": {},
              "counts": {"forgejo": {"repo": {"added": 30, "skipped": 4}, "issue": {"added": 400}},
                         "github": {"repo": {"added": 12}, "pr": {"added": 950, "refused": 2}}}}

    def write(self, path, **kw):
        with open(path, "w") as f:
            json.dump(dict(self.STATUS, **kw), f)

    def repos_route(self, answer):
        hister = self.fakes["hister"]
        hister.routes["/search"] = lambda: answer if "code_kind" in hister.seen[-1]["query"] else HISTER["/search"]

    def test_hister_counts_repos(self):
        self.repos_route({"total": 1234, "documents": []})
        a = self.landing().poll()["apps"]["hister"]
        self.assertEqual(a["facts"], ["1.9k pages", "1.2k repos"])
        q = [x["query"] for x in self.fakes["hister"].seen if "code_kind" in x["query"]][0]
        self.assertIn("metadata.source%3Acode%20metadata.code_kind%3Arepo", q)
        self.repos_route({"total": 0, "documents": []})
        self.assertEqual(self.landing().poll()["apps"]["hister"]["facts"], ["1.9k pages"])     # 0: hidden
        self.repos_route((500, "x", "text/plain"))
        self.assertEqual(self.landing().poll()["apps"]["hister"]["facts"], ["1.9k pages"])     # unavailable: hidden

    def test_code_import_row_and_sync(self):
        path = os.path.join(self.tmp, "code.json")
        a = self.landing(LANDING_CODE_STATUS=path).poll()["apps"]["code-import"]
        self.assertEqual(a["state"], "absent")                                             # missing file: quiet
        clock = [NOW]
        self.write(path)
        l = landing.Landing(landing.Config(self.env(LANDING_CODE_STATUS=path)), now=lambda: clock[0])
        snap = l.poll()
        a = snap["apps"]["code-import"]
        self.assertEqual((a["state"], a["version"], a["facts"]), ("up", "0.1.0", ["Forgejo, GitHub", "42 repos"]))
        row = {r["key"]: r for r in snap["sync"]}["code"]
        self.assertEqual((row["label"], row["source"], row["state"], row["at"]), ("Repos indexed", "code-import", "up", NOW - 100))
        clock[0] += 86400 + 60
        self.fakes["kura"].routes["/api/status"] = dict(KURA, synced_at=clock[0], push=dict(KURA["push"], at=clock[0]))
        self.write(path, last_success=clock[0] - 60,
                   counts=dict(self.STATUS["counts"], github={"repo": {"added": 12}, "pr": {"added": 1100}}))
        row = {r["key"]: r for r in l.poll()["sync"]}["code"]
        self.assertEqual(row["text"], "150 added in the last day")
        self.write(path, ok=False, error="github: HTTP 502", failures_in_a_row=4, last_success=clock[0] - 60)
        snap = l.poll()
        row = {r["key"]: r for r in snap["sync"]}["code"]
        self.assertEqual((row["state"], row["error"]), ("error", "github: HTTP 502"))
        self.assertIn("4 failed runs", row["text"])
        self.assertIn("code-import", snap["overall"]["text"])
        self.assertNotIn("Repos indexed", snap["overall"]["text"])
        html = render.main_html(snap, l.history, l.logs, l.config.links, l.config.targets, clock[0])
        self.assertIn("Repos indexed", html)
        with open(path, "w") as f:
            f.write("{broken")
        self.assertEqual(l.poll()["apps"]["code-import"]["state"], "down")


class HisterSignIn(Server):
    """0.3.0: LANDING_AUTH=hister, like Konbini and Niwa: the helper decides, the tailnet is the fallback."""

    def setUp(self):
        super().setUp()
        self.helper = Fake({"/healthz": {"ok": True},
                            "/v1/check": lambda: {"username": "owner", "user_id": 1}
                            if self.helper.seen[-1]["headers"].get("x-machiya-session") == SID
                            or self.helper.seen[-1]["headers"].get("x-access-token") == "owner-hister-token"
                            else (401, "{}", "application/json"),
                            "POST /v1/signout": (204, "", "text/plain"),
                            "/v1/prefs": self.account_prefs, "PUT /v1/prefs": self.account_prefs})
        self.account = {"rev": 0, "prefs": {}}
        self.addCleanup(self.helper.close)

    def account_prefs(self):
        """The helper's /v1/prefs (0.4.0): the account's settings, for the caller's own credential only."""
        seen = self.helper.seen[-1]
        if seen["headers"].get("x-machiya-session") != SID:
            return (401, '{"reason":"signed-out"}', "application/json")
        if seen["method"] == "PUT":
            self.account["prefs"].update(json.loads(seen["body"])["prefs"])
            self.account["rev"] += 1
        elif seen["headers"].get("if-none-match") == '"%d"' % self.account["rev"]:
            return (304, b"", "application/json")
        return {"v": 1, "rev": self.account["rev"], "prefs": dict(self.account["prefs"]),
                "updated": {k: 1 for k in self.account["prefs"]}}

    def hister_env(self, **kw):
        return dict({"LANDING_AUTH": "hister", "LANDING_AUTH_URL": self.helper.url, "LANDING_BIND": "127.0.0.1",
                     "LANDING_AUTH_SIGNIN_URL": "https://hister.example.ts.net/machiya/signin",
                     "LANDING_HISTER_USERS": "owner", "LANDING_USERS": "owner@example.com",
                     "LANDING_PUBLIC_URL": "https://machiya.example.ts.net"}, **kw)

    def test_signed_in_page_settings_and_meta(self):
        base = self.serve(**self.hister_env())
        cookie = {"Cookie": "machiya_sso=" + SID, "Accept": "text/html"}
        code, _, body = self.get(base + "/", cookie)
        self.assertEqual(code, 200)
        self.assertIn('<meta name="machiya-signin" content="/signout">', body)
        self.assertIn('title="Signed in as owner"', body)                  # the header's person button
        self.assertNotIn("machiya-banner", body)
        code, _, body = self.get(base + "/settings", cookie)
        self.assertIn('<h2 id="account">Account</h2>', body)
        self.assertIn("Signed in as owner", body)
        self.assertIn('<form method="post" action="/signout"', body)
        self.assertEqual(self.get(base + "/status", cookie)[0], 200)

    def test_signed_out_goes_to_sign_in(self):
        base = self.serve(**self.hister_env())
        import http.client
        conn = http.client.HTTPConnection(urlsplit(base).netloc, timeout=10)
        conn.request("GET", "/status", headers={"Accept": "text/html"})
        r = conn.getresponse()
        r.read()
        self.assertEqual(r.status, 302)
        self.assertTrue(r.getheader("Location").startswith("https://hister.example.ts.net/machiya/signin"))
        self.assertIn("machiya.example.ts.net%2Fstatus", r.getheader("Location"))
        conn.close()
        code, _, body = self.get(base + "/api/status")
        self.assertEqual((code, json.loads(body)["error"]), (401, "sign in"))
        for open_path in ("/healthz", "/api/changelog", "/static/machiya.css"):
            self.assertEqual(self.get(base + open_path)[0], 200, open_path)    # open while signed out

    def test_fallback_when_sign_in_is_down(self):
        self.helper.close()
        self.helper = Fake({})                       # replaced so tearDown can close it
        base = self.serve(**self.hister_env(LANDING_AUTH_URL="http://127.0.0.1:9"))
        code, _, body = self.get(base + "/", {"Tailscale-User-Login": "owner@example.com", "Accept": "text/html"})
        self.assertEqual(code, 200)
        self.assertIn("machiya-banner", body)                              # signed in through the tailnet
        code, _, body = self.get(base + "/settings", {"Tailscale-User-Login": "owner@example.com", "Accept": "text/html"})
        self.assertIn("Hister&#x27;s sign-in is unavailable", body)
        self.assertNotIn('action="/signout"', body.split('id="account"')[1].split("</div>")[0])
        self.assertEqual(self.get(base + "/", {"Tailscale-User-Login": "someone@example.com"})[0], 403)

    def test_signout(self):
        base = self.serve(**self.hister_env())
        import http.client

        def post(origin):
            conn = http.client.HTTPConnection(urlsplit(base).netloc, timeout=10)
            conn.request("POST", "/signout", body=b"", headers={"Cookie": "machiya_sso=" + SID, "Origin": origin,
                                                               "Content-Length": "0"})
            r = conn.getresponse()
            r.read()
            conn.close()
            return r
        self.assertEqual(post("https://evil.example").status, 403)
        r = post("https://machiya.example.ts.net")
        self.assertEqual((r.status, r.getheader("Location")), (303, "/"))
        self.assertIn("machiya_sso=;", " ".join(v for k, v in r.getheaders() if k == "Set-Cookie"))
        self.assertTrue(any(x["path"] == "/v1/signout" for x in self.helper.seen))

    def one_connection(self, base, requests):
        """[(status, location, body)] for requests sent one after another down ONE kept-alive connection, as
        Tailscale Serve does with different people's requests."""
        import http.client
        conn = http.client.HTTPConnection(urlsplit(base).netloc, timeout=30)
        out = []
        for path, headers in requests:
            conn.request("GET", path, headers=headers)
            r = conn.getresponse()
            out.append((r.status, r.getheader("Location"), r.read().decode("utf-8", "replace")))
        conn.close()
        return out

    def test_keep_alive_never_reuses_an_answer(self):
        """0.3.1: an answer kept on the handler decided the next request on the same connection."""
        base = self.serve(**self.hister_env())
        page = {"Accept": "text/html"}
        got = self.one_connection(base, [
            ("/status", page),                                                         # signed out: to sign in
            ("/status", dict(page, Cookie="machiya_sso=" + SID)),                      # signed in: the page
            ("/status", page),                                                         # signed out again
            ("/api/status", {"X-Access-Token": "owner-hister-token"}),                 # the owner's Hister token
            ("/api/status", {"Authorization": "Bearer owner-hister-token"}),           # ... or as a Bearer
            ("/api/status", {}),                                                       # nobody: 401
        ])
        self.assertEqual([g[0] for g in got], [302, 200, 302, 200, 200, 401])
        self.assertNotIn("Everything", got[2][2])

    def test_helper_unreachable_by_name(self):
        """0.3.1: the helper's name doesn't resolve (its container is gone): the tailnet owner gets the page with the
        banner, nobody else gets in, and never a trip to sign in."""
        base = self.serve(**self.hister_env(LANDING_AUTH_URL="http://hister-login.invalid:8081"))
        page = {"Accept": "text/html"}
        got = self.one_connection(base, [
            ("/status", page),                                                         # no login: 503
            ("/status", dict(page, **{"Tailscale-User-Login": "owner@example.com"})),  # the owner: the page, banner
            ("/status", dict(page, Cookie="machiya_sso=" + SID, **{"Tailscale-User-Login": "owner@example.com"})),
            ("/status", dict(page, **{"Tailscale-User-Login": "someone@example.com"})),
            ("/api/status", {"X-Access-Token": "owner-hister-token"}),                 # can't be checked, no login: 503
        ])
        self.assertEqual([g[0] for g in got], [503, 200, 200, 403, 503])
        self.assertTrue(all(g[1] is None for g in got))                                # never sent to sign in
        self.assertIn("machiya-banner", got[1][2])
        self.assertIn("machiya-banner", got[2][2])

    def test_setup_refusals(self):
        for drop in ("LANDING_AUTH_SIGNIN_URL", "LANDING_HISTER_USERS", "LANDING_PUBLIC_URL"):
            env = self.hister_env()
            del env[drop]
            with self.assertRaises(SystemExit, msg=drop):
                landing.Config(env)
        with self.assertRaises(SystemExit):                                # the fallback trusts a header: a proxy only
            landing.Config(self.hister_env(LANDING_BIND="0.0.0.0"))
        landing.Config(self.hister_env(LANDING_BIND="0.0.0.0", LANDING_BIND_BEHIND_PROXY="1"))
        with self.assertRaises(SystemExit):
            landing.Config(self.hister_env(LANDING_HISTER_USERS="*"))

    def put_prefs(self, base, prefs, headers):
        import http.client
        body = json.dumps({"prefs": prefs}).encode()
        conn = http.client.HTTPConnection(urlsplit(base).netloc, timeout=10)
        conn.request("PUT", "/api/prefs", body=body, headers=dict(headers, **{"Content-Type": "application/json"}))
        r = conn.getresponse()
        out = r.read()
        conn.close()
        return r.status, json.loads(out or b"null")

    def test_prefs_follow_the_person(self):
        """0.4.0: in hister mode /api/prefs is the account's, forwarded to the helper's /v1/prefs with the caller's
        own credential (no local file involved)."""
        base = self.serve(**self.hister_env())
        cookie = {"Cookie": "machiya_sso=" + SID}
        code, _, body = self.get(base + "/api/prefs", cookie)
        self.assertEqual((code, json.loads(body)["prefs"], json.loads(body)["rev"]), (200, {}, 0))
        status, data = self.put_prefs(base, {"theme": "night"}, dict(cookie, Origin="https://machiya.example.ts.net"))
        self.assertEqual((status, data["prefs"]["theme"], data["rev"]), (200, "night", 1))
        self.assertEqual(self.helper.seen[-1]["path"], "/v1/prefs")
        self.assertEqual(self.helper.seen[-1]["headers"]["x-machiya-session"], SID)          # the caller's own
        self.assertEqual(self.put_prefs(base, {"theme": "day"}, dict(cookie, Origin="https://evil.example"))[0], 403)
        code, _, body = self.get(base + "/", dict(cookie, Accept="text/html"))
        self.assertIn('<meta name="machiya-prefs" content="/api/prefs">', body)

    def test_keep_alive_prefs_answer_each_request_alone(self):
        """0.4.0: prefs requests from different people down one kept-alive connection (as Serve sends them): each is
        forwarded with its own credential or refused; a refused PUT's unread body never becomes a request."""
        import http.client
        import socket
        base = self.serve(**self.hister_env())
        conn = http.client.HTTPConnection(urlsplit(base).netloc, timeout=10)
        body = json.dumps({"prefs": {"palette": "ayu"}}).encode()
        got = []
        for method, headers, data in (
                ("PUT", {"Cookie": "machiya_sso=" + SID, "Origin": "https://machiya.example.ts.net",
                         "Content-Type": "application/json"}, body),
                ("GET", {}, None),                                            # nobody: 401, not the owner's
                ("GET", {"Cookie": "machiya_sso=" + "mhs_" + "z" * 43}, None),  # signed out
                ("GET", {"Cookie": "machiya_sso=" + SID}, None),
                ("PUT", {"Cookie": "machiya_sso=" + SID, "Content-Type": "application/json"}, body)):  # no Origin
            conn.request(method, "/api/prefs", body=data, headers=headers)
            r = conn.getresponse()
            got.append((r.status, r.read()))
        conn.close()
        self.assertEqual([g[0] for g in got], [200, 401, 401, 200, 403])
        self.assertEqual(json.loads(got[3][1])["prefs"], {"palette": "ayu"})
        self.assertNotIn(b"ayu", got[1][1] + got[2][1])
        host, port = urlsplit(base).netloc.split(":")
        smuggled = b"GET /api/prefs HTTP/1.1\r\nHost: x\r\nCookie: machiya_sso=%s\r\n\r\n" % SID.encode()
        s = socket.create_connection((host, int(port)), timeout=2)
        s.sendall(b"PUT /api/prefs HTTP/1.1\r\nHost: x\r\nContent-Length: %d\r\n\r\n" % len(smuggled) + smuggled)
        out, closed = b"", False
        try:
            while True:
                chunk = s.recv(65536)
                if not chunk:
                    closed = True
                    break
                out += chunk
        except socket.timeout:
            pass
        s.close()
        self.assertEqual(out.count(b"HTTP/1.1 "), 1)
        self.assertTrue(closed)
        self.assertNotIn(b"ayu", out)

    def test_settings_shared_section_first(self):
        base = self.serve(**self.hister_env())
        cookie = {"Cookie": "machiya_sso=" + SID, "Accept": "text/html"}
        code, _, body = self.get(base + "/settings", cookie)
        heads = re.findall(r'<h2 id="([a-z-]+)">', body)
        self.assertEqual(heads, ["shared", "this-device", "account", "about"])
        self.assertIn("Follows you on every Machiya app when signed in.", body)
        self.assertIn('data-prefs-state="account"', body)
        self.assertIn("Signed in as owner. Saved to your account.", body)
        self.assertIn("Use This Device&#x27;s Size", body)

    def test_fallback_has_no_account_prefs(self):
        self.helper.close()
        self.helper = Fake({})
        base = self.serve(**self.hister_env(LANDING_AUTH_URL="http://127.0.0.1:9"))
        who = {"Tailscale-User-Login": "owner@example.com"}
        code, _, body = self.get(base + "/api/prefs", who)
        self.assertEqual(code, 503)
        code, _, body = self.get(base + "/settings", dict(who, Accept="text/html"))
        self.assertIn('data-prefs-state="unavailable"', body)
        self.assertIn("Sign-in is unavailable: kept here", body)

    def test_signout_stops_the_automatic_signin(self):
        """MACHIYA_SIGNIN_PROVIDER: signed out, a page goes through the provider; a deliberate sign-out sets this page's
        own marker (vaultkit 0.22: host-only; the helper remembers the ended session itself), so Sign Out doesn't
        bounce straight back in."""
        base = self.serve(**self.hister_env(MACHIYA_SIGNIN_PROVIDER="oidc"))
        import http.client
        conn = http.client.HTTPConnection(urlsplit(base).netloc, timeout=10)
        conn.request("GET", "/status", headers={"Accept": "text/html"})
        r = conn.getresponse()
        r.read()
        self.assertIn("provider=oidc&auto=1", r.getheader("Location"))
        conn.request("POST", "/signout", body=b"", headers={"Cookie": "machiya_sso=" + SID, "Content-Length": "0",
                                                           "Origin": "https://machiya.example.ts.net"})
        r = conn.getresponse()
        r.read()
        conn.close()
        self.assertIn("__Host-machiya_sso_landing_out=1", " ".join(v for k, v in r.getheaders() if k == "Set-Cookie"))


class Setup(unittest.TestCase):
    def test_header_mode_needs_loopback_or_proxy(self):
        with self.assertRaises(SystemExit):
            landing.Config({"LANDING_AUTH": "tailscale", "LANDING_BIND": "0.0.0.0"})
        landing.Config({"LANDING_AUTH": "tailscale", "LANDING_BIND": "0.0.0.0", "LANDING_BIND_BEHIND_PROXY": "1"})
        landing.Config({"LANDING_AUTH": "tailscale", "LANDING_BIND": "127.0.0.1"})

    def test_unknown_mode_and_identity_file(self):
        with self.assertRaises(SystemExit):
            landing.Config({"LANDING_AUTH": "header", "LANDING_BIND": "127.0.0.1"})      # header needs the file
        with self.assertRaises(SystemExit) as cm:
            landing.Config({"LANDING_AUTH": "open", "LANDING_BIND": "127.0.0.1", "MACHIYA_IDENTITY_FILE": "/x/identity.toml"})
        self.assertIn("identity", str(cm.exception))                                     # a missing file refuses

    def test_token_file(self):
        d = tempfile.mkdtemp(prefix="landing-test-")
        empty = os.path.join(d, "empty")
        open(empty, "w").close()
        with self.assertRaises(SystemExit):
            landing.Config({"LANDING_AUTH": "open", "LANDING_BIND": "127.0.0.1", "LANDING_TOKEN_FILE": empty})

    def test_vendored_vaultkit_is_untouched(self):
        self.assertEqual(verify.check(os.path.join(HERE, "..", "vaultkit")), [])


if __name__ == "__main__":
    unittest.main()
