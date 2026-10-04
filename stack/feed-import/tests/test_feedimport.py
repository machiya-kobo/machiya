"""feed-import's tests: no network. Three local fakes on 127.0.0.1:
- NewsBlur (read_stories with its one-story page overlap, starred_story_hashes, starred_stories?h=, feeds), which
  answers `authenticated: false` without the token and records every method it is sent;
- Hister (/api/document, /api/add, /api/label, /api/rules), which refuses calls without `Origin: hister://`;
- article servers: a full article, a paywall teaser, 404, 503, a PDF, a redirect to a URL Hister already has.
The fetcher is smallweb's (stack/smallweb/web.py), allowed to reach 127.0.0.1 for the tests only.

Run, from stack/feed-import: python3 -m unittest discover -s tests
"""
import io
import json
import os
import sys
import tempfile
import threading
import unittest
from contextlib import redirect_stdout
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

import feedimport                                               # noqa: E402
import hister as histermod                                      # noqa: E402
import smolnet                                                  # noqa: E402
from newsblur import NewsBlur                                   # noqa: E402
from readers import ReaderError                                 # noqa: E402
from store import Store                                         # noqa: E402

TOKEN = "nb-test-token"
LONG = " ".join("Paragraph %d of the whole article, with enough words to count." % i for i in range(60))


def serve(handler):
    srv = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, "http://127.0.0.1:%d" % srv.server_address[1]


# -- the article servers --------------------------------------------------------------------------------------------

class Web(BaseHTTPRequestHandler):
    hits = []

    def log_message(self, *a):
        pass

    def do_GET(self):
        Web.hits.append(self.path)
        path = urlsplit(self.path).path
        if path.startswith("/article/"):
            self.page(200, "<html><head><title>Article %s</title></head><body><nav>Home</nav><article><p>%s</p>"
                           "</article><script>evil()</script></body></html>" % (path[9:], LONG))
        elif path == "/paywall":
            self.page(200, "<html><head><title>Paywalled</title></head><body><p>The first lines.</p>"
                           "<p>Subscribe to read more.</p></body></html>")
        elif path == "/flaky":
            self.page(503, "busy")
        elif path == "/pdf":
            self.send_response(200)
            self.send_header("Content-Type", "application/pdf")
            self.end_headers()
            self.wfile.write(b"%PDF-1.4")
        elif path == "/moved":
            self.send_response(301)
            self.send_header("Location", "/article/known?utm_source=feed")
            self.send_header("Content-Length", "0")
            self.end_headers()
        else:
            self.page(404, "gone")

    def page(self, status, body):
        data = body.encode()
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


WEB_SRV, WEB = serve(Web)


# -- NewsBlur -------------------------------------------------------------------------------------------------------

def story(h, path, title, ts=1700000000, feed=7, content="<p>%s</p>" % LONG, tags=None, starred=None):
    s = {"story_hash": h, "story_permalink": WEB + path if path.startswith("/") else path, "story_title": title,
         "story_content": content, "story_timestamp": str(ts), "story_feed_id": feed, "story_authors": "A. Writer",
         "story_tags": ["feedtag"]}
    if tags is not None:
        s["user_tags"] = tags
    if starred is not None:
        s["starred_timestamp"] = starred
    return s


class NB:
    read = []            # newest first, like lRS:<user>
    starred = []         # [(story, star time)], newest first
    methods = []
    paths = []


