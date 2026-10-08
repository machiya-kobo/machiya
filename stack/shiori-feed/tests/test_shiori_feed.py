"""shiori-feed's tests: no network. A fake Hister on 127.0.0.1 records what it is sent; shiori-feed runs on 127.0.0.1
too. Covers the feed itself, the gate in each mode, the start checks, the limits, redirects and the log.

Run, from stack/shiori-feed: python3 -m unittest discover -s tests
"""
import http.client
import io
import json
import os
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
from contextlib import redirect_stderr
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

import shiori_feed as sf                                        # noqa: E402

TOKEN = "hister-test-token"
DOCS = [
    {"url": "https://a.example/one", "title": "One & Only", "text": "First   page\ttext " * 60, "added": 1759000000,
     "label": "books"},
    {"url": "https://b.example/two", "title": "", "text": "bad \x01 char", "added": "x", "label": "vault"},
    {"url": "", "title": "no url"},
]


def serve(handler):
    srv = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, "http://127.0.0.1:%d" % srv.server_address[1]


class Hister(BaseHTTPRequestHandler):
    seen = []
    mode = "ok"

    def log_message(self, *a):
        pass

    def do_GET(self):
        Hister.seen.append((self.path, self.headers))
        if Hister.mode == "redirect":
            self.send_response(302)
            self.send_header("Location", "http://127.0.0.1:9/elsewhere")
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        if Hister.mode == "down":
            self.send_response(500)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        if self.headers.get("Origin") != "hister://":
            self.send_response(403)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        body = json.dumps({"documents": DOCS}).encode()
        if Hister.mode == "huge":
            body = b" " * (sf.HISTER_MAX + 10) + body
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def env(**kw):
    out = {"SHIORI_FEED_HISTER_URL": HISTER_URL, "SHIORI_FEED_AUTH": "open"}
    out.update({"SHIORI_FEED_" + k: v for k, v in kw.items()})
    return out


def get(base, path, headers=None, method="GET"):
    req = urllib.request.Request(base + path, headers=headers or {}, method=method)
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return r.status, dict(r.headers), r.read()
    except urllib.error.HTTPError as e:
        return e.code, dict(e.headers), e.read()


HISTER_SRV, HISTER_URL = serve(Hister)


class Base(unittest.TestCase):
    cfg_env = {}

    def setUp(self):
        Hister.seen, Hister.mode = [], "ok"
        self.err = io.StringIO()
        self._redir = redirect_stderr(self.err)
        self._redir.__enter__()
        self.start(env(**self.cfg_env))

    def start(self, e):
        if getattr(self, "srv", None):
            self.srv.shutdown()
            self.srv.server_close()
        self.cfg = sf.Config(e)
        self.srv = sf.make_server(self.cfg, "127.0.0.1", 0)
        threading.Thread(target=self.srv.serve_forever, args=(0.05,), daemon=True).start()
        self.base = "http://127.0.0.1:%d" % self.srv.server_address[1]

    def tearDown(self):
        self.srv.shutdown()
        self.srv.server_close()
        self._redir.__exit__(None, None, None)


class TestFeed(Base):
    def test_feed(self):
        code, headers, body = get(self.base, "/shiori/feed?q=label%3Abooks&title=Books&exclude_label=vault")
        self.assertEqual(code, 200)
        self.assertTrue(headers["Content-Type"].startswith("application/rss+xml"))
        self.assertIn("max-age=300", headers["Cache-Control"])
        root = ET.fromstring(body)                                  # well-formed XML, control characters dropped
        ch = root.find("channel")
        self.assertEqual(ch.findtext("title"), "Shiori – Books")
        self.assertEqual(ch.findtext("link"), HISTER_URL + "/?q=label%3Abooks")
        items = ch.findall("item")
        self.assertEqual(len(items), 1)                             # the vault one is dropped, the one with no url too
        self.assertEqual(items[0].findtext("title"), "One & Only")
        self.assertEqual(items[0].findtext("category"), "books")
        self.assertTrue(items[0].findtext("description").endswith("…"))
        self.assertIn("GMT", items[0].findtext("pubDate"))
        path, sent = Hister.seen[0]
        query = json.loads(parse_qs(urlsplit(path).query)["query"][0])
        self.assertEqual(query["text"], 'label:books -label:"vault" -metadata.source:code')
        self.assertEqual((query["sort"], query["limit"], query["include_text"]), ("date", 50, True))
        self.assertEqual(sent["Origin"], "hister://")
        self.assertIsNone(sent.get("X-Access-Token"))

    def test_short_paths_and_health(self):
        self.assertEqual(get(self.base, "/feed?q=x")[0], 200)
        self.assertEqual(get(self.base, "/shiori/healthz")[:3:2], (200, b"ok\n"))
        self.assertEqual(get(self.base, "/healthz")[0], 200)
        self.assertEqual(get(self.base, "/shiori/settings")[0], 404)
        self.assertEqual(get(self.base, "/shiori/feed?q=x", method="POST")[0], 405)

    def test_bad_requests(self):
        for path in ("/shiori/feed", "/shiori/feed?q=%20", "/shiori/feed?q=" + "a" * 501,
                     "/shiori/feed?q=x&title=" + "t" * 201, "/shiori/feed?q=x&exclude_label=" + "l" * 101,
                     "/shiori/feed?q=x" + "".join("&exclude_label=l%d" % i for i in range(21))):
            self.assertEqual(get(self.base, path)[0], 400, path)
        self.assertEqual(Hister.seen, [])

    def test_hister_fails(self):
        for mode in ("down", "huge"):
            Hister.mode = mode
            self.assertEqual(get(self.base, "/shiori/feed?q=x")[0], 502, mode)

    def test_log_has_no_query(self):
        get(self.base, "/shiori/feed?q=secret-words&title=hidden")
        get(self.base, "/shiori/feed?q=" + "a" * 600)
        log = self.err.getvalue()
        self.assertIn("GET /shiori/feed 200", log)
        self.assertNotIn("secret", log)
        self.assertNotIn("hidden", log)


