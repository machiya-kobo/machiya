"""The Miniflux, FreshRSS and Feedbin readers against local fakes of their APIs (shapes from their docs and source,
2026-10-04), and one whole run per reader through the pipeline into the fake Hister of test_feedimport. No network.
Every fake records its requests: the readers may only GET, except FreshRSS's ClientLogin and items/contents (read-only
POSTs, the only way FreshRSS takes those)."""
import base64
import json
import os
import sys
import tempfile
import unittest
from http.server import BaseHTTPRequestHandler
from urllib.parse import parse_qs, urlsplit

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
sys.path.insert(0, HERE)

import feedimport                                               # noqa: E402
import hister as histermod                                      # noqa: E402
import smolnet                                                  # noqa: E402
import test_feedimport as T                                     # noqa: E402  (its fakes: Hister, the article servers)
from feedbin import Feedbin                                     # noqa: E402
from freshrss import FreshRSS, decimal_id                       # noqa: E402
from miniflux import Miniflux                                   # noqa: E402
from readers import ReaderError, unix                           # noqa: E402
from store import Store                                         # noqa: E402

REQ = []                # (method, path, query, body, headers)


class Fake(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def send(self, status, obj, text=False):
        data = obj.encode() if text else json.dumps(obj).encode()
        self.send_response(status)
        self.send_header("Content-Type", "text/plain" if text else "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        self.route("GET")

    def do_POST(self):
        self.route("POST")

    def route(self, method):
        u = urlsplit(self.path)
        body = self.rfile.read(int(self.headers.get("Content-Length") or 0)).decode() if method == "POST" else ""
        REQ.append((method, u.path, parse_qs(u.query), body, dict(self.headers)))
        self.answer(method, u.path, parse_qs(u.query), parse_qs(body), body)


# -- Miniflux -----------------------------------------------------------------------------------------------------

MF = {"entries": []}     # each: dict with status, starred, changed (unix)


def mf_entry(i, path, status="read", starred=False, changed=1760000000):
    return {"id": i, "feed_id": 3, "status": status, "starred": starred, "title": "MF %d" % i, "url": T.WEB + path,
            "content": "<p>%s</p>" % T.LONG, "published_at": "2025-09-01T10:00:00.123456789Z",
            "changed_at": "2025-10-%02dT08:00:00Z" % (1 + changed % 28), "tags": ["feed-category"],
            "feed": {"id": 3, "title": "MF Feed", "category": {"id": 1, "title": "Reading"}}, "_changed": changed}


class MinifluxFake(Fake):
    def answer(self, method, path, q, form, raw):
        if self.headers.get("X-Auth-Token") != "mf-key":
            return self.send(401, {"error_message": "Access Unauthorized"})
        if path != "/v1/entries" or method != "GET":
            return self.send(404, {})
        items = MF["entries"]
        if q.get("status") == ["read"]:
            items = [e for e in items if e["status"] == "read"]
        if q.get("starred") == ["true"]:
            items = [e for e in items if e["starred"]]
        assert q["order"] == ["changed_at"] and q["direction"] == ["desc"]
        items = sorted(items, key=lambda e: -e["_changed"])
        off, lim = int(q["offset"][0]), int(q["limit"][0])
        self.send(200, {"total": len(items), "entries": [{k: v for k, v in e.items() if k != "_changed"}
                                                          for e in items[off:off + lim]]})


# -- FreshRSS -----------------------------------------------------------------------------------------------------

FR = {"items": {}, "read": [], "starred": []}       # decimal id -> item; id lists newest first


def fr_item(i, path, labels=(), folder="Tech"):
    return {"id": "tag:google.com,2005:reader/item/%016x" % i, "crawlTimeMsec": "1760000000000",
            "timestampUsec": "1760000000000000", "published": 1759000000, "title": "FR %d" % i,
            "canonical": [{"href": T.WEB + path}], "alternate": [{"href": T.WEB + path, "type": "text/html"}],
            "categories": ["user/-/state/com.google/reading-list", "user/-/label/" + folder]
                          + ["user/-/label/" + x for x in labels],
            "origin": {"streamId": "feed/5", "htmlUrl": T.WEB + "/", "title": "FR Feed"},
            "summary": {"content": "<p>%s</p>" % T.LONG}, "author": "W"}


class FreshRSSFake(Fake):
    def answer(self, method, path, q, form, raw):
        base = "/api/greader.php"
        if path == base + "/accounts/ClientLogin":
            assert method == "POST" and "Passwd" not in self.path
            if form.get("Email") == ["owner"] and form.get("Passwd") == ["api-pass"]:
                return self.send(200, "SID=owner/abc\nLSID=null\nAuth=owner/abc\n", text=True)
            return self.send(401, "Unauthorized", text=True)
        if self.headers.get("Authorization") != "GoogleLogin auth=owner/abc":
            return self.send(401, "Unauthorized", text=True)
        if path == base + "/reader/api/0/subscription/list":
            return self.send(200, {"subscriptions": [{"id": "feed/5", "title": "FR Feed",
                                                      "categories": [{"id": "user/-/label/Tech", "label": "Tech"}]}]})
        if path == base + "/reader/api/0/stream/items/ids":
            ids = FR[q["s"][0].rsplit("/", 1)[-1]]
            n, c = int(q["n"][0]), q.get("c", [""])[0]
            start = ids.index(c) + 1 if c else 0
            page = ids[start:start + n]
            out = {"itemRefs": [{"id": x} for x in page]}
            if len(page) == n:
                out["continuation"] = page[-1]
            return self.send(200, out)
        if path == base + "/reader/api/0/stream/items/contents" and method == "POST":
            want = [decimal_id(x) for x in form.get("i", [])]
            assert len(want) <= 100
            return self.send(200, {"items": [FR["items"][x] for x in want if x in FR["items"]]})
        self.send(404, {})


# -- Feedbin ------------------------------------------------------------------------------------------------------

FB = {"entries": [], "recent": [], "starred": []}     # entries: dicts with _read, newest created first
FB_AUTH = "Basic " + base64.b64encode(b"me@example.com:fb-pass").decode()


def fb_entry(i, path, read=True, created="2025-10-01T00:00:00.000000Z"):
    return {"id": i, "feed_id": 47, "title": "FB %d" % i, "url": T.WEB + path, "author": None,
            "content": "<p>%s</p>" % T.LONG, "summary": "s", "published": "2025-09-30T12:00:00.000000Z",
            "created_at": created, "_read": read}


class FeedbinFake(Fake):
    def answer(self, method, path, q, form, raw):
        if self.headers.get("Authorization") != FB_AUTH:
            return self.send(401, {})
        if method != "GET":
            return self.send(405, {})
        clean = lambda xs: [{k: v for k, v in e.items() if k != "_read"} for e in xs]
        if path == "/v2/subscriptions.json":
            return self.send(200, [{"id": 1, "feed_id": 47, "title": "FB Feed"}])
        if path == "/v2/taggings.json":
            return self.send(200, [{"id": 4, "feed_id": 47, "name": "Blogs"}])
        if path == "/v2/recently_read_entries.json":
            return self.send(200, FB["recent"])
        if path == "/v2/starred_entries.json":
            return self.send(200, FB["starred"])
        if path == "/v2/entries.json":
            if "ids" in q:
                ids = [int(x) for x in q["ids"][0].split(",")]
                assert len(ids) <= 100
                return self.send(200, clean([e for e in FB["entries"] if e["id"] in ids]))
            items = [e for e in FB["entries"] if e["_read"] == (q.get("read") == ["true"])]
            if "since" in q:
                items = [e for e in items if unix(e["created_at"]) > unix(q["since"][0])]
            per, page = int(q["per_page"][0]), int(q["page"][0])
            chunk = items[(page - 1) * per:page * per]
            return self.send(200, clean(chunk)) if chunk or page == 1 else self.send(404, {"status": 404})
        self.send(404, {})


MF_SRV, MF_URL = T.serve(MinifluxFake)
FR_SRV, FR_URL = T.serve(FreshRSSFake)
FB_SRV, FB_URL = T.serve(FeedbinFake)


class Base(unittest.TestCase):
    def setUp(self):
        REQ.clear()
        T.H.docs, T.H.calls = {}, []
        MF["entries"] = []
        FR.update(items={}, read=[], starred=[])
        FB.update(entries=[], recent=[], starred=[])
        self.store = Store(os.path.join(tempfile.mkdtemp(prefix="feed-import-readers-"), "s.sqlite3"))
        self.addCleanup(self.store.db.close)

    def tearDown(self):
        allowed = {"/api/greader.php/accounts/ClientLogin", "/api/greader.php/reader/api/0/stream/items/contents"}
        self.assertEqual([r[1] for r in REQ if r[0] != "GET" and r[1] not in allowed], [],
                         "a reader may only read")

    def run_import(self, reader):
        f = feedimport.Fetcher(allow="127.0.0.1/32")
        f.polite = smolnet.Polite(default=0)
        imp = feedimport.Importer([reader], self.store, histermod.Hister(T.H_URL), f, out=lambda *_: None,
                                  clock=lambda: 1800000000)
        imp.run()
        return {d["url"]: d for d in T.adds()}


class Units(unittest.TestCase):
    def test_unix(self):
        self.assertEqual(unix("2025-09-01T10:00:00.123456789Z"), 1756720800)
        self.assertEqual(unix("2013-02-03T01:00:19.000000Z"), 1359853219)
        self.assertEqual(unix("1700000000"), 1700000000)
        self.assertEqual(unix(None), 0)
        self.assertEqual(unix("nonsense"), 0)
        self.assertEqual(unix("2025-09-01T03:00:00.5-07:00"), 1756720800)

    def test_decimal_id(self):
        self.assertEqual(decimal_id("tag:google.com,2005:reader/item/000000000000001f"), "31")
        self.assertEqual(decimal_id("31"), "31")


class MinifluxTests(Base):
    def test_run(self):
        MF["entries"] = [mf_entry(1, "/article/m1", changed=1760000300), mf_entry(2, "/article/m2", changed=1760000200),
                         mf_entry(3, "/article/m3", starred=True, changed=1760000100),
                         mf_entry(4, "/article/m4", status="unread", changed=1760000400)]
        sent = self.run_import(Miniflux(MF_URL, "mf-key", gap=0, page=2))
        self.assertEqual(sorted(sent), sorted(T.WEB + "/article/m%d" % i for i in (1, 2, 3)))
        m1, m3 = sent[T.WEB + "/article/m1"], sent[T.WEB + "/article/m3"]
        self.assertEqual((m1["label"], m1["added"], m1["metadata"]["source"]), ("", unix(MF["entries"][0]["changed_at"]), "miniflux"))
        self.assertEqual(m1["metadata"]["miniflux_category"], "Reading")
        self.assertEqual(m3["label"], "starred")                    # the feed's own categories never become a label
        self.assertTrue(m3["metadata"]["ignore_skip_rules"])
        # a new read on top: one page is enough
        REQ.clear()
        T.H.calls = []
        MF["entries"].append(mf_entry(5, "/article/m5", changed=1760000500))
        Miniflux_ = Miniflux(MF_URL, "mf-key", gap=0, page=2)
        self.run_import(Miniflux_)
        self.assertEqual([d["url"] for d in T.adds()], [T.WEB + "/article/m5"])
        self.assertEqual(len([r for r in REQ if r[2].get("status") == ["read"]]), 2)   # page 1 had m5; page 2 is short

    def test_bad_key(self):
        with self.assertRaises(ReaderError):
            list(Miniflux(MF_URL, "wrong", gap=0).read(lambda i: False))


class FreshRSSTests(Base):
    def test_run(self):
        for i, path, labels in ((10, "/article/f10", ()), (11, "/article/f11", ()), (12, "/article/f12", ("Books", "Tech"))):
            FR["items"][str(i)] = fr_item(i, path, labels)
        FR["read"] = ["11", "10"]
        FR["starred"] = ["12"]
        reader = FreshRSS(FR_URL, "owner", "api-pass", gap=0)
        import freshrss
        old = freshrss.IDS
        freshrss.IDS = 1                                            # page the id list one at a time
        try:
            sent = self.run_import(reader)
        finally:
            freshrss.IDS = old
        self.assertEqual(sorted(sent), sorted(T.WEB + "/article/f%d" % i for i in (10, 11, 12)))
        f12 = sent[T.WEB + "/article/f12"]
        self.assertEqual(f12["label"], "books")                     # a FreshRSS label that names a Hister label
        self.assertEqual(f12["metadata"]["freshrss_tags"], ["Books"])       # the folder is not a tag
        self.assertEqual(f12["metadata"]["freshrss_folder"], "Tech")
        self.assertEqual(f12["added"], 1759000000)                  # no star time: the story's date in a backfill
        f10 = sent[T.WEB + "/article/f10"]
        self.assertEqual((f10["label"], f10["metadata"]["freshrss_item_id"]), ("", "10"))
        # an OLD entry read now (low id, deep in the list) is still found: the whole id list is diffed
        FR["items"]["3"] = fr_item(3, "/article/f3")
        FR["read"].append("3")
        T.H.calls = []
        self.run_import(FreshRSS(FR_URL, "owner", "api-pass", gap=0))
        self.assertEqual([(d["url"], d["added"]) for d in T.adds()], [(T.WEB + "/article/f3", 1800000000)])

    def test_bad_password(self):
        with self.assertRaises(ReaderError):
            list(FreshRSS(FR_URL, "owner", "wrong", gap=0).read(lambda i: False))


class FeedbinTests(Base):
    def test_run(self):
        FB["entries"] = [fb_entry(i, "/article/b%d" % i, created="2025-10-%02dT00:00:00.000000Z" % (20 - i))
                         for i in range(1, 4)]
        FB["entries"].append(fb_entry(9, "/article/b9", read=False))
        FB["entries"].append(fb_entry(50, "/article/b50", read=False, created="2020-01-01T00:00:00.000000Z"))
        FB["starred"] = [50]
        sent = self.run_import(Feedbin(FB_URL, "me@example.com", "fb-pass", gap=0, clock=lambda: unix("2025-10-25T00:00:00Z")))
        self.assertEqual(sorted(sent), sorted(T.WEB + "/article/b%d" % i for i in (1, 2, 3, 50)))
        b1, b50 = sent[T.WEB + "/article/b1"], sent[T.WEB + "/article/b1".replace("b1", "b50")]
        self.assertEqual((b1["label"], b1["metadata"]["feedbin_feed"], b1["metadata"]["feedbin_folder"]),
                         ("", "FB Feed", "Blogs"))
        self.assertEqual((b50["label"], b50["metadata"]["feedbin_starred"]), ("starred", True))
        # later: an old entry read in Feedbin's web app (outside the look-back) arrives through recently_read
        FB["entries"].append(fb_entry(60, "/article/b60", created="2019-01-01T00:00:00.000000Z"))
        FB["recent"] = [60, 1]
        T.H.calls = []
        reader = Feedbin(FB_URL, "me@example.com", "fb-pass", gap=0, lookback_days=14,
                         clock=lambda: unix("2025-10-25T00:00:00Z"))
        self.run_import(reader)
        self.assertEqual([d["url"] for d in T.adds()], [T.WEB + "/article/b60"])
        since = [r[2]["since"][0] for r in REQ if r[1] == "/v2/entries.json" and "since" in r[2]]
        self.assertEqual(since[-1], "2025-10-11T00:00:00.000000Z")

    def test_bad_password(self):
        with self.assertRaises(ReaderError):
            list(Feedbin(FB_URL, "me@example.com", "wrong", gap=0).starred(lambda i: False))


class Build(unittest.TestCase):
    def test_build_each_reader(self):
        d = tempfile.mkdtemp(prefix="feed-import-build-")
        secret = os.path.join(d, "s")
        with open(secret, "w") as f:
            f.write("x\n")
        env = {"FEED_IMPORT_HISTER_URL": "http://hister:4433", "FEED_IMPORT_DATA": d,
               "FEED_IMPORT_READERS": "newsblur,miniflux,freshrss,feedbin",
               "FEED_IMPORT_NEWSBLUR_TOKEN_FILE": secret, "FEED_IMPORT_NEWSBLUR_URL": "https://nb.example",
               "FEED_IMPORT_MINIFLUX_URL": "https://mf.example",
               "FEED_IMPORT_MINIFLUX_TOKEN_FILE": secret, "FEED_IMPORT_FRESHRSS_URL": "https://fr.example",
               "FEED_IMPORT_FRESHRSS_USER": "owner", "FEED_IMPORT_FRESHRSS_PASSWORD_FILE": secret,
               "FEED_IMPORT_FEEDBIN_USER": "me@example.com", "FEED_IMPORT_FEEDBIN_PASSWORD_FILE": secret}
        imp, _ = feedimport.build(env)
        imp.store.db.close()
        self.assertEqual([r.name for r in imp.readers], ["newsblur", "miniflux", "freshrss", "feedbin"])
        for missing in ("FEED_IMPORT_MINIFLUX_TOKEN_FILE", "FEED_IMPORT_FRESHRSS_USER", "FEED_IMPORT_FEEDBIN_PASSWORD_FILE"):
            with self.assertRaises(SystemExit):
                feedimport.build({k: v for k, v in env.items() if k != missing})
        with self.assertRaises(SystemExit):
            feedimport.build(dict(env, FEED_IMPORT_READERS="inoreader"))


if __name__ == "__main__":
    unittest.main()
