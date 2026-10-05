"""smallweb's tests: the parsers on the engines' response formats (with invented content), the renderers, and the
whole gateway against local fakes: two Gemini engines and a capsule (TLS), a Gopher engine and hole, a web server (http
and https) for http(s) saves, a SOCKS5 proxy (which records the names or addresses it is asked for) and Hister (which
records /api/add). The web server is on 127.0.0.1, which smallweb never fetches unless SMALLWEB_FETCH_ALLOW says so:
the tests allow 127.0.0.1/32 and map invented names to addresses with a patched resolver.

The test certificates are throwaway self-signed ones for 127.0.0.1 fakes, made with `openssl` when the tests start and
deleted afterwards (nothing secret is committed; `old` is valid for one day, so it is always about to expire).
Run, from stack/smallweb (needs openssl): python3 -m unittest discover -s tests
"""
import atexit
import contextlib
import io
import json
import os
import shutil
import subprocess
import socket
import socketserver
import ssl
import struct
import sys
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

CERTS = tempfile.mkdtemp(prefix="smallweb-test-certs-")
atexit.register(shutil.rmtree, CERTS, True)
for _name, _cn, _days in (("a", "capsule.test", 36500), ("b", "capsule.test", 36500), ("old", "old.test", 1)):
    subprocess.run(["openssl", "req", "-x509", "-newkey", "ec", "-pkeyopt", "ec_paramgen_curve:prime256v1", "-nodes",
                    "-keyout", os.path.join(CERTS, "test-%s.key" % _name), "-out", os.path.join(CERTS, "test-%s.crt" % _name),
                    "-subj", "/CN=" + _cn, "-days", str(_days)], check=True, capture_output=True)
subprocess.run(["openssl", "req", "-x509", "-newkey", "ec", "-pkeyopt", "ec_paramgen_curve:prime256v1", "-nodes",
                "-keyout", os.path.join(CERTS, "test-web.key"), "-out", os.path.join(CERTS, "test-web.crt"),
                "-subj", "/CN=web.test", "-addext", "subjectAltName=DNS:web.test", "-days", "36500"],
               check=True, capture_output=True)

TLGS_PAGE = """# TLGS
=> / 🏠 Home
=> /search 🔍 Search
=> gemini://capsule-a.test/notes Example Page (text/gemini, 9KB)
* gemini://capsule-a.test/notes
an [example] snippet with some words around the match …
=> gemini://tardis.test/archive/gemini/x/capsule-a.test%2Fnotes 📦 Archived page
=> gemini://only.tlgs.test/page A <Page> & "quotes" (text/gemini, 1KB)
* gemini://only.tlgs.test/page
[example] only on TLGS
Page 1 of 94 (937 results).
=> /search/2?example+query ➡️ Next Page
"""
KENNEDY_PAGE = """=> gemini://wiki.test/cgi-bin/wp.cgi/view?Example+(topic) Gemipedia Article: Example (topic)
Showing 1 - 15 of 121 results
=> gemini://capsule-a.test/notes/ 1. Example Page (text/gemini • 82 Lines • 8.90 KB)
* capsule-a.test › notes
Language: French
>…Some Author - Experimenting With [Example] (web link) My [example] [query]
=> /page-info?gemini%3a%2f%2fcapsule-a.test ℹ️ More Info / Archived Copy
=> gemini://kennedy.only.test/x 2. Kennedy only (text/gemini • 3 Lines • 1 KB)
* kennedy.only.test › x
>just [this]
=> /search/p:2/?example%20query ➡️ Next Page
"""
VERONICA_PAGE = ("iFirst 30 selectors displayed (page 1).\t/v2/\terror.host\t1\r\n"
                 "iApproximately 700 total matches.\t/v2/\terror.host\t1\r\n"
                 "1Veronica-2 home\t/v2/\tgopher.floodgap.com\t70\r\n"
                 "1~example      2001-Jan-01 12:00   -\t/~example/\thole-b.test\t70\r\n"
                 "igopher://hole-b.test:70/1/~example/\t/v2/\terror.host\t1\r\n"
                 "0Example Post\tsub:news:12345\tbbs-c.test\t70\r\n"
                 "igopher://bbs-c.test:70/0sub:news:12345\t/v2/\terror.host\t1\r\n"
                 "1Next 30 matches\t/v2/vs?example query forward=30\tgopher.floodgap.com\t70\r\n.\r\n")
CAPSULE = {
    "/": "20 text/gemini\r\n# Hello <capsule>\nSome text & more.\n=> /two Second page\n=> gopher://hole.test:7070/0/readme.txt A text\n"
         "=> https://example.com/ The web\n=> spartan://x.test/ Spartan\n```ascii\n<pre> & art\n```\n* item\n> quote\n",
    "/two": "20 text/gemini\r\n# Two\nsecond\n",
    "/moved": "31 /two\r\n",
    "/ask": "10 Your name?\r\n",
    "/ask?Ann%20B": "20 text/gemini\r\n# Hi Ann B\n",
    "/busy": "44 60\r\n",
    "/secret": "60 Client certificate required\r\n",
    "/view": "20 text/gemini\r\n# View\n",
    "/view?Some+Article": "20 text/gemini\r\n# Some Article\nA page with a query that isn't a prompt answer.\n",
    "/saveme": "20 text/gemini\r\n# Save me\nOpened in a Gemini app.\n",
    "/robots.txt": "20 text/plain\r\nUser-agent: webproxy\nDisallow: /private\n",
    "/private/x": "20 text/gemini\r\n# should not be fetched\n",
}
HOLE = {
    "": "iWelcome to the hole\t\terror.host\t1\r\n0Readme\t/readme.txt\thole.test\t7070\r\n7Search here\t/find\thole.test\t7070\r\n"
        "hWeb link\tURL:https://example.com/\thole.test\t7070\r\n.\r\n",
    "/readme.txt": "Plain <text> in a hole.\r\n.\r\n",
    "/find\tcats": "0Cats\t/cats.txt\thole.test\t7070\r\n.\r\n",
}


class Fakes:
    """Gemini (TLS) and Gopher fake servers plus a SOCKS5h proxy that maps every name to 127.0.0.1."""
    resolved, gemini_requests, cert = [], [], "a"

    @classmethod
    def gemini_server(cls, pages):
        class H(socketserver.StreamRequestHandler):
            def handle(self):
                ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
                ctx.load_cert_chain(os.path.join(CERTS, "test-%s.crt" % Fakes.cert), os.path.join(CERTS, "test-%s.key" % Fakes.cert))
                try:
                    t = ctx.wrap_socket(self.request, server_side=True)
                    line = b""
                    while not line.endswith(b"\r\n"):
                        chunk = t.recv(1)
                        if not chunk:
                            return
                        line += chunk
                    u = urllib.parse.urlsplit(line.decode().strip())
                    Fakes.gemini_requests.append(line.decode().strip())
                    key = u.path + ("?" + u.query if u.query else "")
                    t.sendall(pages.get(key, pages.get(u.path + "?*", "51 Not found\r\n")).encode())
                    t.close()
                except (ssl.SSLError, OSError):
                    pass
        s = socketserver.ThreadingTCPServer(("127.0.0.1", 0), H)
        s.daemon_threads = True
        threading.Thread(target=s.serve_forever, daemon=True).start()
        return s.server_address[1]

    @classmethod
    def gopher_server(cls, pages):
        class H(socketserver.StreamRequestHandler):
            def handle(self):
                line = self.rfile.readline().decode().rstrip("\r\n")
                self.wfile.write(pages.get(line, "3Not found\t\terror.host\t1\r\n.\r\n").encode())
        s = socketserver.ThreadingTCPServer(("127.0.0.1", 0), H)
        s.daemon_threads = True
        threading.Thread(target=s.serve_forever, daemon=True).start()
        return s.server_address[1]

    @classmethod
    def socks_server(cls):
        class H(socketserver.BaseRequestHandler):
            def handle(self):
                c = self.request
                c.recv(3)
                c.sendall(b"\x05\x00")
                head = c.recv(4)
                if head[3] == 1:
                    name = socket.inet_ntop(socket.AF_INET, c.recv(4))
                elif head[3] == 4:
                    name = socket.inet_ntop(socket.AF_INET6, c.recv(16))
                else:
                    name = c.recv(c.recv(1)[0]).decode()
                port = struct.unpack(">H", c.recv(2))[0]
                Fakes.resolved.append("%s:%d" % (name, port))
                try:
                    up = socket.create_connection(("127.0.0.1", port), timeout=5)
                except OSError:
                    c.sendall(b"\x05\x05\x00\x01" + b"\x00" * 6)
                    return
                c.sendall(b"\x05\x00\x00\x01" + b"\x7f\x00\x00\x01" + struct.pack(">H", port))

                def pipe(a, b):
                    try:
                        while True:
                            d = a.recv(65536)
                            if not d:
                                break
                            b.sendall(d)
                    except OSError:
                        pass
                    finally:
                        try:
                            b.shutdown(socket.SHUT_WR)
                        except OSError:
                            pass
                with up:
                    t = threading.Thread(target=pipe, args=(up, c), daemon=True)
                    t.start()
                    pipe(c, up)
                    t.join(10)
        s = socketserver.ThreadingTCPServer(("127.0.0.1", 0), H)
        s.daemon_threads = True
        threading.Thread(target=s.serve_forever, daemon=True).start()
        return s.server_address[1]