class NewsBlurFake(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def reply(self, obj):
        data = json.dumps(obj).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(data)

    def do_POST(self):
        NB.methods.append("POST")
        self.reply({"result": "error"})

    def do_GET(self):
        NB.methods.append("GET")
        u = urlsplit(self.path)
        q = parse_qs(u.query)
        NB.paths.append(u.path)
        if self.headers.get("Authorization") != "Bearer " + TOKEN:
            return self.reply({"stories": [], "feeds": {}, "result": "ok", "authenticated": False})
        if u.path == "/reader/feeds":
            return self.reply({"feeds": {"7": {"id": 7, "feed_title": "A Blog"}, "8": {"id": 8, "feed_title": "News"}},
                               "flat_folders": {"Tech": [7], " ": [8]}, "authenticated": True})
        if u.path == "/reader/read_stories":
            page, limit = int(q.get("page", ["1"])[0]), int(q.get("limit", ["10"])[0])
            offset = limit * (page - 1)
            return self.reply({"stories": NB.read[offset:offset + limit + 1],      # LRANGE is inclusive: +1
                               "feeds": [{"id": 9, "feed_title": "Unsubscribed"}], "authenticated": True})
        if u.path == "/reader/starred_story_hashes":
            return self.reply({"starred_story_hashes": [[s["story_hash"], str(t)] for s, t in NB.starred],
                               "authenticated": True})
        if u.path == "/reader/starred_stories":
            want = q.get("h", [])
            assert len(want) <= 100
            return self.reply({"stories": [dict(s, starred_timestamp=t) for s, t in NB.starred if s["story_hash"] in want],
                               "feeds": {}, "authenticated": True})
        self.reply({"authenticated": True})


NB_SRV, NB_URL = serve(NewsBlurFake)


# -- Hister ---------------------------------------------------------------------------------------------------------

class H:
    docs = {}            # url -> document
    calls = []           # (method, path, body, headers)
    down = False


class HisterFake(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def send(self, status, obj=None):
        data = json.dumps(obj).encode() if obj is not None else b""
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def handle_any(self, method):
        u = urlsplit(self.path)
        body = None
        if method == "POST":
            body = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)) or b"null")
        H.calls.append((method, u.path, body, dict(self.headers)))
        if H.down:
            return self.send(503)
        if self.headers.get("Origin") != "hister://":
            return self.send(403)
        if u.path == "/api/document":
            doc = H.docs.get(parse_qs(u.query)["url"][0])
            return self.send(200, doc) if doc else self.send(404)
        if u.path == "/api/rules":
            return self.send(200, {"aliases": {"@reading": "label:(books|essays)", "@tech": "label:tech"}})
        if u.path == "/api/add":
            if "sensitive" in body["url"]:
                return self.send(422)
            if "skipme" in body["url"] and not body["metadata"].get("ignore_skip_rules"):
                return self.send(406)
            H.docs[body["url"]] = dict(body)
            return self.send(201)
        if u.path == "/api/label":
            if body["url"] not in H.docs:
                return self.send(404)
            H.docs[body["url"]]["label"] = body["label"]
            return self.send(200, {"ok": True})
        self.send(404)

    def do_GET(self):
        self.handle_any("GET")

    def do_POST(self):
        self.handle_any("POST")


H_SRV, H_URL = serve(HisterFake)


def adds():
    return [c[2] for c in H.calls if c[1] == "/api/add"]


def labels_set():
    return [c[2] for c in H.calls if c[1] == "/api/label"]


class Base(unittest.TestCase):
    def setUp(self):
        NB.read, NB.starred, NB.methods, NB.paths = [], [], [], []
        H.docs, H.calls, H.down = {}, [], False
        Web.hits = []
        self.tmp = tempfile.mkdtemp(prefix="feed-import-test-")
        self.store = Store(os.path.join(self.tmp, "state.sqlite3"))
        self.addCleanup(self.store.db.close)
        self.lines = []

    def importer(self, dry_run=False, token="", hister=True, fetch=True, **kw):
        nb = NewsBlur(NB_URL, TOKEN, page=3, gap=0)
        f = feedimport.Fetcher(allow="127.0.0.1/32") if fetch else None
        if f:
            f.polite = smolnet.Polite(default=0)
        h = histermod.Hister(H_URL, token) if hister else None
        return feedimport.Importer([nb], self.store, h, f, dry_run=dry_run, out=self.lines.append,
                                   clock=lambda: 1800000000, **kw)

    def tearDown(self):
        self.assertNotIn("POST", NB.methods, "feed-import must never send NewsBlur anything but GET")


