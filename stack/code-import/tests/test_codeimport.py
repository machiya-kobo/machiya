"""code-import's tests: no network. Three local fakes on 127.0.0.1:
- Forgejo and GitHub: dev/fake_forgejo.py and dev/fake_github.py (the dev stack's fakes), on copies of their seeds:
  a fork, an archived repo, a mirror on each host, an excluded name, a private repo, an empty repo, twins on both hosts, a README with an
  invented token, a file named like a secret, hidden, vendored, test, example and sample-vault markdown, issues, PRs (one merged), releases (one a
  draft, one a prerelease). They record every request: the importer may only GET.
- Hister (/api/document, /api/add, /api/delete), which refuses calls without `Origin: hister://`, answers 422 when
  the html matches Hister's default sensitive patterns, and records everything.

Run, from stack/code-import: python3 -m unittest discover -s tests
"""
import copy
import json
import os
import re
import subprocess
import sys
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "dev"))

import codeimport                                               # noqa: E402
import hister as histermod                                      # noqa: E402
import render                                                   # noqa: E402
import secretscan                                               # noqa: E402
import tokens                                                   # noqa: E402
from fake_forgejo import FakeForgejo                            # noqa: E402
from fake_github import FakeGitHub                              # noqa: E402
from forgejo import Forgejo                                     # noqa: E402
from forges import Client, ForgeError                           # noqa: E402
from github import GitHub                                       # noqa: E402
from store import Store                                         # noqa: E402

FJ_TOKEN, GH_LANTERN, GH_WORKSHOP, HISTER_TOKEN = "fj-test-token", "gh-lantern-token", "gh-workshop-token", "hister-owner"
FJ_ROOT = "https://forgejo.example.ts.net"
GH_WEB = "https://github.example"
TWINS = "github:workshop-kobo=forgejo:workshop,forgejo:lantern=github:lantern"


def load(path):
    with open(path) as f:
        return json.load(f)


FJ_SEED = load(os.path.join(ROOT, "dev", "seed", "forgejo.json"))
GH_SEED = load(os.path.join(ROOT, "dev", "seed", "github.json"))
# Hister v0.20.0's default sensitive patterns (the ones that matter here), checked on html only, as Hister does.
HISTER_SENSITIVE = re.compile(r"AKIA[0-9A-Z]{16}|gh[pousr]_[A-Za-z0-9]{36}|-----BEGIN [A-Z ]*PRIVATE KEY-----")


def fj_url(path):
    return FJ_ROOT + path


def gh_url(path):
    return GH_WEB + path


# -- Hister ---------------------------------------------------------------------------------------------------------

class H:
    docs = {}            # url -> document
    calls = []           # (method, path, body, headers)
    down = False
    reject = ()          # URL substrings answered 400 (a final refusal)


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
        if self.headers.get("X-Access-Token") != HISTER_TOKEN:
            return self.send(403)
        if u.path == "/api/document":
            doc = H.docs.get(parse_qs(u.query)["url"][0])
            return self.send(200, doc) if doc else self.send(404)
        if u.path == "/api/add":
            if not body.get("skip_sensitive_check") and HISTER_SENSITIVE.search(body.get("html") or ""):
                return self.send(422)
            if any(x in body["url"] for x in H.reject):
                return self.send(400)
            H.docs[body["url"]] = dict(body)
            return self.send(201)
        if u.path == "/api/delete":
            m = re.fullmatch(r'url:"(.*)"', body["query"])
            n = 1 if m and H.docs.pop(m.group(1), None) else 0
            return self.send(200, {"deleted": n})
        self.send(404)

    def do_GET(self):
        self.handle_any("GET")

    def do_POST(self):
        self.handle_any("POST")


def serve(handler):
    srv = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=srv.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True).start()
    return srv, "http://127.0.0.1:%d" % srv.server_address[1]


H_SRV, H_URL = serve(HisterFake)


def adds():
    return [c[2] for c in H.calls if c[1] == "/api/add"]


def deletes():
    return [re.fullmatch(r'url:"(.*)"', c[2]["query"]).group(1) for c in H.calls if c[1] == "/api/delete"]


# -- the base -------------------------------------------------------------------------------------------------------

class Base(unittest.TestCase):
    def setUp(self):
        H.docs, H.calls, H.down, H.reject = {}, [], False, ()
        self.fj = FakeForgejo(copy.deepcopy(FJ_SEED), FJ_TOKEN, FJ_ROOT)
        self.gh = FakeGitHub(copy.deepcopy(GH_SEED), {GH_LANTERN: "lantern", GH_WORKSHOP: "workshop-kobo"}, GH_WEB)
        self.fj_srv, self.fj_api = self.fj.serve()
        self.gh_srv, self.gh_api = self.gh.serve()
        for srv in (self.fj_srv, self.gh_srv):
            self.addCleanup(srv.server_close)
            self.addCleanup(srv.shutdown)
        self.tmp = tempfile.mkdtemp(prefix="code-import-test-")
        self.store = Store(os.path.join(self.tmp, "state.sqlite3"))
        self.addCleanup(self.store.db.close)
        self.lines = []
        self.now = 1791200000

    def importer(self, dry_run=False, hister=True, store=None, timeout=5, **kw):
        store = store or self.store
        client = {"retry_delays": (0.01, 0.01, 0.01), "timeout": timeout}
        forges = [Forgejo(self.fj_api, FJ_TOKEN, ["lantern", "workshop"], gap=0, cache=store, **client),
                  GitHub(self.gh_api, [("lantern", GH_LANTERN), ("workshop-kobo", GH_WORKSHOP)], gap=0, cache=store,
                         **client)]
        h = histermod.Hister(H_URL, HISTER_TOKEN) if hister else None
        return codeimport.Importer(forges, store, h, rules=codeimport.Rules(twins=codeimport.parse_twins(TWINS)),
                                   dry_run=dry_run, out=self.lines.append, clock=lambda: self.now, **kw)

    def fj_repo(self, name):
        return next(r for r in self.fj.seed["repos"] if r["name"] == name)

    def gh_repo(self, name):
        return next(r for r in self.gh.seed["repos"] if r["name"] == name)

    def tearDown(self):
        for method, path, *_ in self.fj.requests:
            self.assertEqual(method, "GET", "code-import must only GET from Forgejo (%s %s)" % (method, path))
        for method, path, *_ in self.gh.requests:
            self.assertEqual(method, "GET", "code-import must only GET from GitHub (%s %s)" % (method, path))