WEB_REQUESTS = []                                 # (path, Host, User-Agent) for every request the web fake answers
BIG = b"<html><body>" + b"x" * (6 << 20) + b"</body></html>"
WEB_PAGES = {
    "/ok": (200, {"Content-Type": "text/html; charset=utf-8"},
            b"<!DOCTYPE html><html><head><title>A  Web Page</title><script>alert(1)</script><style>p{}</style></head>"
            b"<body onload=\"x()\"><h1>Heading</h1><p>Hello <b>web</b> &amp; friends.</p><noscript>no js</noscript>"
            b"<iframe src=\"https://ads.example/\">frame</iframe><object data=\"x.swf\">obj</object><embed src=\"e\">"
            b"<a href=\"javascript:alert(1)\" onclick=\"y()\">link</a><svg><text>drawn</text></svg>"
            b"<template><p>later</p></template><!-- [if IE]><script>z()</script><![endif] --></body></html>"),
    "/og": (200, {"Content-Type": "text/html"}, b'<meta property="og:title" content="From OG"><p>body</p>'),
    "/h1": (200, {"Content-Type": "application/xhtml+xml"}, b"<html><body><h1>Body  Title</h1><p>x</p></body></html>"),
    "/untitled": (200, {"Content-Type": "text/html"}, b"<p>no title anywhere</p>"),
    "/plain": (200, {"Content-Type": "text/plain"}, b"Just   text.\n\nTwo lines <b>."),
    "/latin1": (200, {"Content-Type": "text/html; charset=iso-8859-1"}, "<title>Caf\u00e9</title>".encode("latin-1")),
    "/meta-charset": (200, {"Content-Type": "text/html"},
                      '<meta charset="windows-1252"><title>\u201cQuoted\u201d</title>'.encode("cp1252")),
    "/bad-utf8": (200, {"Content-Type": "text/html"}, b"<title>ok \xff</title>"),
    "/image": (200, {"Content-Type": "image/png"}, b"\x89PNG...."),
    "/notype": (200, {}, b"<p>?</p>"),
    "/big": (200, {"Content-Type": "text/html"}, BIG),
    "/big-nolength": (200, {"Content-Type": "text/html", "Content-Length": None}, BIG),
    "/missing": (404, {"Content-Type": "text/html"}, b"gone"),
    "/to-ok": (301, {"Location": "/ok"}, b""),
    "/to-inside": (302, {"Location": "http://inside.test/secret"}, b""),
    "/to-private-ip": (302, {"Location": "http://10.1.2.3/"}, b""),
    "/to-vault": (302, {"Location": "/%76/work/n/x"}, b""),
    "/to-file": (302, {"Location": "file:///etc/passwd"}, b""),
    "/loop": (302, {"Location": "/loop"}, b""),
    "/v/work/n/x": (200, {"Content-Type": "text/html"}, b"<title>private</title>"),
    "/skipme": (200, {"Content-Type": "text/html"}, b"<title>Skip me</title><p>Hister refuses this one.</p>"),
}


class WebFake(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_GET(self):
        WEB_REQUESTS.append((self.path, self.headers.get("Host"), self.headers.get("User-Agent")))
        status, headers, body = WEB_PAGES.get(self.path.split("?", 1)[0], (404, {}, b""))
        self.send_response(status)
        headers = dict({"Content-Length": str(len(body))}, **headers)
        for k, v in headers.items():
            if v is not None:
                self.send_header(k, v)
        self.end_headers()
        try:
            for i in range(0, len(body), 1 << 16):
                self.wfile.write(body[i:i + (1 << 16)])
        except OSError:
            pass                                                     # the client stopped reading: the cap


def web_server(tls=False):
    s = ThreadingHTTPServer(("127.0.0.1", 0), WebFake)
    s.daemon_threads = True
    if tls:
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        ctx.load_cert_chain(os.path.join(CERTS, "test-web.crt"), os.path.join(CERTS, "test-web.key"))
        s.socket = ctx.wrap_socket(s.socket, server_side=True)
    threading.Thread(target=s.serve_forever, daemon=True).start()
    return s.server_address[1]


# invented names -> addresses (the fake resolver smallweb is given)
NAMES = {"web.test": ["127.0.0.1"], "other.test": ["127.0.0.1"], "inside.test": ["10.0.0.5"],
         "mixed.test": ["93.184.216.34", "192.168.1.1"], "mapped.test": ["::ffff:127.0.0.1"]}
RESOLVED = []


def fake_resolve(host, port, type=0, **kw):
    RESOLVED.append(host)
    if host in NAMES:
        return [(socket.AF_INET6 if ":" in ip else socket.AF_INET, socket.SOCK_STREAM, 6, "", (ip, port))
                for ip in NAMES[host]]
    if host.replace(".", "").isdigit():
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (host, port))]
    if host.endswith(".test") and host != "nowhere.test":      # the gemini and gopher fakes (all on 127.0.0.1)
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", port))]
    raise socket.gaierror("no such name: %s" % host)


HISTER_DOCS = []
HISTER_TOKENS = []                               # the X-Access-Token each save carried (None: none)


class HisterFake(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_POST(self):
        doc = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        HISTER_DOCS.append((self.headers.get("Origin"), doc))
        HISTER_TOKENS.append(self.headers.get("X-Access-Token"))
        self.send_response(406 if "/skipme" in doc["url"] else 201)          # 406: one of Hister's skip rules
        self.send_header("Content-Length", "0")
        self.end_headers()


GEM_PAGES, GOPHER_PAGES = {}, {}                  # filled below, once the ports are known
GEM_PORT = Fakes.gemini_server(GEM_PAGES)
GOPHER_PORT = Fakes.gopher_server(GOPHER_PAGES)
SOCKS_PORT = Fakes.socks_server()
hister = ThreadingHTTPServer(("127.0.0.1", 0), HisterFake)
threading.Thread(target=hister.serve_forever, daemon=True).start()

DATA = tempfile.mkdtemp()
TOKEN = "SmallwebHisterToken0123456789"
with open(os.path.join(DATA, "hister.token"), "w") as _f:
    _f.write(TOKEN + "\n")
os.environ.update(SMALLWEB_HISTER_TOKEN_FILE=os.path.join(DATA, "hister.token"), SMALLWEB_DATA=DATA, SMALLWEB_USERS="user@test", SMALLWEB_SOCKS="socks5h://127.0.0.1:%d" % SOCKS_PORT,
                  SMALLWEB_HISTER_URL="http://127.0.0.1:%d" % hister.server_address[1], SMALLWEB_PER_HOUR="100",
                  SMALLWEB_PUBLIC_URL="https://smallweb.test", SMALLWEB_ORIGINS="https://shiori.test/",
                  SMALLWEB_FETCH_ALLOW="127.0.0.1/32")
os.environ.pop("SMALLWEB_AUTH", None)

import engines   # noqa: E402
import render    # noqa: E402
import smolnet   # noqa: E402
import smallweb  # noqa: E402

engines.ENGINES.update({"tlgs": {"scheme": "gemini", "host": "tlgs.test", "port": GEM_PORT},
                        "kennedy": {"scheme": "gemini", "host": "kennedy.test", "port": GEM_PORT},
                        "veronica": {"scheme": "gopher", "host": "veronica.test", "port": GOPHER_PORT}})
smallweb.polite.gaps = {"tlgs.test": 0.2, "kennedy.test": 0.2, "veronica.test": 0.2}
HOLE_URL = "gopher://hole.test:%d/1" % GOPHER_PORT
CAP = "gemini://capsule.test:%d" % GEM_PORT
for k, v in list(HOLE.items()):                                       # the hole's menu links to itself on GOPHER_PORT
    HOLE[k] = v.replace("hole.test\t7070", "hole.test\t%d" % GOPHER_PORT)
CAPSULE["/"] = CAPSULE["/"].replace("hole.test:7070", "hole.test:%d" % GOPHER_PORT)
GEM_PAGES.update({"/search?example%20query": "20 text/gemini\r\n" + TLGS_PAGE, "/search?slow": "44 30\r\n", **CAPSULE})
GOPHER_PAGES.update({**HOLE, "/v2/vs\texample query": VERONICA_PAGE})

import web       # noqa: E402

WEB_PORT, WEBS_PORT = web_server(), web_server(tls=True)
web.resolve = fake_resolve
web.CAFILE = os.path.join(CERTS, "test-web.crt")
smallweb.web_polite.default = 0.05
WEB = "http://web.test:%d" % WEB_PORT

SERVER = ThreadingHTTPServer(("127.0.0.1", 0), smallweb.Handler)
threading.Thread(target=SERVER.serve_forever, daemon=True).start()
BASE = "http://127.0.0.1:%d" % SERVER.server_address[1]


def get(path, user="user@test", data=None, headers=None):
    h = {"Tailscale-User-Login": user} if user else {}
    h.update(headers or {})
    req = urllib.request.Request(BASE + path, data=data, headers=h)

    class NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, *a, **k):
            return None
    try:
        with urllib.request.build_opener(NoRedirect).open(req, timeout=30) as r:
            return r.status, dict(r.headers), r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as ex:
        return ex.code, dict(ex.headers), ex.read().decode("utf-8", "replace")