class TestToken(Base):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.token = os.path.join(self.dir.name, "token")
        with open(self.token, "w") as f:
            f.write(TOKEN + "\n")
        self.cfg_env = {"HISTER_TOKEN_FILE": self.token}
        super().setUp()

    def tearDown(self):
        super().tearDown()
        self.dir.cleanup()

    def test_token_sent_and_never_logged(self):
        self.assertEqual(get(self.base, "/shiori/feed?q=x")[0], 200)
        self.assertEqual(Hister.seen[0][1]["X-Access-Token"], TOKEN)
        self.assertNotIn(TOKEN, self.err.getvalue())
        self.assertNotIn(TOKEN, repr(self.cfg.token))

    def test_rotated_token(self):
        with open(self.token, "w") as f:
            f.write("rotated-token-value\n")
        os.utime(self.token, ns=(1, 1))
        get(self.base, "/shiori/feed?q=x")
        self.assertEqual(Hister.seen[-1][1]["X-Access-Token"], "rotated-token-value")

    def test_redirect_never_followed(self):
        Hister.mode = "redirect"
        self.assertEqual(get(self.base, "/shiori/feed?q=x")[0], 502)
        self.assertEqual(len(Hister.seen), 1)                     # one request, and nothing went to the Location

    def test_bad_token_file_stops_the_start(self):
        empty = os.path.join(self.dir.name, "empty")
        open(empty, "w").close()
        for path in (empty, os.path.join(self.dir.name, "missing")):
            with self.assertRaises(SystemExit):
                sf.Config(env(HISTER_TOKEN_FILE=path))


