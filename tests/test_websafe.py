"""vaultkit.websafe (v0.22, the sweep of 2026-10): response headers, header values, local redirects, the private-address
rule (the same as smallweb's) and the two openers. python3 -m unittest tests.test_websafe"""
import importlib.util
import os
import socket
import sys
import threading
import unittest
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
sys.path.insert(0, ROOT)

from vaultkit import histerauth, shell, websafe   # noqa: E402

ADDRESSES = ["127.0.0.1", "127.1.2.3", "10.0.0.1", "172.17.0.2", "192.168.1.1", "169.254.169.254", "100.64.0.1",
             "100.100.100.100", "0.0.0.0", "255.255.255.255", "224.0.0.1", "::1", "fe80::1", "fd7a:115c:a1e0::1",
             "::ffff:10.0.0.1", "64:ff9b::a00:1", "2002:a00:1::", "93.184.216.34", "1.1.1.1", "2606:4700::1111",
             "8.8.8.8", "::ffff:8.8.8.8"]
PUBLIC = {"93.184.216.34", "1.1.1.1", "2606:4700::1111", "8.8.8.8", "::ffff:8.8.8.8"}


class Headers(unittest.TestCase):
    def test_every_answer_gets_the_base_headers(self):
        h = dict(websafe.base_headers())
        self.assertEqual(h, {"X-Content-Type-Options": "nosniff", "X-Frame-Options": "SAMEORIGIN",
                             "Referrer-Policy": "same-origin"})
        self.assertEqual(dict(shell.security_headers())["X-Frame-Options"], "SAMEORIGIN")

    def test_a_vault_svg_is_sandboxed(self):
        """KURA-1, NIWA-1: an SVG from the vault ran script in the room's origin."""
        h = dict(websafe.asset_headers("Attachments/diagram.svg"))
        self.assertEqual(h["Content-Type"], "image/svg+xml")
        self.assertIn("sandbox", h["Content-Security-Policy"])
        self.assertIn("default-src 'none'", h["Content-Security-Policy"])
        self.assertNotIn("script-src", h["Content-Security-Policy"])
        self.assertEqual(h["X-Content-Type-Options"], "nosniff")
        self.assertNotIn("Content-Disposition", h)
        self.assertEqual(dict(websafe.asset_headers("a.PNG"))["Content-Type"], "image/png")

    def test_anything_else_is_a_download(self):
        for name in ("page.html", "x.xhtml", "notes.pdf", "noext", 'we"ird\r\n.html'):
            h = dict(websafe.asset_headers(name))
            self.assertEqual(h["Content-Type"], "application/octet-stream", name)
            self.assertTrue(h["Content-Disposition"].startswith("attachment;"), name)
            websafe.header_value(h["Content-Disposition"])

    def test_header_values_refuse_control_characters(self):
        """KONB-2: a CR/LF in a redirect became a Set-Cookie on the room's domain."""
        self.assertEqual(websafe.header_value("/garden/x"), "/garden/x")
        for bad in ("/x\r\nSet-Cookie: a=b", "/x\nb", "/x\x00", "a\x7f"):
            with self.assertRaises(ValueError):
                websafe.header_value(bad)

    def test_local_redirect_targets(self):
        self.assertEqual(websafe.location("/garden/x\r\nSet-Cookie: a=b"), "/garden/x%0D%0ASet-Cookie:%20a=b")
        self.assertEqual(websafe.location("/n/町家?q=a b"), "/n/%E7%94%BA%E5%AE%B6?q=a%20b")
        self.assertEqual(websafe.location("/p/x?a=1&b=%2F"), "/p/x?a=1&b=%2F")
        for bad in ("//evil.example/x", "/\\evil.example", "https://evil.example/", "javascript:x", "", None):
            self.assertEqual(websafe.location(bad), "/")

    def test_histerauth_refusals_carry_the_headers(self):
        auth = histerauth.HisterAuth("kura", "https://hister.t/machiya/signin", ["owner"], "https://kura.t", "",
                                     "tailscale", ["o@t"], "", True, None)
        result = histerauth.Result(status=401, signin="https://hister.t/machiya/signin")
        for is_page in (True, False):
            status, headers, _ = auth.respond(result, is_page=is_page)
            h = dict(headers)
            self.assertEqual((h["X-Content-Type-Options"], h["X-Frame-Options"]), ("nosniff", "SAMEORIGIN"))
            if is_page:
                self.assertIn("script-src 'self'", h["Content-Security-Policy"])