def page(url, **kw):
    return get("/page?url=" + urllib.parse.quote(url, safe=""), **kw)


def wait_for(cond, timeout=5):
    end = time.time() + timeout
    while time.time() < end:
        if cond():
            return True
        time.sleep(0.05)
    return False


class Parsers(unittest.TestCase):
    def test_tlgs(self):
        hits, total, nxt = engines.parse_tlgs(TLGS_PAGE)
        self.assertEqual((len(hits), total, nxt), (2, 937, True))
        h = hits[0]
        self.assertEqual(h["url"], "gemini://capsule-a.test/notes")
        self.assertEqual((h["title"], h["kind"], h["size"]), ("Example Page", "text/gemini", "9KB"))
        self.assertEqual(h["snippet"], "an example snippet with some words around the match …")
        a, b = h["marks"][0]
        self.assertEqual(h["snippet"][a:b], "example")
        self.assertTrue(h["archive_url"].startswith("gemini://tardis.test/archive/"))

    def test_kennedy(self):
        hits, total, nxt = engines.parse_kennedy(KENNEDY_PAGE)
        self.assertEqual((len(hits), total, nxt), (2, 121, True))            # the Gemipedia box is skipped
        h = hits[0]
        self.assertEqual((h["title"], h["kind"], h["size"]), ("Example Page", "text/gemini", "8.90 KB"))
        self.assertEqual(h["snippet"], "Some Author - Experimenting With Example (web link) My example query")
        self.assertEqual([h["snippet"][a:b] for a, b in h["marks"]], ["Example", "example", "query"])

    def test_marks_are_only_the_engines_highlights(self):
        # the page's own brackets (a Markdown link here) aren't highlights; stems are
        text = 'update [Example Link]({{}} "Example Link") I ended up using [example] sync with [querying]'
        snip, marks = engines.unbracket(text, engines.terms_of("Example query"))
        self.assertTrue(snip.startswith('update [Example Link]({{}}'))
        self.assertEqual([snip[a:b] for a, b in marks], ["example", "querying"])
        snip, marks = engines.unbracket("see [1] and [café] ok", engines.terms_of("café"))
        self.assertEqual([snip[a:b] for a, b in marks], ["café"])                  # code-point offsets
        self.assertIn("[1]", snip)

    def test_veronica(self):
        hits, total, nxt = engines.parse_veronica(VERONICA_PAGE)
        self.assertEqual((total, nxt), (700, True))
        self.assertEqual([h["url"] for h in hits], ["gopher://hole-b.test/1/~example/", "gopher://bbs-c.test/0sub:news:12345"])
        self.assertEqual((hits[1]["kind"], hits[1]["snippet"]), ("text", "text · bbs-c.test › sub:news:12345"))
        self.assertEqual(hits[0]["title"], "~example")                             # the listing's date columns go

    def test_merge_dedupes_on_the_normalised_url(self):
        merged = engines.merge([("tlgs", engines.parse_tlgs(TLGS_PAGE)[0]), ("kennedy", engines.parse_kennedy(KENNEDY_PAGE)[0])])
        self.assertEqual(len(merged), 3)
        self.assertEqual(merged[0]["sources"], ["tlgs", "kennedy"])             # trailing slash doesn't split it
        self.assertEqual([m["source"] for m in merged], ["tlgs", "tlgs", "kennedy"])

    def test_urls(self):
        self.assertEqual(smolnet.canonical("GEMINI://Host.Test:1965"), "gemini://host.test/")
        self.assertEqual(smolnet.canonical("gopher://h.test:70/0a#b c"), "gopher://h.test/0a%23b%20c")
        self.assertEqual(smolnet.gopher_parts("gopher://h.test:7070/7/find%09cats"), ("h.test", 7070, "7", "/find", "cats"))
        self.assertEqual(smolnet.gopher_parts("gopher://h.test"), ("h.test", 70, "1", "", ""))
        with self.assertRaises(ValueError):
            smolnet.canonical("https://example.com/")

    def test_cert_expiry(self):
        import ssl as _ssl
        for name in ("a", "old"):
            with open(os.path.join(CERTS, "test-%s.crt" % name)) as f:
                der = _ssl.PEM_cert_to_DER_cert(f.read())
            na = smolnet.cert_not_after(der)
            self.assertTrue(na > time.time() + 86400 * 365 * 50 if name == "a" else 0 < na < time.time() + 2 * 86400)


class Renderers(unittest.TestCase):
    def test_gemtext_escapes_and_rewrites(self):
        title, html, plain = render.gemtext(CAPSULE["/"].split("\r\n", 1)[1], "gemini://capsule.test/")
        self.assertEqual(title, "Hello <capsule>")
        self.assertIn("<h1>Hello &lt;capsule&gt;</h1>", html)
        self.assertIn('href="/page?url=gemini%3A%2F%2Fcapsule.test%2Ftwo"', html)
        self.assertIn('href="https://example.com/" rel="noreferrer noopener"', html)
        self.assertIn('<span class="other" title="spartan://x.test/">Spartan</span>', html)
        self.assertIn("<pre>&lt;pre&gt; &amp; art</pre>", html)
        self.assertNotIn("<script", html)

    def test_gophermap(self):
        title, html, plain = render.gophermap(HOLE[""], "hole.test", GOPHER_PORT)
        self.assertEqual(title, "Welcome to the hole")
        self.assertIn('name="q"', html)                                      # a type-7 item is a search form
        self.assertIn('href="https://example.com/"', html)                   # an h URL: item goes direct