class TestGate(Base):
    cfg_env = {"AUTH": "tailscale", "USERS": "you@example.com", "BIND": "127.0.0.1"}

    def test_tailscale_header(self):
        self.assertEqual(get(self.base, "/shiori/feed?q=x")[0], 403)
        self.assertEqual(get(self.base, "/shiori/feed?q=x", {"Tailscale-User-Login": "other@example.com"})[0], 403)
        self.assertEqual(get(self.base, "/shiori/feed?q=x", {"Tailscale-User-Login": "you@example.com"})[0], 200)
        self.assertEqual(get(self.base, "/shiori/healthz")[0], 200)     # the probe needs no login
        self.assertEqual(len(Hister.seen), 1)

    def test_duplicate_header_refused(self):
        conn = http.client.HTTPConnection("127.0.0.1", self.srv.server_address[1], timeout=5)
        conn.putrequest("GET", "/shiori/feed?q=x")
        conn.putheader("Tailscale-User-Login", "you@example.com")
        conn.putheader("Tailscale-User-Login", "other@example.com")
        conn.endheaders()
        self.assertEqual(conn.getresponse().status, 403)
        conn.close()

    def test_trusted_proxies(self):
        self.start(env(AUTH="tailscale", USERS="you@example.com", BIND="0.0.0.0", TRUSTED_PROXIES="192.0.2.1/32"))
        self.assertEqual(get(self.base, "/shiori/feed?q=x", {"Tailscale-User-Login": "you@example.com"})[0], 403)
        self.start(env(AUTH="tailscale", USERS="you@example.com", BIND="0.0.0.0", TRUSTED_PROXIES="127.0.0.0/8"))
        self.assertEqual(get(self.base, "/shiori/feed?q=x", {"Tailscale-User-Login": "you@example.com"})[0], 200)

    def test_proxy_mode(self):
        self.start(env(AUTH="proxy", BIND="0.0.0.0", TRUSTED_PROXIES="192.0.2.1"))
        self.assertEqual(get(self.base, "/shiori/feed?q=x")[0], 403)
        self.start(env(AUTH="proxy", BIND="0.0.0.0", TRUSTED_PROXIES="127.0.0.1,::1"))
        self.assertEqual(get(self.base, "/shiori/feed?q=x")[0], 200)

    def test_start_checks(self):
        for bad in (dict(AUTH="open", BIND="0.0.0.0"),                      # no gate off loopback
                    dict(AUTH="tailscale", BIND="0.0.0.0"),                 # a header anyone could send
                    dict(AUTH="proxy"),                                     # whose sign-in?
                    dict(AUTH="nobody"),
                    dict(AUTH="open", HISTER_URL="hister:4433"),
                    dict(AUTH="proxy", TRUSTED_PROXIES="not-an-address")):
            with self.assertRaises(SystemExit, msg=bad):
                sf.Config(env(**bad))
        for good in (dict(AUTH="open"), dict(AUTH="open", BIND="0.0.0.0", BIND_BEHIND_PROXY="1"),
                     dict(AUTH="tailscale", BIND="0.0.0.0", BIND_BEHIND_PROXY="1"),
                     dict(AUTH="tailscale", BIND="0.0.0.0", TRUSTED_PROXIES="172.31.250.2/32")):
            sf.Config(env(**good))
        self.assertEqual(sf.Config({"SHIORI_FEED_HISTER_URL": HISTER_URL}).auth, "tailscale")    # the default


class TestRate(Base):
    cfg_env = {"PER_MINUTE": "2"}

    def test_per_minute(self):
        self.assertEqual(get(self.base, "/shiori/feed?q=a")[0], 200)
        self.assertEqual(get(self.base, "/shiori/feed?q=b")[0], 200)
        code, headers, _ = get(self.base, "/shiori/feed?q=c")
        self.assertEqual(code, 429)
        self.assertGreater(int(headers["Retry-After"]), 0)
        self.assertEqual(len(Hister.seen), 2)

    def test_limiter_window(self):
        now = [0.0]
        lim = sf.Limiter(1, clock=lambda: now[0])
        self.assertEqual(lim.take(), 0)
        self.assertEqual(lim.take(), 61)
        now[0] = 60.5
        self.assertEqual(lim.take(), 0)


if __name__ == "__main__":
    unittest.main()


# -- 0.2.0: room tokens, /api/status, /api/changelog ------------------------------------------------------------------

GOOD = "mht_" + "A" * 43            # a room token the fake helper confirms
OTHER = "mht_" + "B" * 43           # another room's token: refused


class Login(BaseHTTPRequestHandler):
    """A fake hister-login: GET /v1/check confirms GOOD for the feed's own origin only."""
    seen = []

    def log_message(self, *a):
        pass

    def do_GET(self):
        Login.seen.append((self.path, self.headers.get("X-Machiya-Room")))
        ok = self.path == "/v1/check" and self.headers.get("X-Machiya-Session") == GOOD \
            and self.headers.get("X-Machiya-Room") == "https://feed.example.ts.net"
        body = json.dumps({"kind": "token", "username": "you"} if ok else {"error": "no"}).encode()
        self.send_response(200 if ok else 401)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


LOGIN_SRV, LOGIN_URL = serve(Login)
TOKENS = dict(AUTH_URL=LOGIN_URL, PUBLIC_URL="https://feed.example.ts.net/", HISTER_USERS="you")


