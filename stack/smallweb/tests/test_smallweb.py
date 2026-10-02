"""smallweb's tests: the parsers on the engines' response formats (with invented content), the renderers, and the
whole gateway against local fakes: two Gemini engines and a capsule (TLS), a Gopher engine and hole, a SOCKS5 proxy
(which records the names it resolves: socks5h) and Hister (which records /api/add).

The test certificates are throwaway self-signed ones for 127.0.0.1 fakes, made with `openssl` when the tests start and
deleted afterwards (nothing secret is committed; `old` is valid for one day, so it is always about to expire).
Run, from stack/smallweb (needs openssl): python3 -m unittest discover -s tests
"""
import atexit
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
                n = c.recv(1)[0]
                name = c.recv(n).decode()
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


HISTER_DOCS = []


class HisterFake(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_POST(self):
        doc = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        HISTER_DOCS.append((self.headers.get("Origin"), doc))
        self.send_response(201)
        self.send_header("Content-Length", "0")
        self.end_headers()


GEM_PAGES, GOPHER_PAGES = {}, {}                  # filled below, once the ports are known
GEM_PORT = Fakes.gemini_server(GEM_PAGES)
GOPHER_PORT = Fakes.gopher_server(GOPHER_PAGES)
SOCKS_PORT = Fakes.socks_server()
hister = ThreadingHTTPServer(("127.0.0.1", 0), HisterFake)
threading.Thread(target=hister.serve_forever, daemon=True).start()

DATA = tempfile.mkdtemp()
os.environ.update(SMALLWEB_DATA=DATA, SMALLWEB_USERS="user@test", SMALLWEB_SOCKS="socks5h://127.0.0.1:%d" % SOCKS_PORT,
                  SMALLWEB_HISTER_URL="http://127.0.0.1:%d" % hister.server_address[1], SMALLWEB_PER_HOUR="100",
                  SMALLWEB_PUBLIC_URL="https://smallweb.test", SMALLWEB_ORIGINS="https://shiori.test/")
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


class Gateway(unittest.TestCase):
    def test_users_only_and_status_open(self):
        self.assertEqual(get("/api/search?q=x", user=None)[0], 403)
        self.assertEqual(page(CAP + "/", user="other@test")[0], 403)
        code, _, body = get("/api/status", user=None)
        self.assertEqual(code, 200)
        d = json.loads(body)
        self.assertEqual((d["auth"], d["ready"], d["error"]), ("tailscale", True, None))

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
        self.assertEqual(get("/api/save", data=b'{"url": "https://example.com/"}', headers=dict(js, Origin="hister://"))[0], 400)
        self.assertEqual(get("/api/save", data=body, user="other@test", headers=dict(js, Origin="hister://"))[0], 403)
        n = len(HISTER_DOCS)
        get("/api/save", data=json.dumps({"url": CAP + "/ask?Ann%20B"}).encode(), headers=dict(js, Origin="hister://"))
        time.sleep(0.5)
        self.assertEqual(len([d for _, d in HISTER_DOCS if "/ask?" in d["url"]]), 0)   # the never-save rules hold

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


if __name__ == "__main__":
    unittest.main()