class Web(unittest.TestCase):
    """http(s) fetching for saves: the URL rules, the address checks, the fetch limits and reading the page."""
    ALLOW = web.parse_allow("127.0.0.1/32")
    UA = "smallweb/test"

    def fetch(self, url, allow=None, proxy=""):
        return web.fetch(web.check(url, allow or self.ALLOW), allow or self.ALLOW, proxy, smolnet.Polite(), self.UA)

    def test_canonical(self):
        self.assertEqual(web.canonical("HTTPS://Example.COM:443/a%20b?q=é#frag"), "https://example.com/a%20b?q=%C3%A9")
        self.assertEqual(web.canonical("http://example.com:80"), "http://example.com/")
        self.assertEqual(web.canonical("http://example.com:8080/x"), "http://example.com:8080/x")
        self.assertEqual(web.canonical("http://[::1]:80/"), "http://[::1]/")
        self.assertEqual(web.canonical("https://bücher.example/"), "https://xn--bcher-kva.example/")
        for bad in ("ftp://example.com/", "gemini://example.com/", "http://user@example.com/",
                    "http://user:pw@example.com/", "http:///x", "https://example.com/" + "a" * 2048, "javascript:alert(1)",
                    "http://exa mple.com/", "http://example.com:99999/"):
            with self.assertRaises(web.Refused, msg=bad):
                web.canonical(bad)

    def test_private_ranges(self):
        for ip in ("127.0.0.1", "10.1.2.3", "172.16.0.1", "192.168.1.1", "169.254.169.254", "224.0.0.1", "240.0.0.1",
                   "0.0.0.0", "0.1.2.3", "255.255.255.255", "100.64.0.1", "100.127.255.254", "::1", "::", "fe80::1",
                   "fc00::1", "fd12:3456::1", "ff02::1", "::ffff:127.0.0.1", "::ffff:10.0.0.1", "64:ff9b::a00:1",
                   "2002:7f00:1::"):
            self.assertTrue(web.private(ip), ip)
        for ip in ("93.184.216.34", "1.1.1.1", "100.128.0.1", "2606:4700:4700::1111", "2001:4860:4860::8888"):
            self.assertFalse(web.private(ip), ip)

    def test_allow_setting(self):
        names, nets = web.parse_allow(" Intranet.Example , 10.0.0.0/8,127.0.0.1, fd00::/8 ")
        self.assertEqual(names, {"intranet.example"})
        self.assertEqual([str(n) for n in nets], ["10.0.0.0/8", "127.0.0.1/32", "fd00::/8"])
        self.assertEqual(web.parse_allow(""), (set(), []))
        for bad in ("10.0.0.0/33", "http://x", "a b"):
            with self.assertRaises(SystemExit, msg=bad):
                web.parse_allow(bad)

    def test_vault_paths(self):
        for path in ("/v/work/n/x", "/v/work/", "/v/work", "//v/work/n/x", "/%76/work/n/x", "/%2576/work/", "/V/work/x"):
            self.assertTrue(web.vault_path(path), path)
        for path in ("/", "/n/x", "/vault/x", "/v", "/a/v/work/"):
            self.assertFalse(web.vault_path(path), path)

    def test_checks_before_fetching(self):
        n = len(WEB_REQUESTS)
        with self.assertRaises(web.PrivateAddress):                     # an IP literal in a private range: never fetched
            web.check("http://127.0.0.1:%d/ok" % WEB_PORT, web.parse_allow(""))
        with self.assertRaises(web.PrivateAddress):
            web.check("http://[::ffff:7f00:1]/", web.parse_allow(""))
        with self.assertRaises(web.Blocked):                            # a private vault's address, even when allowed
            web.check(WEB + "/v/work/n/x", self.ALLOW)
        self.assertEqual(len(WEB_REQUESTS), n)
        self.assertEqual(web.check("http://127.0.0.1:%d/ok" % WEB_PORT, self.ALLOW), "http://127.0.0.1:%d/ok" % WEB_PORT)

    def test_names_resolving_to_private_addresses(self):
        n = len(WEB_REQUESTS)
        for host in ("inside.test", "mixed.test", "mapped.test"):          # ANY private address refuses the save
            with self.assertRaises(web.Blocked, msg=host):
                self.fetch("http://%s:%d/ok" % (host, WEB_PORT))
        with self.assertRaises(web.Blocked):                              # unset: nothing private
            self.fetch(WEB + "/ok", allow=web.parse_allow(""))
        self.assertEqual(len(WEB_REQUESTS), n)
        r = self.fetch(WEB + "/ok", allow=web.parse_allow("web.test"))    # a host name in the setting
        self.assertEqual(r.status, 200)

    def test_fetch_sends_the_real_host_to_the_vetted_address(self):
        Fakes.resolved.clear()
        WEB_REQUESTS.clear()
        r = self.fetch(WEB + "/ok", proxy="127.0.0.1:%d" % SOCKS_PORT)
        self.assertEqual((r.status, r.mime, r.charset), (200, "text/html", "utf-8"))
        self.assertEqual(WEB_REQUESTS, [("/ok", "web.test:%d" % WEB_PORT, self.UA)])
        self.assertEqual(Fakes.resolved, ["127.0.0.1:%d" % WEB_PORT])     # the proxy got the vetted IP, not the name

    def test_https_checks_the_certificate_against_the_name(self):
        r = self.fetch("https://web.test:%d/ok" % WEBS_PORT)
        self.assertEqual((r.status, r.url), (200, "https://web.test:%d/ok" % WEBS_PORT))
        with self.assertRaisesRegex(web.WebError, "TLS"):
            self.fetch("https://other.test:%d/ok" % WEBS_PORT)

    def test_redirects_are_checked_again(self):
        self.assertEqual(self.fetch(WEB + "/to-ok").url, WEB + "/ok")
        for path, err in (("/to-inside", web.Blocked), ("/to-private-ip", web.Blocked), ("/to-vault", web.Blocked),
                          ("/to-file", web.WebError)):
            with self.assertRaises(err, msg=path):
                self.fetch(WEB + path)
        WEB_REQUESTS.clear()
        with self.assertRaisesRegex(web.WebError, "redirects"):
            self.fetch(WEB + "/loop")
        self.assertEqual(len(WEB_REQUESTS), web.MAX_REDIRECTS + 1)

    def test_limits(self):
        for path in ("/big", "/big-nolength"):
            with self.assertRaisesRegex(web.WebError, "larger than 5 MB", msg=path):
                self.fetch(WEB + path)
        for path in ("/image", "/notype"):
            with self.assertRaisesRegex(web.WebError, "not saved", msg=path):
                self.fetch(WEB + path)
        with self.assertRaisesRegex(web.WebError, "HTTP 404"):
            self.fetch(WEB + "/missing")

    def test_reading_a_page(self):
        r = self.fetch(WEB + "/ok")
        title, text, doc = web.read_page(r.body, r.mime, r.charset, r.url, "Asked Title")
        self.assertEqual(title, "A Web Page")
        self.assertEqual(text, "Heading\nHello web & friends.\nlink")
        for gone in ("<script", "alert", "<style", "<iframe", "<object", "<embed", "onload", "onclick", "javascript:",
                     "z()"):
            self.assertNotIn(gone, doc)
        self.assertIn("<p>Hello <b>web</b> &amp; friends.</p>", doc)
        self.assertNotIn("<svg", doc)                                    # 0.3.1: an allowlist (MACH-F-7)
        self.assertNotIn("drawn", doc)

    def test_the_stored_html_is_an_allowlist(self):
        """MACH-F-7 (sweep 2026-10): these passed the old blocklist."""
        page = ('<p>keep <a href="https://ok.example/" title="t" style="x" class="c">ok</a> <a href="/rel">rel</a> '
                '<a href="gemini://cap.example/">gem</a></p><iframe srcdoc="<script>alert(1)</script>"/>'
                '<script/>bad()<svg><animate attributeName="href" to="javascript:alert(2)"/></svg>'
                '<a href="data:text/html,<script>alert(3)</script>">d</a><img src="data:image/svg+xml,x" alt="i">'
                '<a href=" jav&#x09;ascript:alert(4)">j</a><form action="https://x.example/"><input name="p">'
                '<button>Go</button></form><math><mi>m</mi></math><base href="https://evil.example/">'
                '<link rel="stylesheet" href="https://evil.example/x.css"><meta http-equiv="refresh" content="0;url=x">'
                '<custom-el onclick="y()">text stays</custom-el><table><tr><td colspan="2">cell</td></tr></table>')
        _, _, doc = web.read_page(page.encode(), "text/html", "utf-8", "https://site.example/")
        for gone in ("srcdoc", "<iframe", "<script", "alert", "<svg", "<animate", "data:", "javascript", "jav",
                     "<form", "<input", "<button", "<math", "<base", "<link", "<meta", "style=", "class=",
                     "onclick", "<custom-el"):
            self.assertNotIn(gone, doc, gone)
        for kept in ('<a href="https://ok.example/" title="t">ok</a>', '<a href="/rel">rel</a>',
                     '<a href="gemini://cap.example/">gem</a>', '<img alt="i">', "text stays",
                     '<td colspan="2">cell</td>'):
            self.assertIn(kept, doc, kept)

        def title_of(path, asked=""):
            r = self.fetch(WEB + path)
            return web.read_page(r.body, r.mime, r.charset, r.url, asked)[0]
        self.assertEqual(title_of("/og"), "From OG")
        self.assertEqual(title_of("/h1"), "Body Title")
        self.assertEqual(title_of("/untitled", "Asked"), "Asked")
        self.assertEqual(title_of("/untitled"), WEB + "/untitled")
        self.assertEqual(title_of("/latin1"), "Café")                                  # the header's charset
        self.assertEqual(title_of("/meta-charset"), "\u201cQuoted\u201d")             # <meta charset>
        self.assertEqual(title_of("/bad-utf8"), "ok \ufffd")                          # else UTF-8, replaced
        r = self.fetch(WEB + "/plain")
        title, text, doc = web.read_page(r.body, r.mime, r.charset, r.url, "")
        self.assertEqual((title, text), (WEB + "/plain", "Just text.\nTwo lines <b>."))
        self.assertIn("<pre>Just   text.\n\nTwo lines &lt;b&gt;.</pre>", doc)