EXPECTED = {
    fj_url("/lantern/dotfiles"), fj_url("/lantern/dotfiles/src/branch/main/README.md"),
    fj_url("/lantern/dotfiles/src/branch/main/docs/setup.md"), fj_url("/lantern/dotfiles/releases/tag/v1.0"),
    fj_url("/lantern/dotfiles/issues/1"),
    fj_url("/lantern/garden-notes"), fj_url("/lantern/garden-notes/src/branch/main/README.md"),
    fj_url("/lantern/garden-notes/src/branch/main/docs/deploy.md"), fj_url("/lantern/garden-notes/issues/1"),
    fj_url("/lantern/garden-notes/pulls/2"), fj_url("/lantern/garden-notes/pulls/3"),
    fj_url("/lantern/empty-repo"),
    gh_url("/workshop-kobo/lamp"), gh_url("/workshop-kobo/lamp/blob/main/README.md"),
    gh_url("/workshop-kobo/lamp/blob/main/docs/api.md"), gh_url("/workshop-kobo/lamp/releases/tag/v0.1.0"),
    gh_url("/workshop-kobo/lamp/releases/tag/v0.2.0-rc1"), gh_url("/workshop-kobo/lamp/issues/1"),
    gh_url("/workshop-kobo/lamp/pull/2"),
    gh_url("/lantern/pixel-font"), gh_url("/lantern/pixel-font/blob/main/README.md"),
    gh_url("/lantern/pixel-font/issues/4"),
}


# -- units ------------------------------------------------------------------------------------------------------------

class Units(unittest.TestCase):
    def test_repo_key_is_one_token(self):
        self.assertEqual(codeimport.repo_key("machiya-kobo", "kura"), "machiya_kobo__kura")
        self.assertEqual(codeimport.repo_key("owner", "owner.com"), "owner__owner_com")
        self.assertEqual(codeimport.repo_key("owner", "Dot--Files"), "owner__dot_files")
        for owner, name in (("machiya-kobo", "kura"), ("a.b", "c:d"), ("x", "2048-game")):
            self.assertRegex(codeimport.repo_key(owner, name), r"^[a-z0-9_]+$")

    def test_twins_setting(self):
        self.assertEqual(codeimport.parse_twins(TWINS), [(("github", "workshop-kobo"), ("forgejo", "workshop")),
                                                         (("forgejo", "lantern"), ("github", "lantern"))])
        self.assertEqual(codeimport.parse_twins(""), [])
        for bad in ("github:a", "github:a=github:b", "gitlab:a=github:b"):
            with self.assertRaises(SystemExit):
                codeimport.parse_twins(bad)

    def test_secret_scan(self):
        text, kinds = secretscan.redact("token ghp_" + "A1b2" * 9 + " and AKIAABCDEFGHIJKLMNOP here")
        self.assertNotIn("ghp_", text)
        self.assertNotIn("AKIA", text)
        self.assertEqual(kinds, ["aws-key", "github-token"])
        text, kinds = secretscan.redact("a\n-----BEGIN OPENSSH PRIVATE KEY-----\nb3BlbnNzaC1rZXk\n-----END OPENSSH PRIVATE KEY-----\nz")
        self.assertEqual((text, kinds), ("a\n[redacted]\nz", ["private-key"]))
        text, kinds = secretscan.redact("DB_PASSWORD=Xk29fQz81LmPw7Rt4Va")
        self.assertEqual((text, kinds), ("DB_PASSWORD=[redacted]", ["assignment"]))
        text, kinds = secretscan.redact("push to https://bot:s3cretValue99@git.example/x.git")
        self.assertEqual(kinds, ["url-password"])
        for harmless in ("password: changeme", "API_KEY=<your-api-key-goes-here>", "TOKEN_FILE=/run/secrets/token-file",
                         "token = ${GITHUB_TOKEN_FROM_THE_ENVIRONMENT}", "the secret garden is lovely",
                         "HISTER_TOKEN_FILE=/data/secrets/owner-token"):
            self.assertEqual(secretscan.redact(harmless), (harmless, []), harmless)

    def test_secret_names(self):
        for name in ("secrets.md", "notes/SECRETS.md", ".env", "deploy/.env.prod", "id_ed25519", "key.pem", "x.age"):
            self.assertTrue(secretscan.secret_name(name), name)
        for name in ("README.md", "docs/secret-garden-guide.md", "docs/keys.md"):
            self.assertFalse(secretscan.secret_name(name), name)

    def test_render_escapes(self):
        page = render.page("<b>T</b>", "https://x.example/a?b=1&c=2", "Issue", "Hi <script>evil()</script>\n\n# Head\n\n```\n<x>\n```")
        self.assertNotIn("<script>", page)
        self.assertNotIn("<b>T</b>", page)
        self.assertIn("&lt;script&gt;", page)
        self.assertIn("<h2>Head</h2>", page)
        self.assertIn("<pre><code>&lt;x&gt;</code></pre>", page)

    def test_norm_matches_hister(self):
        self.assertEqual(histermod.norm("https://a.example/p?b=2&utm_source=x&a=1#frag"), "https://a.example/p?a=1&b=2")
        self.assertEqual(histermod.norm("https://a.example/p#x"), "https://a.example/p")

    def test_pause_window(self):
        w = codeimport.pause_window("Sun 02:20-02:50")
        self.assertTrue(codeimport.in_window(w, "UTC", now=1791685500))      # Sun 2026-10-11 02:25 UTC
        self.assertFalse(codeimport.in_window(w, "UTC", now=1791685500 + 3600))
        with self.assertRaises(SystemExit):
            codeimport.pause_window("Sun 03:00-02:00")