class Units(unittest.TestCase):
    def test_norm_matches_hister(self):
        self.assertEqual(histermod.norm("https://a.example/p?b=2&utm_source=x&a=1#frag"), "https://a.example/p?a=1&b=2")
        self.assertEqual(histermod.norm("https://a.example/p?z=1&a=2#x"), "https://a.example/p?z=1&a=2")
        self.assertEqual(histermod.norm("https://a.example/p?utm=1"), "https://a.example/p")
        self.assertEqual(histermod.norm("mailto:x@example.com"), "mailto:x@example.com")

    def test_pause_window(self):
        w = feedimport.pause_window("Sun 02:20-02:50")
        self.assertEqual(w, (6, (2, 20), (2, 50)))
        self.assertTrue(feedimport.in_window(w, "UTC", now=1791685500))      # Sun 2026-10-11 02:25 UTC
        self.assertFalse(feedimport.in_window(w, "UTC", now=1791685500 + 3600))
        self.assertIsNone(feedimport.pause_window(""))
        with self.assertRaises(SystemExit):
            feedimport.pause_window("Sun 03:00-02:00")

    def test_socks_setting(self):
        self.assertEqual(feedimport.socks_addr("socks5h://proxy:1080"), "proxy:1080")
        self.assertEqual(feedimport.socks_addr(""), "")
        with self.assertRaises(SystemExit):
            feedimport.socks_addr("proxy:1080")


class Legacy(Base):
    def test_import_legacy_state(self):
        path = os.path.join(self.tmp, "state.json")
        with open(path, "w") as f:
            json.dump({"last_full": 1, "stories": {
                "a": {"status": "done", "url": WEB + "/article/a", "label": "books", "tags": ["Books"], "tries": 1},
                "b": {"status": "rejected", "url": WEB + "/article/b", "http": 422, "tries": 1},
                "c": {"status": "failed", "permalink": WEB + "/article/c", "tries": 5, "error": "fetch: HTTP 403"}}}, f)
        self.assertEqual(feedimport.import_legacy(self.store, path), {"known": 1, "rejected": 1})
        NB.starred = [(story(h, "/article/" + h, h.upper()), 1750000000) for h in "abc"]
        self.importer().run()
        self.assertEqual([d["url"] for d in adds()], [WEB + "/article/c"])    # only the one it had given up on
        self.assertEqual(feedimport.import_legacy(self.store, path), {})      # idempotent


class NewsBlurReader(Base):
    def test_pages_overlap_and_stop_early(self):
        NB.read = [story("h%d" % i, "/article/%d" % i, "Story %d" % i) for i in range(8)]
        nb = NewsBlur(NB_URL, TOKEN, page=3, gap=0)
        got = [e.id for e in nb.read(lambda h: False, backfill=True)]
        self.assertEqual(got, ["h%d" % i for i in range(8)])                 # each once, newest first
        NB.paths = []
        got = [e.id for e in nb.read(lambda h: h != "h0")]                   # only the newest is new
        self.assertEqual(got, ["h0"])
        self.assertEqual(NB.paths.count("/reader/read_stories"), 2)          # page 1 had h0; page 2 was all known

    def test_entry_fields(self):
        NB.read = [story("h1", "/article/1", "  A   title ", feed=7)]
        e = next(NewsBlur(NB_URL, TOKEN, gap=0).read(lambda h: False))
        self.assertEqual((e.title, e.feed, e.folder, e.published), ("A title", "A Blog", "Tech", 1700000000))
        self.assertEqual(e.meta, {"newsblur_story_hash": "h1", "newsblur_feed_id": "7", "newsblur_feed": "A Blog",
                                  "newsblur_folder": "Tech"})

    def test_starred_in_batches(self):
        NB.starred = [(story("s%d" % i, "/article/s%d" % i, "S%d" % i, tags=["Books"]), 1750000000 + i)
                      for i in range(5)]
        got = list(NewsBlur(NB_URL, TOKEN, gap=0).starred(lambda h: h == "s2"))
        self.assertEqual([(e.id, e.starred_at, e.tags) for e in got][:2], [("s0", 1750000000, ["Books"]),
                                                                           ("s1", 1750000001, ["Books"])])
        self.assertNotIn("s2", [e.id for e in got])

    def test_bad_token(self):
        with self.assertRaises(ReaderError):
            list(NewsBlur(NB_URL, "wrong", gap=0).read(lambda h: False))