class Gateway(unittest.TestCase):
    def test_users_only_and_status_open(self):
        self.assertEqual(get("/api/search?q=x", user=None)[0], 403)
        self.assertEqual(page(CAP + "/", user="other@test")[0], 403)
        code, _, body = get("/api/status", user=None)
        self.assertEqual(code, 200)
        d = json.loads(body)
        self.assertEqual((d["auth"], d["ready"], d["error"]), ("tailscale", True, None))

    def test_changelog_is_open_markdown_with_etag_and_304(self):
        code, headers, body = get("/api/changelog", user=None)                 # open, like /api/status
        self.assertEqual(code, 200)
        self.assertEqual(headers["Content-Type"], "text/markdown; charset=utf-8")
        with open(smallweb.CHANGELOG_FILE, encoding="utf-8") as f:
            self.assertEqual(body, f.read())
        self.assertEqual(get("/api/changelog", user=None, headers={"If-None-Match": headers["ETag"]})[::2], (304, ""))
        self.assertEqual(get("/api/changelog", user=None, headers={"If-None-Match": '"x"'})[0], 200)
        saved = smallweb.CHANGELOG_FILE
        smallweb.CHANGELOG_FILE = saved + ".missing"
        try:
            self.assertEqual(get("/api/changelog", user=None)[0], 404)
        finally:
            smallweb.CHANGELOG_FILE = saved

    def test_status_reports_real_breakage_only(self):
        smallweb.hister_state["fails"] = 3
        try:
            d = json.loads(get("/api/status", user=None)[2])
            self.assertTrue(d["error"].startswith("Hister saves failing"))
            self.assertFalse(d["ok"])
        finally:
            smallweb.hister_state["fails"] = 0
        old = smallweb.SOCKS
        smallweb.SOCKS = "127.0.0.1:1"                       # nothing listens there: the proxy itself is down
        try:
            for _ in range(2):
                self.assertIn("Couldn", page("gemini://down.test/")[2])
            self.assertIn("SOCKS proxy", json.loads(get("/api/status", user=None)[2])["error"])
        finally:
            smallweb.SOCKS = old
            smolnet.PROXY.update(fails=0, error=None)
        self.assertIsNone(json.loads(get("/api/status", user=None)[2])["error"])

    def test_search_contract(self):
        Fakes.resolved.clear()
        code, _, body = get("/api/search?q=example+query&source=tlgs,veronica")
        self.assertEqual(code, 200)
        d = json.loads(body)
        self.assertEqual((d["query"], d["page"], d["errors"]), ("example query", 1, {}))
        first = d["results"][0]
        self.assertEqual(set(first), {"title", "url", "proxy_url", "snippet", "marks", "source", "sources", "scheme",
                                      "kind", "size", "archive_url"})
        self.assertEqual(first["proxy_url"], "https://smallweb.test/page?url=" + urllib.parse.quote(first["url"], safe=""))
        self.assertEqual([r["source"] for r in d["results"][:2]], ["tlgs", "veronica"])   # interleaved in source order
        self.assertEqual(d["sources"]["tlgs"]["total"], 937)
        self.assertTrue({"tlgs.test:%d" % GEM_PORT, "veronica.test:%d" % GOPHER_PORT} <= set(Fakes.resolved))  # socks5h
        # the same search again comes from the cache, without touching the network
        Fakes.resolved.clear()
        d2 = json.loads(get("/api/search?q=Example%20%20query&source=tlgs,veronica")[2])
        self.assertTrue(d2["sources"]["tlgs"]["cached"])
        self.assertEqual(Fakes.resolved, [])

    def test_one_engine_failing_never_fails_the_search(self):
        code, _, body = get("/api/search?q=slow&source=tlgs,veronica")
        d = json.loads(body)
        self.assertEqual(code, 200)
        self.assertEqual(d["errors"].get("tlgs"), "slow-down")
        self.assertFalse(d["sources"]["tlgs"]["ok"])
        self.assertEqual(json.loads(get("/api/search?q=slow2&source=tlgs")[2])["errors"]["tlgs"], "slow-down")  # backing off
        smallweb.polite.until.clear()

    def test_search_is_the_star(self):
        _, _, body = get("/")
        self.assertIn('<form class="find big"', body)                           # the one big field, first
        self.assertLess(body.index('class="find big"'), body.index('class="open-address"'))
        _, _, body = get("/?q=example+query&source=tlgs&source=veronica")     # the engine checkboxes
        self.assertIn('value="tlgs" checked', body)
        self.assertNotIn('value="kennedy" checked', body)
        self.assertIn('value="example query"', body)
        _, _, body = page(CAP + "/two")                                          # a rendered page: address as a line
        self.assertIn('<p class="addr"><code class="url">gemini://capsule.test:%d/two</code>' % GEM_PORT, body)
        self.assertIn('<header class="top"><a class="brand" href="/">smallweb</a><form class="find"', body)

    def test_bad_requests(self):
        for q in ("", "q=", "q=x&source=quarry", "q=x&page=0", "q=" + "x" * 301):
            self.assertEqual(get("/api/search?" + q)[0], 400, q)

    def test_page_renders_and_saves_to_hister(self):
        HISTER_DOCS.clear()
        code, headers, body = page(CAP + "/")
        self.assertEqual(code, 200)
        self.assertTrue(headers["Content-Security-Policy"].startswith("default-src 'none'"))
        self.assertIn("<h1>Hello &lt;capsule&gt;</h1>", body)
        self.assertNotIn("<script", body)
        self.assertTrue(wait_for(lambda: HISTER_DOCS))
        origin, doc = HISTER_DOCS[0]
        self.assertEqual(origin, "hister://")
        self.assertEqual(doc["url"], "gemini://capsule.test:%d/" % GEM_PORT)          # canonical, not the proxy URL
        self.assertNotIn("label", doc)
        self.assertEqual(doc["metadata"]["source"], "smallweb")
        self.assertEqual(doc["metadata"]["smallweb_scheme"], "gemini")
        self.assertEqual(len(doc["metadata"]["smallweb_cert_sha256"]), 64)
        self.assertIn("Some text & more.", doc["text"])
        # unchanged: not sent again
        time.sleep(0.3)
        smallweb.store.q("DELETE FROM cache WHERE kind='page'")
        page(CAP + "/")
        time.sleep(0.5)
        self.assertEqual(len(HISTER_DOCS), 1)

    def test_prompts_redirects_and_errors(self):
        HISTER_DOCS.clear()
        self.assertIn('name="q"', page(CAP + "/ask")[2])
        code, _, body = get("/page?url=%s&q=%s" % (urllib.parse.quote(CAP + "/ask", safe=""), "Ann%20B"))
        self.assertIn("Hi Ann B", body)
        self.assertIn("<h1>Two</h1>", page(CAP + "/moved")[2])
        self.assertIn("Client Certificate", page(CAP + "/secret")[2])
        self.assertIn("Not for Proxies", page(CAP + "/private/x")[2])            # robots.txt, webproxy
        self.assertNotIn("/private/x", " ".join(Fakes.gemini_requests))
        self.assertEqual(page("https://example.com/")[0], 400)
        self.assertEqual(page(CAP + "/busy")[0], 429)                            # 44: back off, and don't even ask
        n = len(Fakes.gemini_requests)
        self.assertEqual(page(CAP + "/two")[0], 429)
        self.assertEqual(len(Fakes.gemini_requests), n)
        smallweb.polite.until.clear()
        time.sleep(0.5)
        self.assertNotIn("gemini://capsule.test:%d/ask?Ann%%20B" % GEM_PORT, [d["url"] for _, d in HISTER_DOCS])

    def test_query_urls_saved_unless_they_answer_a_prompt(self):
        HISTER_DOCS.clear()
        self.assertIn("Hi Ann B", page(CAP + "/ask?Ann%20B")[2])               # a link straight to a prompt's answer
        self.assertIn("Some Article", page(CAP + "/view?Some+Article")[2])
        self.assertTrue(wait_for(lambda: any("/view?" in d["url"] for _, d in HISTER_DOCS)))
        time.sleep(0.3)
        self.assertFalse(any("/ask?" in d["url"] for _, d in HISTER_DOCS))

    def test_api_save(self):
        HISTER_DOCS.clear()
        body = json.dumps({"url": CAP + "/saveme"}).encode()
        js = {"Content-Type": "application/json"}
        self.assertEqual(get("/api/save", data=body, headers=dict(js, Origin="https://evil.test"))[0], 403)
        self.assertEqual(get("/api/save", data=body, headers=js)[0], 403)                 # no Origin at all
        self.assertEqual(get("/api/save", data=body, headers=dict(js, Origin="https://shiori.test"))[0], 202)
        self.assertTrue(wait_for(lambda: any(d["url"].endswith("/saveme") for _, d in HISTER_DOCS)))
        code, _, out = get("/api/save", data=json.dumps({"url": CAP + "/two"}).encode(), headers=dict(js, Origin="hister://"))
        self.assertEqual((code, json.loads(out)["url"]), (202, "gemini://capsule.test:%d/two" % GEM_PORT))
        self.assertEqual(get("/api/save", data=b'{"url": "ftp://example.com/"}', headers=dict(js, Origin="hister://"))[0], 400)
        self.assertEqual(get("/api/save", data=body, user="other@test", headers=dict(js, Origin="hister://"))[0], 403)
        n = len(HISTER_DOCS)
        get("/api/save", data=json.dumps({"url": CAP + "/ask?Ann%20B"}).encode(), headers=dict(js, Origin="hister://"))
        time.sleep(0.5)
        self.assertEqual(len([d for _, d in HISTER_DOCS if "/ask?" in d["url"]]), 0)   # the never-save rules hold

    # -- http(s) saves ---------------------------------------------------------------------------------------------

    def save(self, url, origin="hister://", wait=True, **extra):
        code, _, out = get("/api/save", data=json.dumps(dict(extra, url=url)).encode(),
                           headers={"Content-Type": "application/json", "Origin": origin})
        if wait:
            self.assertTrue(wait_for(lambda: smallweb.save_state["pending"] == 0, 15))
        return code, json.loads(out)

    def docs_for(self, fragment):
        return [d for _, d in HISTER_DOCS if fragment in d["url"]]

    def assert_not_saved(self, url, why):
        smallweb.hister_state["last_error"] = None
        n, saved = len(HISTER_DOCS), smallweb.store.save_count()
        code, out = self.save(url)
        self.assertEqual(code, 202, url)
        self.assertIn(why, smallweb.hister_state["last_error"] or "", url)
        self.assertEqual((len(HISTER_DOCS), smallweb.store.save_count()), (n, saved), url)
        status = json.loads(get("/api/status", user=None)[2])
        self.assertEqual((status["error"], status["hister"]["queued"]), (None, 0))   # not a failure

    def test_api_save_http_page(self):
        HISTER_DOCS.clear()
        WEB_REQUESTS.clear()
        saved = smallweb.store.save_count()
        code, out = self.save("HTTP://Web.Test:%d/ok?t=1#top" % WEB_PORT, origin="https://shiori.test")
        self.assertEqual((code, out), (202, {"queued": True, "url": WEB + "/ok?t=1"}))
        origin, doc = HISTER_DOCS[0]
        self.assertEqual(origin, "hister://")
        self.assertEqual((doc["url"], doc["title"]), (WEB + "/ok?t=1", "A Web Page"))
        self.assertNotIn("label", doc)
        self.assertEqual(doc["text"], "Heading\nHello web & friends.\nlink")
        self.assertNotIn("<script", doc["html"])
        self.assertNotIn("onclick", doc["html"])
        self.assertIn("<p>Hello <b>web</b> &amp; friends.</p>", doc["html"])
        meta = doc["metadata"]
        self.assertEqual({k: meta[k] for k in ("source", "smallweb_scheme", "smallweb_mime")},
                         {"source": "smallweb", "smallweb_scheme": "http", "smallweb_mime": "text/html"})
        self.assertLess(abs(meta["smallweb_fetched"] - time.time()), 60)
        self.assertNotIn("smallweb_cert_sha256", meta)
        self.assertEqual(WEB_REQUESTS, [("/ok?t=1", "web.test:%d" % WEB_PORT,
                                         "smallweb/%s (Machiya; saves a page its owner asked for)" % smallweb.VERSION)])
        self.assertEqual(smallweb.store.save_count(), saved + 1)
        # the dedupe: the same body again isn't sent again
        self.assertEqual(self.save(WEB + "/ok?t=1")[0], 202)
        self.assertEqual(len(self.docs_for("/ok?t=1")), 1)
        # https, a form body, and the asked title used only when the page has none; saved under the final URL
        code, _, out = get("/api/save", data=urllib.parse.urlencode({"url": "https://web.test:%d/untitled" % WEBS_PORT,
                                                                      "title": "Asked  Title"}).encode(),
                           headers={"Origin": "hister://"})
        self.assertEqual(code, 202)
        self.assertTrue(wait_for(lambda: self.docs_for("/untitled")))
        doc = self.docs_for("/untitled")[0]
        self.assertEqual((doc["title"], doc["metadata"]["smallweb_scheme"]), ("Asked Title", "https"))
        self.assertEqual(self.save(WEB + "/to-ok", title="Ignored")[0], 202)
        self.assertEqual(self.docs_for(WEB + "/ok")[-1]["title"], "A Web Page")
        self.assertFalse(self.docs_for("/to-ok"))
        self.assertEqual(self.save(WEB + "/latin1")[0], 202)                           # charset decoding
        self.assertEqual(self.docs_for("/latin1")[0]["title"], "Café")

    def test_api_save_http_refusals(self):
        WEB_REQUESTS.clear()
        for url in ("http://user@web.test/ok", "http://user:pw@web.test/ok", "https://web.test/" + "a" * 2048,
                    "data:text/html,x", "http:///ok", "http://[::1/", "http://10.0.0.1/", "http://[::1]/", "http://169.254.169.254/x",
                    WEB + "/v/work/n/x", WEB + "/%76/work/n/x", WEB + "//v/work/n/x"):
            code, out = self.save(url, wait=False)
            self.assertEqual(code, 400, url)
            self.assertIn("error", out)
        old = smallweb.FETCH_ALLOW
        smallweb.FETCH_ALLOW = web.parse_allow("")                                       # the default: nothing private
        try:
            self.assertEqual(self.save("http://127.0.0.1:%d/ok" % WEB_PORT, wait=False)[0], 400)
            self.assert_not_saved(WEB + "/ok?t=allow", "blocked: private address")
        finally:
            smallweb.FETCH_ALLOW = old
        self.assertEqual(WEB_REQUESTS, [])                                               # nothing was fetched
        # the Origin rule is unchanged
        self.assertEqual(self.save(WEB + "/ok", origin="https://evil.test", wait=False)[0], 403)
        code, _, _ = get("/api/save", data=json.dumps({"url": WEB + "/ok"}).encode(),
                         headers={"Content-Type": "application/json"})
        self.assertEqual(code, 403)
        self.assertEqual(self.save(WEB + "/ok", origin="https://shiori.test.evil.test", wait=False)[0], 403)

    def test_api_save_http_blocked_and_failed(self):
        WEB_REQUESTS.clear()
        self.assert_not_saved("http://inside.test:%d/ok" % WEB_PORT, "blocked: private address")   # DNS -> private
        self.assertEqual(WEB_REQUESTS, [])
        self.assert_not_saved(WEB + "/to-inside", "blocked: private address")      # an allowed host redirects inward
        self.assert_not_saved(WEB + "/to-private-ip", "blocked: private address")
        self.assert_not_saved(WEB + "/to-vault", "blocked: private vault address")
        self.assertNotIn("/secret", [p for p, _, _ in WEB_REQUESTS])
        self.assert_not_saved(WEB + "/to-file", "non-http(s)")
        self.assert_not_saved(WEB + "/loop", "more than 5 redirects")
        self.assert_not_saved(WEB + "/big-nolength", "larger than 5 MB")
        self.assert_not_saved(WEB + "/image", "not saved: image/png")
        self.assert_not_saved(WEB + "/missing", "HTTP 404")

    def test_api_save_http_skipped_by_hister(self):
        smallweb.hister_state["fails"] = 0
        saved = smallweb.store.save_count()
        self.assertEqual(self.save(WEB + "/skipme")[0], 202)
        self.assertEqual(len(self.docs_for("/skipme")), 1)                              # sent, and Hister said 406
        status = json.loads(get("/api/status", user=None)[2])
        self.assertEqual((status["hister"]["saved"], status["error"], smallweb.hister_state["fails"]), (saved, None, 0))

    def test_gopher(self):
        code, _, body = page(HOLE_URL)
        self.assertEqual(code, 200)
        self.assertIn("Welcome to the hole", body)
        self.assertIn("Plain &lt;text&gt; in a hole.", page("gopher://hole.test:%d/0/readme.txt" % GOPHER_PORT)[2])
        self.assertIn("Cats", get("/page?url=%s&q=cats" % urllib.parse.quote("gopher://hole.test:%d/7/find" % GOPHER_PORT, safe=""))[2])

    def test_tofu(self):
        url = "gemini://tofu.test:%d/two" % GEM_PORT
        Fakes.cert = "a"
        self.assertIn("<h1>Two</h1>", page(url)[2])
        Fakes.cert = "b"
        smallweb.store.q("DELETE FROM cache WHERE kind='page'")
        HISTER_DOCS.clear()
        code, _, body = page(url)
        self.assertIn("Certificate Changed", body)
        self.assertNotIn("<h1>Two</h1>", body)
        # a cross-site accept is refused; a same-origin one pins the new certificate
        import re as _re
        tofu_form = body.split('action="/tofu"', 1)[1].split("</form>", 1)[0]
        form = dict(_re.findall(r'name="(\w+)" value="([^"]*)"', tofu_form))
        data = urllib.parse.urlencode(form).encode()
        self.assertEqual(get("/tofu", data=data, headers={"Origin": "https://evil.test"})[0], 403)
        self.assertEqual(get("/tofu", data=data, headers={"Origin": BASE})[0], 303)
        self.assertIn("<h1>Two</h1>", page(url)[2])
        Fakes.cert = "a"

    def test_open_mode(self):
        smallweb.AUTH = "open"
        try:
            self.assertEqual(get("/", user=None)[0], 200)
        finally:
            smallweb.AUTH = "tailscale"
        with self.assertRaises(SystemExit):
            smallweb.auth_mode("opne")
        with self.assertRaises(SystemExit):
            smallweb.socks_addr("http://proxy:1080")
        self.assertEqual(smallweb.socks_addr("socks5h://proxy:1080"), "proxy:1080")