class TestRoomTokens(Base):
    cfg_env = dict(AUTH="tailscale", USERS="you@example.com", BIND="127.0.0.1", **TOKENS)

    def test_a_good_token_passes(self):
        self.assertEqual(get(self.base, "/shiori/feed?q=x", {"Authorization": "Bearer " + GOOD})[0], 200)
        self.assertEqual(get(self.base, "/shiori/feed?q=x", {"X-Machiya-Token": GOOD})[0], 200)
        self.assertEqual(Login.seen[-1][1], "https://feed.example.ts.net")     # asked for its own origin

    def test_a_token_decides_alone(self):
        # another room's token is refused even with a good Tailscale login beside it
        self.assertEqual(get(self.base, "/shiori/feed?q=x", {"Authorization": "Bearer " + OTHER,
                                                             "Tailscale-User-Login": "you@example.com"})[0], 403)
        for bad in ({"Authorization": "Bearer mht_short"}, {"X-Machiya-Token": "nope"},
                    {"Authorization": "Bearer " + GOOD, "X-Machiya-Token": GOOD}):
            self.assertEqual(get(self.base, "/shiori/feed?q=x", bad)[0], 403, bad)
        # a non-room Authorization header is no token: the login decides, as before
        self.assertEqual(get(self.base, "/shiori/feed?q=x", {"Authorization": "Basic eDp5",
                                                             "Tailscale-User-Login": "you@example.com"})[0], 200)

    def test_cached_and_never_logged(self):
        Login.seen = []
        for _ in range(3):
            self.assertEqual(get(self.base, "/shiori/feed?q=x", {"Authorization": "Bearer " + GOOD})[0], 200)
        self.assertEqual(len(Login.seen), 1)                    # a good answer is kept 30 s
        self.assertNotIn("mht_", self.err.getvalue())

    def test_proxy_mode_takes_tokens_from_anywhere(self):
        self.start(env(AUTH="proxy", BIND="0.0.0.0", TRUSTED_PROXIES="192.0.2.1", **TOKENS))
        self.assertEqual(get(self.base, "/shiori/feed?q=x")[0], 403)
        self.assertEqual(get(self.base, "/shiori/feed?q=x", {"Authorization": "Bearer " + GOOD})[0], 200)

    def test_without_auth_url_a_token_is_refused(self):
        self.start(env(AUTH="tailscale", USERS="*", BIND="127.0.0.1"))
        self.assertEqual(get(self.base, "/shiori/feed?q=x")[0], 200)
        self.assertEqual(get(self.base, "/shiori/feed?q=x", {"Authorization": "Bearer " + GOOD})[0], 403)

    def test_start_checks(self):
        for bad in (dict(AUTH_URL=LOGIN_URL, HISTER_USERS="you"),                       # no public URL
                    dict(AUTH_URL=LOGIN_URL, PUBLIC_URL="https://f.example"),           # nobody to act as
                    dict(AUTH_URL=LOGIN_URL, PUBLIC_URL="https://f.example", HISTER_USERS="*"),
                    dict(AUTH_URL="hister-login:8081", PUBLIC_URL="https://f.example", HISTER_USERS="you")):
            with self.assertRaises(SystemExit, msg=bad):
                sf.Config(env(**bad))
        self.assertIsNone(sf.Config(env()).room_tokens)


class TestStatusAndChangelog(Base):
    cfg_env = {"AUTH": "tailscale", "USERS": "you@example.com", "BIND": "127.0.0.1"}

    def test_status_is_open_and_counts_failures(self):
        for path in ("/shiori/api/status", "/api/status"):
            code, headers, body = get(self.base, path)
            self.assertEqual(code, 200)
            self.assertTrue(headers["Content-Type"].startswith("application/json"))
            st = json.loads(body)
            self.assertEqual((st["ok"], st["ready"], st["error"], st["version"], st["auth"], st["room_tokens"]),
                             (True, True, None, sf.VERSION, "tailscale", False))
        Hister.mode = "down"
        for i in range(sf.HISTER_FAILS):
            self.assertEqual(get(self.base, "/shiori/feed?q=secret", {"Tailscale-User-Login": "you@example.com"})[0], 502)
        st = json.loads(get(self.base, "/api/status")[2])
        self.assertFalse(st["ok"])
        self.assertIn("HTTPError", st["error"].replace("HTTP 500", "HTTPError"))
        self.assertNotIn("secret", json.dumps(st))
        Hister.mode = "ok"
        self.assertEqual(get(self.base, "/shiori/feed?q=x", {"Tailscale-User-Login": "you@example.com"})[0], 200)
        st = json.loads(get(self.base, "/api/status")[2])
        self.assertTrue(st["ok"])
        self.assertIsNotNone(st["hister"]["last_ok"])
        self.assertNotIn("/api/status", self.err.getvalue())    # the probe isn't logged

    def test_changelog(self):
        code, headers, body = get(self.base, "/shiori/api/changelog")
        self.assertEqual(code, 200)
        self.assertTrue(headers["Content-Type"].startswith("text/markdown"))
        self.assertIn(b"## " + sf.VERSION.encode(), body)
        self.assertEqual(get(self.base, "/api/changelog", {"If-None-Match": headers["ETag"]})[0], 304)
        self.assertEqual(get(self.base, "/api/changelog", method="POST")[0], 405)