class Tokens(unittest.TestCase):
    def test_token_file(self):
        d = tempfile.mkdtemp(prefix="code-import-tok-")
        self.assertIsNone(tokens.token_file("", "X_TOKEN_FILE"))
        with self.assertRaises(SystemExit):
            tokens.token_file("", "X_TOKEN_FILE", required=True)
        with self.assertRaises(SystemExit):
            tokens.token_file(os.path.join(d, "missing"), "X_TOKEN_FILE")
        empty = os.path.join(d, "empty")
        open(empty, "w").close()
        with self.assertRaises(SystemExit) as e:
            tokens.token_file(empty, "X_TOKEN_FILE")
        self.assertIn("doesn't hold a token", str(e.exception))
        path = os.path.join(d, "tok")
        with open(path, "w") as f:
            f.write("first-token\n")
        s = tokens.token_file(path, "X_TOKEN_FILE")
        self.assertEqual(s.get(), "first-token")
        self.assertNotIn("first-token", repr(s))
        with open(path + ".new", "w") as f:
            f.write("second-token\n")
        os.replace(path + ".new", path)
        self.assertEqual(s.get(), "second-token")                           # rotated: re-read
        os.remove(path)
        self.assertEqual(s.get(), "second-token")                           # vanished: the last good value

    def test_no_redirects_with_a_token(self):
        hits = []

        class Elsewhere(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_GET(self):
                hits.append(self.headers.get("Authorization") or self.headers.get("X-Access-Token"))
                self.send_response(200)
                self.end_headers()
                self.wfile.write(b"[]")

        other_srv, other = serve(Elsewhere)

        class Mover(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_GET(self):
                self.send_response(302)
                self.send_header("Location", other + "/stolen")
                self.send_header("Content-Length", "0")
                self.end_headers()

        mover_srv, mover = serve(Mover)
        try:
            with self.assertRaises(ForgeError) as e:
                Client(mover, "secret-forge-token", "token", gap=0).get("/user")
            self.assertIn("redirect", str(e.exception))
            with self.assertRaises(histermod.HisterDown):
                histermod.Hister(mover, "secret-hister-token").document("https://x.example/")
            self.assertEqual(hits, [])
        finally:
            for srv in (other_srv, mover_srv):
                srv.shutdown()
                srv.server_close()


# -- the pipeline -------------------------------------------------------------------------------------------------------

class Pipeline(Base):
    def test_first_run(self):
        stats = self.importer().run()
        sent = {d["url"]: d for d in adds()}
        self.assertEqual(set(sent), EXPECTED)
        self.assertEqual(stats["forgejo"]["repo left out: fork"], 1)
        self.assertEqual(stats["forgejo"]["repo left out: archived"], 1)
        self.assertEqual(stats["forgejo"]["repo left out: mirror"], 1)
        self.assertEqual(stats["github"]["repo left out: mirror"], 1)
        self.assertEqual(stats["forgejo"]["repo left out: excluded"], 1)          # obsidian
        self.assertEqual(stats["forgejo"]["repo left out: twin"], 1)              # workshop/lamp: GitHub's wins
        self.assertEqual(stats["github"]["repo left out: twin"], 1)               # lantern/dotfiles: Forgejo's wins
        self.assertEqual(stats["github"]["repo left out: excluded"], 1)           # backup
        self.assertEqual(stats["forgejo"]["refused (secret name)"], 1)            # notes/secrets.md, never read
        for url, d in sent.items():
            self.assertTrue(d["html"].startswith("<!DOCTYPE html>"), url)
            self.assertNotIn("skip_sensitive_check", d)
            m = d["metadata"]
            self.assertEqual((m["source"], m["client"], m["ignore_skip_rules"]), ("code", "code-import", True))
            self.assertIn(m["code_host"], ("forgejo", "github"))
            self.assertIn(m["code_private"], ("true", "false"))
            self.assertRegex(m["code_repo"], r"^[a-z0-9_]+$")
            self.assertEqual(d.get("label", ""), "")
        for method, path, body, headers in H.calls:
            self.assertEqual(headers.get("Origin"), "hister://")
            self.assertEqual(headers.get("X-Access-Token"), HISTER_TOKEN)

        pr = sent[fj_url("/lantern/garden-notes/pulls/2")]["metadata"]
        self.assertEqual((pr["code_kind"], pr["code_state"], pr["code_private"], pr["code_repo"], pr["code_number"]),
                         ("pr", "merged", "true", "lantern__garden_notes", "2"))
        self.assertEqual(sent[gh_url("/workshop-kobo/lamp/pull/2")]["metadata"]["code_state"], "merged")
        self.assertEqual(sent[gh_url("/workshop-kobo/lamp/issues/1")]["metadata"]["code_state"], "open")
        self.assertEqual(sent[fj_url("/lantern/garden-notes/pulls/3")]["metadata"]["code_state"], "open")
        lamp = sent[gh_url("/workshop-kobo/lamp")]
        self.assertEqual(lamp["metadata"]["code_twin_url"], fj_url("/workshop/lamp"))
        self.assertEqual(lamp["metadata"]["code_topics"], ["lighting", "controller"])
        self.assertIn("dims the stone lanterns", lamp["text"])
        self.assertEqual(sent[gh_url("/workshop-kobo/lamp/releases/tag/v0.2.0-rc1")]["metadata"]["code_prerelease"], "true")
        self.assertEqual(sent[fj_url("/lantern/dotfiles/issues/1")]["added"], 1789200000)    # the issue's updated_at

        readme = sent[fj_url("/lantern/garden-notes/src/branch/main/README.md")]
        self.assertNotIn("ghp_", readme["html"] + readme["text"])                 # our scan redacted it
        self.assertIn("[redacted]", readme["html"])
        self.assertEqual(readme["metadata"]["code_redacted"], "true")
        self.assertIn("Moss beds", readme["text"])
        self.assertNotIn("print(", json.dumps(adds()))                            # no code bodies (phase 2)
        self.assertNotIn("never imported", json.dumps(adds()))                    # tests/, examples/, sample-vault/, …

        auths = {a for m, p, q, a, s in self.gh.requests if p.startswith("/repos/workshop-kobo/")}
        self.assertEqual(auths, {"Bearer " + GH_WORKSHOP})                        # each owner's own token
        auths = {a for m, p, q, a, s in self.gh.requests if p.startswith("/repos/lantern/")}
        self.assertEqual(auths, {"Bearer " + GH_LANTERN})
        self.assertEqual({r[3] for r in self.fj.requests}, {"token " + FJ_TOKEN})

    def test_second_run_is_quiet(self):
        self.importer().run()
        H.calls, self.gh.not_modified = [], 0
        before = len(self.fj.requests)
        self.now += 900
        stats = self.importer().run()
        self.assertEqual(adds(), [])
        self.assertEqual(deletes(), [])
        self.assertEqual([c for c in H.calls if c[0] == "POST"], [])
        self.assertGreater(self.gh.not_modified, 0)                               # conditional GETs: free 304s
        searches = [q for m, p, q, a in self.fj.requests if p == "/api/v1/repos/issues/search"]
        self.assertIn("since", searches[-1])                                      # the cursor
        trees = [p for m, p, q, a in self.fj.requests[before:] if "/git/trees/" in p]
        self.assertEqual(trees, [])                                               # unchanged repos: no tree walk
        self.assertNotIn("would add", stats.get("forgejo", {}))

    def test_changes(self):
        self.importer().run()
        H.calls = []
        self.now += 900
        issue = self.fj_repo("dotfiles")["issues"][0]
        issue.update(state="closed", updated_at="2026-10-04T09:00:00Z")
        r = self.fj_repo("garden-notes")
        r["files"]["docs/deploy.md"] += "\nNow with a frost cover.\n"
        r["files"]["docs/new.md"] = "# A new page\n"
        r["updated_at"] = "2026-10-04T09:00:00Z"
        self.importer().run()
        sent = {d["url"]: d for d in adds()}
        self.assertEqual(set(sent), {fj_url("/lantern/dotfiles/issues/1"),
                                     fj_url("/lantern/garden-notes/src/branch/main/docs/deploy.md"),
                                     fj_url("/lantern/garden-notes/src/branch/main/docs/new.md")})
        self.assertEqual(sent[fj_url("/lantern/dotfiles/issues/1")]["metadata"]["code_state"], "closed")
        self.assertIn("frost cover", sent[fj_url("/lantern/garden-notes/src/branch/main/docs/deploy.md")]["text"])

    def test_withdrawals(self):
        self.importer().run()
        H.calls = []
        self.now += 900
        r = self.fj_repo("garden-notes")
        del r["files"]["docs/deploy.md"]
        r["updated_at"] = "2026-10-04T09:00:00Z"
        lamp = self.gh_repo("lamp")
        lamp["releases"] = lamp["releases"][1:]                                   # v0.1.0 deleted
        lamp["pushed_at"] = "2026-10-04T09:00:00Z"
        self.gh.seed["repos"].remove(self.gh_repo("pixel-font"))                  # a repo deleted
        self.fj_repo("dotfiles")["archived"] = True                               # a repo archived
        self.importer().run()
        gone = set(deletes())
        self.assertEqual(gone, {fj_url("/lantern/garden-notes/src/branch/main/docs/deploy.md"),
                                gh_url("/workshop-kobo/lamp/releases/tag/v0.1.0"),
                                gh_url("/lantern/pixel-font"), gh_url("/lantern/pixel-font/blob/main/README.md"),
                                gh_url("/lantern/pixel-font/issues/4"),
                                fj_url("/lantern/dotfiles"), fj_url("/lantern/dotfiles/src/branch/main/README.md"),
                                fj_url("/lantern/dotfiles/src/branch/main/docs/setup.md"),
                                fj_url("/lantern/dotfiles/releases/tag/v1.0"), fj_url("/lantern/dotfiles/issues/1")})
        for url in gone:
            self.assertNotIn(url, H.docs)
            self.assertIsNone(self.store.doc(url))
        self.assertEqual(adds(), [])

    def test_twin_switch_withdraws_the_loser(self):
        """GitHub's lantern/dotfiles is a twin while Forgejo has dotfiles; once Forgejo's is gone, GitHub's is imported,
        and the Forgejo documents are withdrawn."""
        self.importer().run()
        H.calls = []
        self.fj.seed["repos"].remove(self.fj_repo("dotfiles"))
        self.importer().run()
        self.assertIn(fj_url("/lantern/dotfiles/issues/1"), deletes())
        self.assertIn(gh_url("/lantern/dotfiles"), {d["url"] for d in adds()})

    def test_rename(self):
        self.importer().run()
        H.calls = []
        self.now += 900
        self.gh_repo("pixel-font")["name"] = "pixel-fonts"                       # same id, new name: new URLs
        self.importer().run()
        self.assertEqual(set(deletes()), {gh_url("/lantern/pixel-font"), gh_url("/lantern/pixel-font/blob/main/README.md"),
                                          gh_url("/lantern/pixel-font/issues/4")})
        self.assertEqual({d["url"] for d in adds()}, {gh_url("/lantern/pixel-fonts"),
                                                      gh_url("/lantern/pixel-fonts/blob/main/README.md"),
                                                      gh_url("/lantern/pixel-fonts/issues/4")})

    def test_deleted_issue_goes_on_a_full_run_only(self):
        self.importer().run()
        H.calls = []
        self.fj_repo("garden-notes")["issues"].pop(0)
        self.now += 900
        self.importer().run()
        self.assertEqual(deletes(), [])                                           # incremental: can't tell
        self.now += 21600
        self.importer().run()
        self.assertEqual(deletes(), [fj_url("/lantern/garden-notes/issues/1")])


class Dedupe(Base):
    def test_browsed_pages_are_left_alone(self):
        browsed = gh_url("/workshop-kobo/lamp/pull/2")
        H.docs[browsed] = {"url": browsed, "title": "Finer PWM steps by lantern", "label": "",
                           "metadata": {"description": "a page the owner opened"}}
        mine = gh_url("/lantern/pixel-font")                                      # ours, but the state was lost
        H.docs[mine] = {"url": mine, "title": "old", "metadata": {"source": "code"}}
        self.importer().run()
        urls = [d["url"] for d in adds()]
        self.assertNotIn(browsed, urls)
        self.assertIn(mine, urls)
        self.assertEqual(H.docs[browsed]["title"], "Finer PWM steps by lantern")
        self.assertEqual(self.store.doc(browsed)["status"], "known")
        # the PR changes: still the owner's page, still left alone; and it is never deleted
        H.calls = []
        pr = self.gh_repo("lamp")["issues"][1]
        pr["updated_at"] = "2026-10-04T10:00:00Z"
        self.now += 900
        self.importer().run()
        self.assertEqual(adds(), [])
        self.gh_repo("lamp")["issues"].pop(1)
        self.now += 21600
        self.importer().run()
        self.assertNotIn(browsed, deletes())
        self.assertIn(browsed, H.docs)

    def test_a_page_browsed_after_import_is_not_deleted(self):
        self.importer().run()
        url = gh_url("/lantern/pixel-font/issues/4")
        H.docs[url] = {"url": url, "title": "browsed since", "metadata": {}}       # the extension replaced ours
        H.calls = []
        self.gh_repo("pixel-font")["issues"] = []
        self.now += 21600
        self.importer().run()
        self.assertEqual(deletes(), [])
        self.assertIn(url, H.docs)
        self.assertIsNone(self.store.doc(url))


class Refusals(Base):
    def test_refuse_mode(self):
        self.importer(secrets="refuse").run()
        url = fj_url("/lantern/garden-notes/src/branch/main/README.md")
        self.assertNotIn(url, {d["url"] for d in adds()})
        self.assertEqual(self.store.doc(url)["status"], "refused")
        self.assertIn("github-token", self.store.doc(url)["error"])

    def test_hister_refusal_is_final_until_the_source_changes(self):
        H.reject = ("pixel-font/issues",)
        self.importer().run()
        url = gh_url("/lantern/pixel-font/issues/4")
        self.assertEqual(self.store.doc(url)["status"], "rejected")
        H.calls = []
        self.now += 900
        self.importer().run()
        self.assertEqual(adds(), [])
        H.reject = ()
        self.gh_repo("pixel-font")["issues"][0]["updated_at"] = "2026-10-04T00:00:00Z"
        self.importer().run()
        self.assertEqual([d["url"] for d in adds()], [url])

    def test_hister_sensitive_check_still_fires(self):
        """Our scan misses a pattern Hister knows? Hister's 422 still stops it, because html is always sent."""
        self.gh_repo("pixel-font")["files"]["README.md"] = "# x\n\nkey ghp_" + "Ab12" * 9 + "\n"
        orig = secretscan.redact
        secretscan.redact = lambda text: (text, [])
        try:
            self.importer().run()
        finally:
            secretscan.redact = orig
        url = gh_url("/lantern/pixel-font/blob/main/README.md")
        self.assertNotIn(url, H.docs)
        self.assertEqual(self.store.doc(url)["status"], "rejected")
        self.assertIn("422", self.store.doc(url)["error"])


class Failures(Base):
    def test_hister_down_stops_the_run_and_resumes(self):
        H.down = True
        with self.assertRaises(histermod.HisterDown):
            self.importer().run()
        self.assertEqual(self.store.counts(), {})
        H.down = False
        self.importer().run()
        self.assertEqual({d["url"] for d in adds()}, EXPECTED)

    def test_a_refused_forge_token_fails_its_sources_only(self):
        self.importer().run()
        H.calls = []
        self.fj.token = "rotated-elsewhere"
        self.gh_repo("pixel-font")["description"] = "A 5x7 pixel font, now with katakana"
        imp = self.importer()
        imp.run()
        self.assertFalse(imp.sources["forgejo:lantern"]["ok"])
        self.assertIn("401", imp.sources["forgejo:lantern"]["error"])
        self.assertFalse(imp.sources["forgejo:workshop"]["ok"])
        self.assertTrue(imp.sources["github:lantern"]["ok"])                       # the others go on
        self.assertTrue(imp.sources["github:workshop-kobo"]["ok"])
        self.assertEqual([d["url"] for d in adds()], [gh_url("/lantern/pixel-font")])
        self.assertEqual(deletes(), [])
        auth = [r for r in self.fj.requests if r[1] == "/api/v1/user"]
        self.assertEqual(len(auth), 3)              # the first run's, then once per Forgejo source: 401 isn't retried

    def test_an_empty_listing_withdraws_nothing(self):
        self.importer().run()
        H.calls = []
        self.gh.seed["repos"] = []
        imp = self.importer()
        imp.run()
        self.assertIn("listed no repos", imp.sources["github:lantern"]["error"])
        self.assertIn("listed no repos", imp.sources["github:workshop-kobo"]["error"])
        self.assertTrue(imp.sources["forgejo:lantern"]["ok"])
        self.assertEqual(deletes(), [])


LAMP_ALL = {u for u in EXPECTED if "/workshop-kobo/lamp" in u}
LAMP_KEPT = {gh_url("/workshop-kobo/lamp"), gh_url("/workshop-kobo/lamp/blob/main/README.md")}


class ReadmeOnly(Base):
    def test_setting(self):
        self.assertEqual(codeimport.parse_readme_only(" github:owner/big-fork, forgejo:a/b "),
                         {("github", "owner/big-fork"), ("forgejo", "a/b")})
        for bad in ("owner/qmk", "gitlab:a/b", "github:a", "github:a/b/c", "github:/b"):
            with self.assertRaises(SystemExit):
                codeimport.parse_readme_only(bad)

    def test_card_and_readme_only_and_never_capped(self):
        imp = self.importer(readme_only={("github", "workshop-kobo/lamp")}, max_docs=1)
        imp.run()
        sent = {d["url"] for d in adds()}
        self.assertEqual(sent & LAMP_ALL, LAMP_KEPT)
        self.assertIn(gh_url("/lantern/pixel-font/issues/4"), sent)                # the other repos as before
        self.assertNotIn("github:workshop-kobo/lamp", {c["repo"] for c in imp.caps()})
        self.assertIn("forgejo:lantern/dotfiles", {c["repo"] for c in imp.caps()})   # others still capped
        self.assertFalse([r for r in self.gh.requests if r[1].startswith("/repos/workshop-kobo/lamp/")
                          and r[1].endswith(("/issues", "/releases"))])

    def test_listing_a_repo_withdraws_its_other_documents_and_unlisting_brings_them_back(self):
        self.importer().run()
        H.calls = []
        self.now += 900                                     # an incremental run: the mode change forces the walk
        imp = self.importer(readme_only={("github", "workshop-kobo/lamp")})
        imp.run()
        self.assertEqual(set(deletes()), LAMP_ALL - LAMP_KEPT)
        self.assertEqual(adds(), [])
        for url in LAMP_KEPT:
            self.assertIn(url, H.docs)
        H.calls = []
        self.now += 900
        self.importer(readme_only={("github", "workshop-kobo/lamp")}).run()
        self.assertEqual((adds(), deletes()), ([], []))     # settled
        self.now += 900
        self.importer().run()
        self.assertEqual({d["url"] for d in adds()}, LAMP_ALL - LAMP_KEPT)

    def test_a_failed_source_withdraws_nothing_yet(self):
        self.importer().run()
        H.calls = []
        self.gh.faults = [["/repos/workshop-kobo/lamp/git/trees", "503", None]]
        imp = self.importer(readme_only={("github", "workshop-kobo/lamp")})
        imp.run()
        self.assertFalse(imp.sources["github:workshop-kobo"]["ok"])
        self.assertEqual(deletes(), [])


class Retries(Base):
    def test_a_timeout_once_then_success(self):
        self.gh.faults = [["/repos/lantern/pixel-font/releases", "sleep", 1]]
        self.gh.sleep = 1.0
        imp = self.importer(timeout=0.4)
        imp.run()
        self.assertTrue(all(s["ok"] for s in imp.sources.values()), imp.sources)
        self.assertEqual({d["url"] for d in adds()}, EXPECTED)
        self.assertEqual(sum(f.retries for f in imp.forges), 1)

    def test_resets_5xx_and_429_are_tried_again_404_is_not(self):
        self.fj.faults = [["/api/v1/repos/issues/search", "reset", 1], ["/releases", "503", 2]]
        self.gh.faults = [["/orgs/workshop-kobo/repos", "429", 1]]
        imp = self.importer()
        imp.run()
        self.assertTrue(all(s["ok"] for s in imp.sources.values()), imp.sources)
        self.assertEqual({d["url"] for d in adds()}, EXPECTED)
        self.assertEqual(sum(f.retries for f in imp.forges), 4)
        c = Client(self.gh_api, GH_LANTERN, "Bearer", gap=0, retry_delays=(0.01,) * 3)
        with self.assertRaises(ForgeError):
            c.get("/repos/nobody/nothing")
        self.assertEqual(c.retries, 0)
        self.assertEqual(len([r for r in self.gh.requests if r[1] == "/repos/nobody/nothing"]), 1)

    def test_a_long_retry_after_fails_the_call(self):
        c = Client(self.gh_api, GH_LANTERN, "Bearer", gap=0, retry_delays=(0.01,) * 3, max_wait=10)
        self.gh.faults = [["/user", "429", None]]
        orig = self.gh.fault
        self.gh.fault = lambda path: orig(path)
        with self.assertRaises(ForgeError) as e:
            c.get("/user")
        self.assertIn("4 tries", str(e.exception))                                # Retry-After 0: tried 4 times

    def test_gives_up_after_the_last_try(self):
        self.gh.faults = [["/repos/workshop-kobo/lamp/issues", "503", None]]
        imp = self.importer()
        imp.run()
        self.assertFalse(imp.sources["github:workshop-kobo"]["ok"])
        self.assertIn("HTTP 503 (4 tries)", imp.sources["github:workshop-kobo"]["error"])


class Sources(Base):
    def test_a_source_failing_part_way_withdraws_nothing(self):
        self.importer().run()
        H.calls = []
        self.now += 900
        lamp = self.gh_repo("lamp")
        del lamp["files"]["docs/api.md"]                                          # gone: would be withdrawn
        lamp["releases"] = lamp["releases"][1:]
        lamp["pushed_at"] = "2026-10-04T09:00:00Z"
        self.gh.faults = [["/repos/workshop-kobo/lamp/issues", "503", None]]      # fails after the docs pass
        self.gh_repo("pixel-font")["description"] = "changed"
        self.fj_repo("garden-notes")["issues"][0].update(state="open", updated_at="2026-10-04T09:00:00Z")
        imp = self.importer()
        imp.run()
        self.assertFalse(imp.sources["github:workshop-kobo"]["ok"])
        self.assertTrue(imp.sources["github:lantern"]["ok"] and imp.sources["forgejo:lantern"]["ok"])
        self.assertEqual(deletes(), [])                                           # held back
        self.assertIn(gh_url("/workshop-kobo/lamp/blob/main/docs/api.md"), H.docs)
        self.assertIsNotNone(self.store.doc(gh_url("/workshop-kobo/lamp/blob/main/docs/api.md")))
        self.assertEqual({d["url"] for d in adds()}, {gh_url("/lantern/pixel-font"),          # the good sources land
                                                      fj_url("/lantern/garden-notes/issues/1")})
        self.assertIn("held back", "\n".join(self.lines))
        # the forge recovers: the next run withdraws what the failed one held back
        self.gh.faults = []
        H.calls = []
        self.now += 900
        imp = self.importer()
        imp.run()
        self.assertTrue(all(s["ok"] for s in imp.sources.values()), imp.sources)
        self.assertEqual(set(deletes()), {gh_url("/workshop-kobo/lamp/blob/main/docs/api.md"),
                                          gh_url("/workshop-kobo/lamp/releases/tag/v0.1.0")})

    def test_a_source_gone_quiet_keeps_its_documents(self):
        self.importer().run()
        H.calls = []
        self.gh.faults = [["/user/repos", "503", None]]                           # github:lantern can't be listed
        self.gh.seed["repos"].remove(self.gh_repo("pixel-font"))
        imp = self.importer()
        imp.run()
        self.assertFalse(imp.sources["github:lantern"]["ok"])
        self.assertEqual(deletes(), [])
        self.assertIn(gh_url("/lantern/pixel-font"), H.docs)

    def test_twins_wait_for_a_winner_never_seen(self):
        self.gh.faults = [["/orgs/workshop-kobo/repos", "503", None]]
        imp = self.importer()
        imp.run()
        self.assertIn("waiting for github:workshop-kobo", imp.sources["forgejo:workshop"]["error"])
        self.assertFalse([d for d in adds() if "/workshop/" in d["url"]])         # Forgejo's lamp is a twin
        self.assertTrue(imp.sources["forgejo:lantern"]["ok"] and imp.sources["github:lantern"]["ok"])
        self.gh.faults = []
        self.importer().run()                                                     # seen once...
        H.calls = []
        self.gh.faults = [["/orgs/workshop-kobo/repos", "503", None]]
        imp = self.importer()
        imp.run()                                                                 # ...its last listing still counts
        self.assertTrue(imp.sources["forgejo:workshop"]["ok"])
        self.assertFalse([d for d in adds() if "/workshop/" in d["url"]])
        self.assertEqual(deletes(), [])

    def test_caps_are_named(self):
        imp = self.importer(max_docs=1)
        imp.run()
        caps = {c["repo"]: c for c in imp.caps()}
        self.assertEqual(caps["forgejo:lantern/dotfiles"], {"repo": "forgejo:lantern/dotfiles", "docs": 2, "max": 1})
        self.assertIn("github:workshop-kobo/lamp", caps)
        imp = self.importer(max_docs=200)
        self.now += 21600
        imp.run(full=True)
        self.assertEqual(imp.caps(), [])


class DryRun(Base):
    def test_dry_run_writes_nothing(self):
        out = []
        imp = self.importer(dry_run=True, store=Store(":memory:"), limit=None)
        imp.out = out.append
        stats = imp.run(full=True)
        would = [json.loads(x)["would_add"]["url"] for x in out if x.startswith('{"would_add"')]
        self.assertEqual(set(would), EXPECTED)
        self.assertEqual(stats["forgejo"]["would add"] + stats["github"]["would add"], len(EXPECTED))
        self.assertEqual([c for c in H.calls if c[0] == "POST"], [])              # Hister only asked
        self.assertEqual(self.store.counts(), {})


# -- the command line, as the image runs it ---------------------------------------------------------------------------

class CommandLine(Base):
    def env(self, **kw):
        files = {}
        for name, value in (("fj", FJ_TOKEN), ("ghl", GH_LANTERN), ("ghw", GH_WORKSHOP), ("hister", HISTER_TOKEN)):
            files[name] = os.path.join(self.tmp, name)
            with open(files[name], "w") as f:
                f.write(value + "\n")
        env = {"PATH": os.environ.get("PATH", ""), "CODE_IMPORT_DATA": os.path.join(self.tmp, "data"),
               "CODE_IMPORT_FORGEJO_URL": self.fj_api, "CODE_IMPORT_FORGEJO_TOKEN_FILE": files["fj"],
               "CODE_IMPORT_FORGEJO_OWNERS": "lantern,workshop", "CODE_IMPORT_GITHUB_API": self.gh_api,
               "CODE_IMPORT_GITHUB_TOKEN_FILES": "lantern=%s,workshop-kobo=%s" % (files["ghl"], files["ghw"]),
               "CODE_IMPORT_TWINS": TWINS, "CODE_IMPORT_GAP": "0", "CODE_IMPORT_HISTER_URL": H_URL,
               "CODE_IMPORT_RETRY_DELAYS": "0.01,0.01,0.01",
               "CODE_IMPORT_HISTER_TOKEN_FILE": files["hister"]}
        env.update(kw)
        return {k: v for k, v in env.items() if v is not None}

    def run_cli(self, args, env):
        return subprocess.run([sys.executable, os.path.join(ROOT, "codeimport.py")] + args, env=env,
                              capture_output=True, text=True, timeout=60)

    def test_once_then_status(self):
        p = self.run_cli(["--once"], self.env())
        self.assertEqual(p.returncode, 0, p.stderr + p.stdout)
        self.assertEqual({d["url"] for d in adds()}, EXPECTED)
        status = load(os.path.join(self.tmp, "data", "status.json"))
        self.assertTrue(status["ok"])
        self.assertIsNone(status["error"])
        self.assertEqual(status["version"], codeimport.VERSION)
        self.assertEqual(status["counts"]["github"]["pr"], {"added": 1})
        for token in (FJ_TOKEN, GH_LANTERN, GH_WORKSHOP, HISTER_TOKEN):
            self.assertNotIn(token, p.stdout + p.stderr)
        self.assertNotIn("Moss", p.stdout + p.stderr)                             # logs carry counts, not text

    def test_once_with_a_failing_source(self):
        self.gh.faults = [["/orgs/workshop-kobo/repos", "503", None]]
        p = self.run_cli(["--once"], self.env(CODE_IMPORT_MAX_DOCS="1"))
        self.assertEqual(p.returncode, 1, p.stderr + p.stdout)
        status = load(os.path.join(self.tmp, "data", "status.json"))
        self.assertFalse(status["ok"])
        self.assertIn("github:workshop-kobo", status["error"])
        self.assertIn("HTTP 503 (4 tries)", status["sources"]["github:workshop-kobo"]["error"])
        self.assertIn("waiting for github:workshop-kobo", status["sources"]["forgejo:workshop"]["error"])
        self.assertTrue(status["sources"]["forgejo:lantern"]["ok"] and status["sources"]["github:lantern"]["ok"])
        self.assertTrue(status["sources"]["github:lantern"]["last_success"])
        self.assertIsNone(status["last_success"])                                 # not every source succeeded
        self.assertIn({"repo": "forgejo:lantern/dotfiles", "docs": 2, "max": 1}, status["caps"])
        self.assertIn(fj_url("/lantern/dotfiles"), {d["url"] for d in adds()})   # the good sources landed
        self.assertFalse([d for d in adds() if "workshop" in d["url"]])
        self.gh.faults = []
        p = self.run_cli(["--once"], self.env())
        self.assertEqual(p.returncode, 0, p.stderr + p.stdout)
        status = load(os.path.join(self.tmp, "data", "status.json"))
        self.assertTrue(status["ok"] and all(s["ok"] for s in status["sources"].values()))
        H.calls = []
        p = self.run_cli(["--once"], self.env(CODE_IMPORT_README_ONLY="github:workshop-kobo/lamp"))
        self.assertEqual(p.returncode, 0, p.stderr + p.stdout)
        self.assertEqual(set(deletes()), LAMP_ALL - LAMP_KEPT)

    def test_once_fails_when_hister_is_down(self):
        H.down = True
        p = self.run_cli(["--once"], self.env())
        self.assertEqual(p.returncode, 1)
        status = load(os.path.join(self.tmp, "data", "status.json"))
        self.assertFalse(status["ok"])
        self.assertIn("HTTP 503", status["error"])

    def test_dry_run(self):
        p = self.run_cli(["--dry-run", "--limit", "2"], self.env())
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertIn('"would_add"', p.stdout)
        self.assertIn("nothing was written", p.stdout)
        self.assertEqual([c for c in H.calls if c[0] == "POST"], [])
        self.assertFalse(os.path.exists(os.path.join(self.tmp, "data")))
        p = self.run_cli(["--dry-run", "--repo", "workshop-kobo/lamp", "--limit", "50"], self.env(CODE_IMPORT_HISTER_URL=None,
                                                                                                    CODE_IMPORT_HISTER_TOKEN_FILE=None))
        self.assertEqual(p.returncode, 0, p.stderr)
        urls = {json.loads(x)["would_add"]["url"] for x in p.stdout.splitlines() if x.startswith('{"would_add"')}
        self.assertEqual(urls, {u for u in EXPECTED if "/workshop-kobo/lamp" in u})

    def test_dry_run_with_one_failing_source(self):
        self.gh.faults = [["/user/repos", "503", None]]
        p = self.run_cli(["--dry-run", "--limit", "50"], self.env())
        self.assertEqual(p.returncode, 1)
        self.assertIn("dry run failed for github:lantern", p.stderr)
        self.assertIn(fj_url("/lantern/dotfiles"), p.stdout)                      # the others are still shown

    def test_dry_run_fails_on_a_forge_error(self):
        env = self.env()
        with open(env["CODE_IMPORT_FORGEJO_TOKEN_FILE"], "w") as f:
            f.write("wrong-token\n")
        p = self.run_cli(["--dry-run"], env)
        self.assertEqual(p.returncode, 1)
        self.assertIn("dry run failed", p.stderr)
        self.assertNotIn("wrong-token", p.stderr)

    def test_bad_settings_refuse_to_start(self):
        empty = os.path.join(self.tmp, "empty")
        open(empty, "w").close()
        cases = [
            ({"CODE_IMPORT_FORGEJO_URL": None, "CODE_IMPORT_GITHUB_TOKEN_FILES": None}, "no forge"),
            ({"CODE_IMPORT_FORGEJO_TOKEN_FILE": None}, "CODE_IMPORT_FORGEJO_TOKEN_FILE is required"),
            ({"CODE_IMPORT_FORGEJO_TOKEN_FILE": empty}, "doesn't hold a token"),
            ({"CODE_IMPORT_HISTER_TOKEN_FILE": empty}, "doesn't hold a token"),
            ({"CODE_IMPORT_GITHUB_TOKEN_FILES": "lantern"}, "owner=/path/to/token"),
            ({"CODE_IMPORT_HISTER_URL": None}, "CODE_IMPORT_HISTER_URL is required"),
            ({"CODE_IMPORT_SECRETS": "ignore"}, "redact or refuse"),
            ({"CODE_IMPORT_TWINS": "github:a"}, "host:owner=host:owner"),
            ({"CODE_IMPORT_FORGEJO_OWNERS": None}, "names no owner"),
            ({"CODE_IMPORT_RETRY_DELAYS": "2,soon"}, "CODE_IMPORT_RETRY_DELAYS"),
            ({"CODE_IMPORT_README_ONLY": "workshop-kobo/lamp"}, "host:owner/repo"),
        ]
        for overrides, message in cases:
            p = self.run_cli(["--once"], self.env(**overrides))
            self.assertEqual(p.returncode, 1, overrides)
            self.assertIn(message, p.stderr, overrides)
        self.assertEqual(adds(), [])


if __name__ == "__main__":
    unittest.main()
