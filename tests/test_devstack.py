"""The dev stack's own pieces (compose/dev): the front's routing, the fake NewsBlur's answers, the fixture sites, the
native plan built from compose.yml, and the synthetic-data rule. python3 -m unittest tests.test_devstack (pyyaml)."""
import importlib.machinery
import importlib.util
import json
import os
import re
import sys
import tempfile
import threading
import unittest
import urllib.request
from http.server import ThreadingHTTPServer

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
DEV = os.path.join(ROOT, "compose", "dev")


def load(name, path, env=None):
    old = dict(os.environ)
    os.environ.update(env or {})
    try:
        loader = importlib.machinery.SourceFileLoader(name, path)
        spec = importlib.util.spec_from_loader(name, loader)
        mod = importlib.util.module_from_spec(spec)
        loader.exec_module(mod)
        return mod
    finally:
        os.environ.clear()
        os.environ.update(old)


class Front(unittest.TestCase):
    def setUp(self):
        self.front = load("front", os.path.join(DEV, "front.py"))

    def test_routes_longest_prefix_and_exact(self):
        r = self.front.parse_routes("/=http://hister:4433 /machiya/=http://hister-login:8080 "
                                    "=/api/oauth/callback=http://hister-login:8080")
        pick = self.front.pick
        self.assertEqual(pick(r, "/machiya/signin?return=x"), ("hister-login", 8080))
        self.assertEqual(pick(r, "/api/oauth/callback?code=1"), ("hister-login", 8080))
        self.assertEqual(pick(r, "/api/oauth/callbackx"), ("hister", 4433))      # exact means exact
        self.assertEqual(pick(r, "/api/oauth?provider=oidc"), ("hister", 4433))
        self.assertEqual(pick(r, "/"), ("hister", 4433))

    def test_bad_route_refused(self):
        with self.assertRaises(SystemExit):
            self.front.parse_routes("machiya=http://x:1")
        with self.assertRaises(SystemExit):
            self.front.parse_routes("/=https://x:1")

    def test_compose_sites_parse(self):
        import yaml
        with open(os.path.join(DEV, "compose.yml")) as f:
            spec = yaml.safe_load(f)
        sites = json.loads(spec["services"]["front"]["environment"]["FRONT_SITES"])
        self.assertEqual(sorted(sites), [str(p) for p in range(19200, 19209)])
        hister = self.front.parse_routes(sites["19204"])
        self.assertEqual(self.front.pick(hister, "/machiya/healthz"), ("hister-login", 8080))
        self.assertEqual(self.front.pick(hister, "/mcp"), ("hister", 4433))


def serve(handler):
    srv = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, "http://127.0.0.1:%d" % srv.server_address[1]


def get(url, headers=None):
    req = urllib.request.Request(url, headers=headers or {})
    try:
        with urllib.request.urlopen(req, timeout=5) as r:
            return r.status, r.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()