RAW = []                                                   # bytes a plain TCP listener received (MACH-F-1)


def raw_server():
    class H(socketserver.BaseRequestHandler):
        def handle(self):
            self.request.settimeout(1)
            try:
                RAW.append(self.request.recv(4096))
            except OSError:
                pass
    s = socketserver.ThreadingTCPServer(("127.0.0.1", 0), H)
    s.daemon_threads = True
    threading.Thread(target=s.serve_forever, daemon=True).start()
    return s.server_address[1]


RAW_PORT = raw_server()
CROSS = {"Sec-Fetch-Site": "cross-site", "Sec-Fetch-Mode": "navigate", "Sec-Fetch-Dest": "document"}


class Sweep(unittest.TestCase):
    """The 2026-10 sweep: MACH-F-1 (a relay to anything), F-2 (planted saves), F-5 (logs), F-8 (no deadline), F-9."""

    def fresh(self):
        HISTER_DOCS.clear()
        Fakes.gemini_requests.clear()
        smallweb.store.q("DELETE FROM cache WHERE kind='page'")
        smallweb.store.q("DELETE FROM saves")

    def test_no_control_characters_in_a_selector(self):
        RAW.clear()
        code, _, body = page("gopher://raw.test:%d/0x%%0D%%0AINJECTED%%0D%%0A" % RAW_PORT)
        self.assertEqual(code, 502)
        self.assertIn("control characters", body)
        time.sleep(0.3)
        self.assertFalse([r for r in RAW if b"INJECTED" in r])
        self.assertEqual(page("gemini://capsule.test:%d/a%%00b" % GEM_PORT)[0], 502)

    def test_private_hosts_and_bad_ports_are_never_reached(self):
        Fakes.resolved.clear()
        for url, why in (("gopher://inside.test/1", "private address"), ("gopher://127.0.0.2:%d/1" % GOPHER_PORT,
                         "private address"), ("gemini://inside.test/", "private address"),
                         ("gopher://hole.test:25/1", "port 25"), ("gopher://hole.test:6379/1", "port 6379")):
            code, _, body = page(url)
            self.assertEqual(code, 502, url)
            self.assertIn(why, body, url)
        self.assertEqual(Fakes.resolved, [])                         # the proxy was never asked
        smallweb.store.q("DELETE FROM cache WHERE kind='robots'")
        menu = page("gopher://hole.test:%d/1" % GOPHER_PORT)
        self.assertEqual(menu[0], 200)                               # a public (allowed) hole still opens
        self.assertIn("127.0.0.1:%d" % GOPHER_PORT, Fakes.resolved)   # by the vetted address

    def test_another_site_cant_make_smallweb_fetch_or_save(self):
        self.fresh()
        code, _, body = page(CAP + "/", headers=CROSS)
        self.assertEqual(code, 200)
        self.assertIn("Open This Page?", body)
        self.assertIn('href="/page?url=', body)
        for h in ({"Sec-Fetch-Site": "cross-site", "Sec-Fetch-Mode": "no-cors", "Sec-Fetch-Dest": "image"},
                  {"Sec-Fetch-Site": "same-site", "Sec-Fetch-Mode": "no-cors", "Sec-Fetch-Dest": "image"},
                  {"Sec-Fetch-Site": "same-site", "Sec-Fetch-Mode": "cors", "Sec-Fetch-Dest": "empty"}):
            self.assertEqual(page(CAP + "/", headers=h)[0], 403, h)
        self.assertEqual(get("/api/search?q=example+query&source=tlgs", headers=CROSS)[0], 403)
        smallweb.store.q("DELETE FROM cache WHERE kind='search'")
        self.assertEqual(get("/?q=never+asked&source=tlgs", headers=CROSS)[0], 200)   # the home page; nothing searched
        time.sleep(0.5)
        self.assertEqual(Fakes.gemini_requests, [])
        self.assertEqual(HISTER_DOCS, [])

    def test_saves_only_for_pages_viewed_through_smallweb(self):
        for headers, saved in (({"Sec-Fetch-Site": "same-origin", "Sec-Fetch-Mode": "navigate"}, True),
                               ({"Sec-Fetch-Site": "none", "Sec-Fetch-Mode": "navigate"}, True),
                               ({"Sec-Fetch-Site": "same-site", "Sec-Fetch-Mode": "navigate",
                                 "Sec-Fetch-Dest": "document"}, True),          # a result clicked in Shiori
                               ({}, True)):                                      # an app, not a browser
            self.fresh()
            self.assertEqual(page(CAP + "/", headers=headers)[0], 200)
            self.assertEqual(wait_for(lambda: HISTER_DOCS, 2), saved, headers)
        self.fresh()
        req = urllib.request.Request(BASE + "/page?url=" + urllib.parse.quote(CAP + "/", safe=""), method="HEAD",
                                     headers={"Tailscale-User-Login": "user@test"})
        with urllib.request.urlopen(req, timeout=10) as r:
            self.assertEqual(r.status, 200)
        time.sleep(0.5)
        self.assertEqual(HISTER_DOCS, [])                            # HEAD never saves

    def test_the_log_has_no_queries(self):
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            page(CAP + "/?secretword")
            get("/?q=secretsearch")
            get("/page?url=gemini%3A%2F%2Fcapsule.test%2Fask&q=secretanswer")
            time.sleep(0.2)
        log = err.getvalue()
        self.assertIn("smallweb GET /page ", log)
        self.assertIn("smallweb GET / 200", log)
        for word in ("secretword", "secretsearch", "secretanswer", "capsule.test"):
            self.assertNotIn(word, log)

    def test_a_trickling_server_hits_the_deadline(self):
        a, b = socket.socketpair()
        stop = threading.Event()

        def trickle():
            while not stop.is_set():
                try:
                    b.sendall(b"x")
                except OSError:
                    return
                time.sleep(0.05)
        threading.Thread(target=trickle, daemon=True).start()
        try:
            a.settimeout(5)
            t = time.time()
            with self.assertRaises(smolnet.FetchError) as cm:
                smolnet.read_all(a, deadline=0.5)
            self.assertEqual(cm.exception.kind, "timeout")
            self.assertLess(time.time() - t, 3)
        finally:
            stop.set()
            a.close()
            b.close()

    def test_the_header_counts_only_from_the_trusted_proxy(self):
        old = smallweb.TRUSTED
        smallweb.TRUSTED = web.parse_allow("10.9.9.9/32")[1]
        try:
            self.assertEqual(get("/")[0], 403)                       # 127.0.0.1 isn't the sidecar
        finally:
            smallweb.TRUSTED = old
        self.assertEqual(get("/")[0], 200)

    def test_a_public_bind_needs_a_trusted_proxy(self):
        old = (smallweb.TRUSTED, smallweb.BEHIND_PROXY)
        try:
            smallweb.TRUSTED, smallweb.BEHIND_PROXY = [], False
            with self.assertRaises(SystemExit):
                smallweb.check_bind("0.0.0.0")
            smallweb.check_bind("127.0.0.1")
            smallweb.BEHIND_PROXY = True
            smallweb.check_bind("0.0.0.0")
            smallweb.TRUSTED, smallweb.BEHIND_PROXY = web.parse_allow("172.31.250.2/32")[1], False
            smallweb.check_bind("0.0.0.0")
        finally:
            smallweb.TRUSTED, smallweb.BEHIND_PROXY = old