class Addresses(unittest.TestCase):
    def test_the_rule_matches_smallwebs(self):
        spec = importlib.util.spec_from_file_location("smallweb_web", os.path.join(ROOT, "stack", "smallweb", "web.py"))
        sys.path.insert(0, os.path.join(ROOT, "stack", "smallweb"))
        try:
            web = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(web)
        finally:
            sys.path.remove(os.path.join(ROOT, "stack", "smallweb"))
        for ip in ADDRESSES:
            self.assertEqual(websafe.private(ip), web.private(ip), ip)
            self.assertEqual(websafe.public_address(ip), ip in PUBLIC, ip)
        self.assertFalse(websafe.public_address("not an address"))

    def test_vet_resolves_and_checks_every_address(self):
        table = {"inside.test": ["10.1.2.3"], "outside.test": ["93.184.216.34"], "both.test": ["93.184.216.34", "127.0.0.1"]}
        old = websafe.resolve
        websafe.resolve = lambda host, port, **kw: [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (ip, port))
                                                   for ip in table.get(host, [])]
        try:
            self.assertEqual(websafe.vet("outside.test", 80), ["93.184.216.34"])
            for host in ("inside.test", "both.test", "nowhere.test", ""):
                with self.assertRaises(websafe.Blocked):
                    websafe.vet(host, 80)
            self.assertEqual(websafe.vet("inside.test", 80, allow=("inside.test",)), ["10.1.2.3"])
            self.assertEqual(websafe.vet("inside.test", 80, allow=("10.0.0.0/8",)), ["10.1.2.3"])
        finally:
            websafe.resolve = old


class Fake(BaseHTTPRequestHandler):
    seen = []

    def log_message(self, *a):
        pass

    def do_GET(self):
        Fake.seen.append((self.path, self.headers.get("X-Access-Token")))
        if self.path == "/to-inside":
            self.send_response(302)
            self.send_header("Location", "http://inside.test:%d/secret" % self.server.server_address[1])
        elif self.path == "/to-ok":
            self.send_response(302)
            self.send_header("Location", "/ok")
        elif self.path == "/to-file":
            self.send_response(302)
            self.send_header("Location", "file:///etc/passwd")
        else:
            self.send_response(200)
        self.send_header("Content-Length", "2")
        self.end_headers()
        self.wfile.write(b"ok")


class Openers(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), Fake)
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()
        cls.port = cls.server.server_address[1]
        cls.old = websafe.resolve
        table = {"site.test": "127.0.0.1", "inside.test": "127.0.0.2"}
        websafe.resolve = lambda host, port, **kw: [(socket.AF_INET, socket.SOCK_STREAM, 6, "",
                                                     (table.get(host, host), port))]

    @classmethod
    def tearDownClass(cls):
        websafe.resolve = cls.old
        cls.server.shutdown()
        cls.server.server_close()

    def url(self, path, host="site.test"):
        return "http://%s:%d%s" % (host, self.port, path)

    def test_private_addresses_are_never_fetched(self):
        with self.assertRaises(websafe.Blocked):
            websafe.public_opener().open(self.url("/ok"), timeout=5)

    def test_allowed_then_redirects_are_vetted_again(self):
        opener = websafe.public_opener(allow=("site.test",))
        with opener.open(self.url("/to-ok"), timeout=5) as r:
            self.assertEqual(r.read(), b"ok")
        with self.assertRaises(websafe.Blocked):                      # a redirect into a private address
            opener.open(self.url("/to-inside"), timeout=5)
        with self.assertRaises(urllib.error.HTTPError):               # off http(s)
            opener.open(self.url("/to-file"), timeout=5)

    def test_a_credential_never_follows_a_redirect(self):
        Fake.seen.clear()
        opener = websafe.public_opener(allow=("site.test", "inside.test"))
        req = urllib.request.Request(self.url("/to-inside"), headers={"X-Access-Token": "t0ken"})
        with self.assertRaises(urllib.error.HTTPError):
            opener.open(req, timeout=5)
        self.assertEqual(Fake.seen, [("/to-inside", "t0ken")])
        req = urllib.request.Request("http://127.0.0.1:%d/to-ok" % self.port, headers={"Authorization": "Bearer x"})
        with self.assertRaises(urllib.error.HTTPError) as cm:
            websafe.token_opener().open(req, timeout=5)
        self.assertEqual(cm.exception.code, 302)
        with websafe.token_opener().open("http://127.0.0.1:%d/ok" % self.port, timeout=5) as r:
            self.assertEqual(r.status, 200)


if __name__ == "__main__":
    unittest.main()