class FakeNewsBlur(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        tok = os.path.join(cls.tmp.name, "token")
        with open(tok, "w") as f:
            f.write("devnb_test\n")
        cls.nb = load("fake_newsblur", os.path.join(DEV, "fake_newsblur.py"), {"NEWSBLUR_TOKEN_FILE": tok})
        os.environ["NEWSBLUR_TOKEN_FILE"] = tok          # token() reads it per request
        cls.nb.H.log_message = lambda *a: None
        cls.srv, cls.base = serve(cls.nb.H)
        cls.auth = {"Authorization": "Bearer devnb_test"}

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()
        os.environ.pop("NEWSBLUR_TOKEN_FILE", None)
        cls.tmp.cleanup()

    def j(self, path, headers=None):
        st, body = get(self.base + path, headers)
        return st, json.loads(body)

    def test_wrong_token_is_200_unauthenticated(self):
        self.assertEqual(self.j("/reader/feeds?flat=true"), (200, {"authenticated": False, "result": "ok"}))
        st, body = self.j("/reader/feeds?flat=true", {"Authorization": "Bearer nope"})
        self.assertIs(body["authenticated"], False)

    def test_read_pages_overlap_by_one(self):
        _, p1 = self.j("/reader/read_stories?page=1&limit=2&order=newest", self.auth)
        _, p2 = self.j("/reader/read_stories?page=2&limit=2&order=newest", self.auth)
        h1 = [s["story_hash"] for s in p1["stories"]]
        h2 = [s["story_hash"] for s in p2["stories"]]
        self.assertEqual(len(h1), 3)                           # limit + 1, as NewsBlur's inclusive LRANGE
        self.assertEqual(h1[-1], h2[0])

    def test_starred_hashes_and_stories(self):
        _, hashes = self.j("/reader/starred_story_hashes?include_timestamps=true", self.auth)
        pairs = hashes["starred_story_hashes"]
        self.assertEqual([p[1] for p in pairs], sorted((p[1] for p in pairs), reverse=True))
        q = "&".join("h=" + p[0] for p in pairs)
        _, full = self.j("/reader/starred_stories?" + q, self.auth)
        self.assertEqual(len(full["stories"]), len(pairs))
        self.assertTrue(all(s["user_tags"] and s["story_content"].startswith("<") for s in full["stories"]))

    def test_feed_import_reader_reads_it(self):
        sys.path.insert(0, os.path.join(ROOT, "stack", "feed-import"))
        try:
            newsblur = load("newsblur_reader", os.path.join(ROOT, "stack", "feed-import", "newsblur.py"))
            reader = newsblur.NewsBlur(self.base, "devnb_test", gap=0)
            read = list(reader.read(lambda h: False))
            starred = list(reader.starred(lambda h: False))
        finally:
            sys.path.pop(0)
        self.assertEqual(len(read), 3)
        self.assertEqual(len(starred), 2)
        self.assertTrue(all(e.url.startswith("http://workshop-journal.example/") for e in read + starred))
        self.assertEqual({e.feed for e in read}, {"Workshop Journal"})


class Fixtures(unittest.TestCase):
    def test_by_host_and_by_path(self):
        fx = load("fixture_site", os.path.join(DEV, "fixture_site.py"))
        self.assertTrue(fx.resolve("kyoto-guide.example", "/packing.html").endswith("kyoto-guide.example/packing.html"))
        self.assertTrue(fx.resolve("localhost:19209", "/kyoto-guide.example/").endswith("kyoto-guide.example/index.html"))
        self.assertIsNone(fx.resolve("kyoto-guide.example", "/../../../etc/passwd"))
        self.assertIsNone(fx.resolve("elsewhere.example", "/x"))


class NativePlan(unittest.TestCase):
    def test_every_service_has_a_native_plan(self):
        dev = load("devcli", os.path.join(DEV, "dev"))
        env = {"DEV_ENGINE": "native", "DEV_URL": "https://localhost", "DEV_HOST": "localhost", "DEV_BIND": "127.0.0.1",
               "DEV_DATA": "/d", "DEV_ROOT": DEV, "MACHIYA_SRC": ROOT, "KURA_SRC": "/s/kura", "NIWA_SRC": "/s/niwa",
               "KONBINI_SRC": "/s/konbini", "DEV_UID": "1000", "DEV_GID": "1000", "DEV_USERNS": "",
               "DEV_COOKIE_DOMAIN": "", "DEV_TAILNET_USERS": "owner@dev", "DEV_TLS_CERT": "/certs/tls.crt",
               "DEV_TLS_KEY": "/certs/tls.key"}
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        env["DEV_DATA"] = tmp.name
        os.makedirs(os.path.join(tmp.name, "secrets"))
        with open(os.path.join(tmp.name, "secrets", "hister.env"), "w") as f:
            f.write("HISTER__SERVER__OAUTH__OIDC__CLIENT_SECRET=x\n")
        plan = {p[0]: p for p in dev.native_plan(env)}
        self.assertEqual(plan["searxng"][4] and "optional" in plan["searxng"][4], True)
        kura = plan["kura"][3]
        self.assertEqual(kura["KURA_AUTH_URL"], "http://127.0.0.1:19236")
        self.assertEqual(kura["KURA_REPO_DIR"], tmp.name + "/mirror/vault")
        self.assertEqual(kura["KURA_VAULTS"], "personal=%s/mirror/vault#personal,team+shared:Team=%s/mirror/vault#team,"
                                              "work:Work=%s/mirror/vault#work" % ((tmp.name,) * 3))
        self.assertEqual(kura["KURA_PUBLIC_URL"], "https://localhost:19201")
        self.assertEqual(kura["KURA_HISTER_TOKEN_FILE"], tmp.name + "/secrets/owner-token")
        self.assertEqual(plan["kura"][2], "/s/kura/app")
        front = plan["front"][3]
        self.assertIn("http://127.0.0.1:19235", front["FRONT_SITES"])
        self.assertEqual(front["FRONT_TLS_CERT"], tmp.name + "/certs/tls.crt")
        self.assertEqual(plan["hister"][3]["HISTER__SERVER__OAUTH__OIDC__CLIENT_SECRET"], "x")
        ports = []
        for name, cmd, cwd, extra, skip in plan.values():
            if skip:
                continue
            for k, v in extra.items():
                self.assertNotRegex(v, r"//(hister|kura|niwa|konbini|landing|stub-idp|hister-login|machiya-mcp|"
                                       r"smallweb|searxng|fake-newsblur|fake-forgejo|fake-github|code-import|fixtures):", "%s %s" % (name, k))
                self.assertFalse(re.match(r"^/(data|vault|secrets|repo|certs|seed|mirror|feed)(/|$)", v),
                                 "%s %s=%s still a container path" % (name, k, v))
                if k.endswith("_PORT") and k != "NIWA_GOPHER_PUBLIC_PORT":
                    ports.append(v)
        self.assertEqual(len(ports), len(set(ports)), "two native services on one port: %s" % sorted(ports))

    def test_interpolate(self):
        dev = load("devcli2", os.path.join(DEV, "dev"))
        self.assertEqual(dev.interpolate("${A:-x}/${B-y}/${C}", {"A": "", "C": "c"}), "x/y/c")
        self.assertEqual(dev.interpolate("${A:-x}", {"A": "a"}), "a")


class SyntheticOnly(unittest.TestCase):
    """The seed is invented: no tailnet names, no real hosts, nothing that looks like a secret."""

    def test_seed_is_synthetic(self):
        bad = re.compile(r"ts\.net|hale-|server|owner|passkey|@gmail|@icloud|mch_|mhs_", re.I)
        for root, _, names in os.walk(os.path.join(DEV, "seed")):
            for n in names:
                with open(os.path.join(root, n), encoding="utf-8") as f:
                    text = f.read()
                self.assertIsNone(bad.search(text), os.path.join(root, n))
                for url in re.findall(r"https?://([a-z0-9.-]+)", text):
                    self.assertTrue(url.endswith(".example") or url == "localhost", url)


if __name__ == "__main__":
    unittest.main()