class Pipeline(Base):
    def test_backfill(self):
        H.docs[WEB + "/article/known"] = {"url": WEB + "/article/known", "label": "tech"}
        H.docs[WEB + "/article/visited"] = {"url": WEB + "/article/visited", "label": ""}
        H.docs[WEB + "/article/labelled"] = {"url": WEB + "/article/labelled", "label": "essays"}
        NB.read = [story("r1", "/article/1#comments", "One"),
                   story("r2", "/paywall", "Paywalled"),
                   story("r3", "/missing", "Gone"),
                   story("r4", "/article/known", "Known"),
                   story("r5", "/flaky", "Flaky"),
                   story("r6", "/pdf", "A PDF", content=""),
                   story("r7", "gopher://example.org/1/", "Not http")]
        NB.starred = [(story("s1", "/article/s1", "Saved, tagged", tags=["Books", "x"]), 1750000001),
                      (story("s2", "/article/s2", "Saved, no tag"), 1750000002),
                      (story("s3", "/article/visited", "Saved, visited before"), 1750000003),
                      (story("s4", "/article/labelled", "Saved, labelled before"), 1750000004)]
        stats = self.importer().run()
        sent = {d["url"]: d for d in adds()}
        self.assertEqual(sorted(sent), sorted([WEB + "/article/1", WEB + "/paywall", WEB + "/missing",
                                               WEB + "/article/s1", WEB + "/article/s2"]))
        one = sent[WEB + "/article/1"]
        self.assertEqual((one["label"], one["added"], one["title"]), ("", 1700000000, "Article 1"))
        self.assertNotIn("evil()", one["html"])
        self.assertEqual(one["metadata"]["newsblur_content"], "original")
        self.assertEqual(one["metadata"]["source"], "newsblur")
        self.assertEqual(one["metadata"]["newsblur_permalink"], WEB + "/article/1#comments")
        self.assertNotIn("ignore_skip_rules", one["metadata"])
        pay = sent[WEB + "/paywall"]
        self.assertEqual(pay["metadata"]["newsblur_content"], "copy")
        self.assertIn("much shorter", pay["metadata"]["newsblur_copy_reason"])
        self.assertIn("Paragraph 59", pay["html"])
        gone = sent[WEB + "/missing"]
        self.assertEqual((gone["metadata"]["newsblur_content"], gone["metadata"]["newsblur_copy_reason"]),
                         ("copy", "HTTP 404"))
        s1, s2 = sent[WEB + "/article/s1"], sent[WEB + "/article/s2"]
        self.assertEqual((s1["label"], s1["added"]), ("books", 1750000001))    # the matching Hister label's spelling
        self.assertEqual((s2["label"], s2["added"]), ("starred", 1750000002))
        self.assertTrue(s1["metadata"]["ignore_skip_rules"] and s1["metadata"]["newsblur_starred"])
        self.assertEqual(labels_set(), [{"url": WEB + "/article/visited", "label": "starred"}])
        self.assertEqual(H.docs[WEB + "/article/labelled"]["label"], "essays")   # never relabelled
        self.assertEqual(self.store.get("newsblur", "r4")["status"], "known")
        self.assertEqual(self.store.get("newsblur", "r5")["status"], "pending")
        self.assertEqual(self.store.get("newsblur", "r6")["status"], "skipped")  # a PDF and no copy
        self.assertEqual(self.store.get("newsblur", "r7")["error"], "no http(s) URL")
        self.assertEqual(stats["newsblur"]["added original"], 3)

        # the next run: nothing new; the flaky one is tried again, and after 3 tries its copy is stored
        H.calls = []
        imp = self.importer()
        imp.run()
        self.assertEqual(adds(), [])
        imp.run()
        self.assertEqual([d["url"] for d in adds()], [WEB + "/flaky"])
        self.assertIn("gave up after 3 tries", adds()[0]["metadata"]["newsblur_copy_reason"])
        self.assertEqual(adds()[0]["added"], 1700000000)                       # the backfill's time, kept on retry
        self.assertEqual(Web.hits.count("/flaky"), 3)

    def test_not_backfill_uses_now(self):
        self.store.meta("backfill_done:newsblur", 1)
        self.store.put("newsblur", "old", status="added", url="https://example.org/old")
        NB.read = [story("r1", "/article/1", "One")]
        self.importer().run()
        self.assertEqual(adds()[0]["added"], 1800000000)

    def test_starred_after_read(self):
        NB.read = [story("r1", "/article/1", "One")]
        self.importer().run()
        self.assertEqual(adds()[0]["label"], "")
        NB.starred = [(story("r1", "/article/1", "One", tags=["Tech"]), 1750000000)]
        H.calls = []
        self.importer().run()
        self.assertEqual(adds(), [])                                          # never re-added
        self.assertEqual(labels_set(), [{"url": WEB + "/article/1", "label": "tech"}])
        H.calls = []
        self.importer().run()
        self.assertEqual(labels_set(), [])
        self.assertNotIn("/reader/starred_stories", [p for p in NB.paths[-2:]])

    def test_redirect_to_known(self):
        H.docs[WEB + "/article/known"] = {"url": WEB + "/article/known", "label": ""}
        NB.read = [story("r1", "/moved", "Moved")]
        self.importer().run()
        self.assertEqual(adds(), [])
        self.assertEqual(self.store.get("newsblur", "r1")["url"], WEB + "/article/known")

    def test_hister_refusals(self):
        NB.read = [story("r1", "/article/skipme", "Skipped"), story("r2", "/article/sensitive", "Sensitive")]
        self.importer().run()
        self.assertEqual(self.store.get("newsblur", "r1")["status"], "skipped")
        self.assertEqual(self.store.get("newsblur", "r2")["status"], "rejected")
        NB.starred = [(story("r1", "/article/skipme", "Skipped"), 1750000000)]
        self.importer().run()                                                 # a save ignores skip rules
        self.assertEqual(self.store.get("newsblur", "r1")["status"], "added")

    def test_hister_down(self):
        NB.read = [story("r1", "/article/1", "One")]
        H.down = True
        with self.assertRaises(histermod.HisterDown):
            self.importer().run()
        self.assertIsNone(self.store.get("newsblur", "r1"))

    def test_token_header(self):
        NB.read = [story("r1", "/article/1", "One")]
        self.importer(token="owner-token").run()
        self.assertTrue(all(c[3].get("X-Access-Token") == "owner-token" for c in H.calls))
        H.calls = []
        NB.read = [story("r2", "/article/2", "Two")] + NB.read
        self.importer().run()
        self.assertTrue(all("X-Access-Token" not in c[3] for c in H.calls))

    def test_token_file_sent_on_every_call_and_reread(self):
        path = os.path.join(self.tmp, "hister.token")
        with open(path, "w") as f:
            f.write("owner-token-one\n")
        secret = histermod.token_file(path)
        H.docs[WEB + "/article/known"] = {"url": WEB + "/article/known", "label": ""}
        NB.read = [story("r1", "/article/1", "One")]
        NB.starred = [(story("s1", "/article/known", "Known"), 1700000000)]
        self.importer(token=secret).run()
        paths = {c[1] for c in H.calls}
        self.assertTrue({"/api/document", "/api/add", "/api/label"} <= paths, paths)
        self.assertTrue(all(c[3].get("X-Access-Token") == "owner-token-one" for c in H.calls))
        with open(path + ".new", "w") as f:                       # rotated: picked up without a restart
            f.write("owner-token-two\n")
        os.replace(path + ".new", path)
        H.calls = []
        NB.read = [story("r2", "/article/2", "Two")] + NB.read
        self.importer(token=secret).run()
        self.assertTrue(H.calls and all(c[3].get("X-Access-Token") == "owner-token-two" for c in H.calls))
        os.unlink(path)                                           # vanished: the last good value stays
        self.assertEqual(secret.get(), "owner-token-two")
        self.assertNotIn("owner-token", repr(secret) + repr(histermod.Hister(H_URL, secret)))
        self.assertFalse(any("owner-token" in x for x in self.lines))

    def test_token_file_refusals(self):
        empty = os.path.join(self.tmp, "empty")
        open(empty, "w").close()
        spaced = os.path.join(self.tmp, "spaced")
        with open(spaced, "w") as f:
            f.write("secret with spaces\n")
        for path in (empty, spaced, os.path.join(self.tmp, "missing"), self.tmp):
            with self.assertRaises(SystemExit, msg=path) as cm:
                histermod.token_file(path)
            self.assertNotIn("secret with", str(cm.exception))
        self.assertIsNone(histermod.token_file(""))
        self.assertIsNone(histermod.token_file(None))
        env = {"FEED_IMPORT_READERS": "newsblur", "FEED_IMPORT_NEWSBLUR_URL": NB_URL,
               "FEED_IMPORT_NEWSBLUR_TOKEN_FILE": spaced, "FEED_IMPORT_HISTER_URL": H_URL,
               "FEED_IMPORT_HISTER_TOKEN_FILE": empty, "FEED_IMPORT_DATA": os.path.join(self.tmp, "data")}
        with self.assertRaises(SystemExit):
            feedimport.build(env)

    def test_no_redirect_with_the_token(self):
        elsewhere = []

        class Redirect(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_GET(self):
                elsewhere.append(dict(self.headers)) if self.path.startswith("/landed") else None
                self.send_response(302 if not self.path.startswith("/landed") else 404)
                self.send_header("Location", "/landed")
                self.send_header("Content-Length", "0")
                self.end_headers()
        srv, url = serve(Redirect)
        with self.assertRaises(histermod.HisterDown) as cm:
            histermod.Hister(url, "owner-token").document("https://x.test/a")
        self.assertIn("not followed", str(cm.exception))
        self.assertNotIn("owner-token", str(cm.exception))
        self.assertEqual(elsewhere, [])
        self.assertIsNone(histermod.Hister(url).document("https://x.test/a"))  # without a token, as before (404)
        self.assertEqual(len(elsewhere), 1)
        self.assertNotIn("X-Access-Token", elsewhere[0])

    def test_dry_run_writes_nothing(self):
        H.docs[WEB + "/article/known"] = {"url": WEB + "/article/known", "label": ""}
        NB.read = [story("r%d" % i, "/article/%d" % i, "S%d" % i) for i in range(15)]
        NB.starred = [(story("s1", "/article/known", "Saved"), 1750000000)]
        imp = self.importer(dry_run=True)
        imp.run(limit=10)
        self.assertEqual([c for c in H.calls if c[0] == "POST"], [])
        self.assertEqual(self.store.counts(), {})
        printed = [json.loads(x) for x in self.lines if x.startswith("{")]
        self.assertEqual(len([p for p in printed if "would_add" in p]), 10)
        self.assertIn({"already_in_hister": WEB + "/article/known", "label": "starred", "stream": "starred"}, printed)
        self.assertTrue(any("would label" in x for x in self.lines))

    def test_dry_run_cli_without_hister(self):
        NB.read = [story("r1", "/article/1", "One")]
        tok = os.path.join(self.tmp, "token")
        with open(tok, "w") as f:
            f.write(TOKEN + "\n")
        env = {"FEED_IMPORT_NEWSBLUR_URL": NB_URL, "FEED_IMPORT_NEWSBLUR_TOKEN_FILE": tok,
               "FEED_IMPORT_DATA": os.path.join(self.tmp, "data")}
        old = dict(os.environ)
        os.environ.clear()
        os.environ.update(env)
        try:
            out = io.StringIO()
            with redirect_stdout(out):
                rc = feedimport.main(["--dry-run", "--no-fetch"])
        finally:
            os.environ.clear()
            os.environ.update(old)
        self.assertEqual(rc, 0)
        self.assertIn('"would_add"', out.getvalue())
        self.assertFalse(os.path.exists(os.path.join(self.tmp, "data")))
        with self.assertRaises(SystemExit):
            feedimport.build(env)                                            # a real run needs Hister
        with self.assertRaises(SystemExit) as cm:                            # no default NewsBlur server
            feedimport.build({k: v for k, v in env.items() if k != "FEED_IMPORT_NEWSBLUR_URL"}, dry_run=True)
        self.assertIn("FEED_IMPORT_NEWSBLUR_URL", str(cm.exception))


if __name__ == "__main__":
    unittest.main()