class KeepAlive(unittest.TestCase):
    """0.2.3: Tailscale Serve sends different people's requests down one kept-alive connection; each is answered
    alone, and a body smallweb didn't read never becomes the next request."""

    def one_connection(self, requests):
        import http.client
        conn = http.client.HTTPConnection(BASE.split("//")[1], timeout=10)
        out = []
        try:
            for method, path, user, body in requests:
                h = {"Tailscale-User-Login": user} if user else {}
                if body is not None:
                    h.update({"Content-Type": "application/json", "Origin": "https://shiori.test"})
                conn.request(method, path, body=body, headers=h)
                r = conn.getresponse()
                out.append((r.status, r.read()))
        finally:
            conn.close()
        return out

    @staticmethod
    def raw(data):
        import socket
        host, port = BASE.split("//")[1].split(":")
        s = socket.create_connection((host, int(port)), timeout=2)
        s.sendall(data)
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
        return out, closed

    def test_each_request_is_answered_alone(self):
        empty = b'{"url": ""}'
        got = self.one_connection([
            ("GET", "/static/smallweb.css", None, None),            # nobody: 403
            ("GET", "/static/smallweb.css", "user@test", None),     # an allowed user
            ("GET", "/static/smallweb.css", None, None),            # nobody again
            ("GET", "/static/smallweb.css", "other@test", None),    # a login that isn't allowed
            ("GET", "/", "user@test", None),
            ("POST", "/api/save", "user@test", empty),              # allowed: the body's read (400: no url)
            ("POST", "/api/save", "user@test", empty),
            ("POST", "/api/save", None, empty),                     # nobody: 403
            ("POST", "/api/save", "other@test", empty),
            ("POST", "/api/save", "user@test", empty),
        ])
        self.assertEqual([g[0] for g in got], [403, 200, 403, 403, 200, 400, 400, 403, 403, 400])

    def test_an_unread_body_never_becomes_a_request(self):
        form = b"url=gemini%3A%2F%2Fkeepalive.test%2F&sha256=" + b"a" * 64
        smuggled = (b"POST /tofu HTTP/1.1\r\nHost: x\r\nOrigin: http://x\r\nTailscale-User-Login: user@test\r\n"
                    b"Content-Type: application/x-www-form-urlencoded\r\nContent-Length: %d\r\n\r\n" % len(form)) + form
        before = smallweb.store.tofu_count()
        for head in (b"POST /api/save HTTP/1.1\r\nHost: x\r\n",                                  # nobody: 403
                     b"POST /tofu HTTP/1.1\r\nHost: x\r\nTailscale-User-Login: other@test\r\n",  # 403
                     b"GET /api/status HTTP/1.1\r\nHost: x\r\n",                                 # open, body unread
                     b"GET /static/smallweb.css HTTP/1.1\r\nHost: x\r\n"):                       # 403
            out, closed = self.raw(head + b"Content-Length: %d\r\n\r\n" % len(smuggled) + smuggled)
            self.assertEqual(out.count(b"HTTP/1.1 "), 1, head)
            self.assertTrue(closed, head)
            self.assertIn(b"\r\nConnection: close\r\n", out)
        self.assertEqual(smallweb.store.tofu_count(), before)

    def test_a_bad_content_length_is_refused(self):
        for value in (b"-1", b"x"):
            out, closed = self.raw(b"POST /api/save HTTP/1.1\r\nHost: x\r\nTailscale-User-Login: user@test\r\n"
                                   b"Content-Length: " + value + b"\r\n\r\n")
            self.assertTrue(out.startswith(b"HTTP/1.1 400 "), value)
            self.assertTrue(closed, value)


class HisterToken(unittest.TestCase):
    """SMALLWEB_HISTER_TOKEN_FILE (docs/contracts/hister.md): every save carries X-Access-Token when it is set, none
    when it isn't; the token is never logged, in /api/status or on argv; a set file that is bad stops the start."""

    def save(self, n):
        HISTER_DOCS.clear()
        HISTER_TOKENS.clear()
        smallweb.save_to_hister({"url": "gemini://tok.test/%s" % n, "title": "t", "text": "x", "body": "x",
                                 "scheme": "gemini", "mime": "text/gemini", "cert": None, "sha": "sha-%s" % n})
        self.assertTrue(wait_for(lambda: HISTER_TOKENS))
        return HISTER_TOKENS[0], HISTER_DOCS[0][0]

    def test_sent_when_set(self):
        self.assertEqual(self.save(time.time()), (TOKEN, "hister://"))

    def test_absent_when_unset(self):
        old = smallweb.HISTER_TOKEN
        smallweb.HISTER_TOKEN = None
        try:
            self.assertEqual(self.save(time.time()), (None, "hister://"))
            self.assertNotIn("X-Access-Token", smallweb.hister_headers())
        finally:
            smallweb.HISTER_TOKEN = old

    def test_never_logged_or_shown(self):
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            self.save(time.time())
            code, _, body = get("/api/status")
        self.assertNotIn(TOKEN, err.getvalue())
        self.assertNotIn(TOKEN, body if isinstance(body, str) else json.dumps(body))
        self.assertNotIn(TOKEN, repr(smallweb.HISTER_TOKEN))
        self.assertNotIn(TOKEN, " ".join(sys.argv))

    def test_bad_file_refuses_to_start(self):
        folder = tempfile.mkdtemp()
        try:
            empty = os.path.join(folder, "empty")
            open(empty, "w").close()
            for path in (empty, os.path.join(folder, "missing"), folder):
                with self.assertRaises(SystemExit, msg=path):
                    smallweb.hister_token(path)
            self.assertIsNone(smallweb.hister_token(""))
            rotating = os.path.join(folder, "t")
            with open(rotating, "w") as f:
                f.write("first-token\n")
            secret = smallweb.hister_token(rotating)
            with open(rotating + ".new", "w") as f:
                f.write("second-token-longer\n")
            os.replace(rotating + ".new", rotating)
            self.assertEqual(secret.get(), "second-token-longer")
            os.unlink(rotating)
            self.assertEqual(secret.get(), "second-token-longer")
        finally:
            shutil.rmtree(folder)

class RoomTokenTest(unittest.TestCase):
    """0.3.0: a room token (Bearer mht_) hister-login issued for smallweb, beside the Tailscale header."""

    def setUp(self):
        good = self.good = "mht_" + "G" * 43
        seen = self.seen = []

        class Helper(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_GET(self):
                seen.append((self.headers.get("X-Machiya-Session"), self.headers.get("X-Machiya-Room")))
                ok = self.headers.get("X-Machiya-Session") == good and \
                    self.headers.get("X-Machiya-Room") == "https://smallweb.test"
                body = json.dumps({"username": "owner", "kind": "token"} if ok else {"reason": "wrong-room"}).encode()
                self.send_response(200 if ok else 401)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
        self.helper = ThreadingHTTPServer(("127.0.0.1", 0), Helper)
        threading.Thread(target=self.helper.serve_forever, daemon=True).start()
        self.saved = (smallweb.AUTH_URL, smallweb.HISTER_USERS)
        smallweb.AUTH_URL = "http://127.0.0.1:%d" % self.helper.server_address[1]
        smallweb.HISTER_USERS = {"owner"}
        smallweb._ROOM_TOKENS.clear()

    def tearDown(self):
        smallweb.AUTH_URL, smallweb.HISTER_USERS = self.saved
        smallweb._ROOM_TOKENS.clear()
        self.helper.shutdown()
        self.helper.server_close()

    def test_room_token(self):
        self.assertEqual(get("/api/status", user=None, headers={"Authorization": "Bearer " + self.good})[0], 200)
        st = get("/search?q=x", user=None, headers={"Authorization": "Bearer " + self.good})[0]
        self.assertNotEqual(st, 403)
        self.assertEqual(self.seen[-1], (self.good, "https://smallweb.test"))
        bad = "mht_" + "B" * 43
        self.assertEqual(get("/search?q=x", user=None, headers={"Authorization": "Bearer " + bad})[0], 403)
        self.assertEqual(get("/search?q=x", user="user@test", headers={"Authorization": "Bearer " + bad})[0], 403)
        self.assertEqual(get("/search?q=x", user="user@test")[0] != 403, True)       # no token: the header


if __name__ == "__main__":
    unittest.main()
