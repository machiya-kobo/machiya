"""python3 -m unittest discover -s tests  (needs markdown + pyyaml; run in any service image)."""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")

import vaultkit                       # noqa: E402
from vaultkit import verify           # noqa: E402

NOTES = {
    "Projects/Widget.md": "---\ntitle: Widget\ntags: [type/project, topic/hobby]\npublish: true\n---\n# Widget\n\n"
                         "A long enough first paragraph about Widget, the project.\n\nSee [[Repos/widget.git|widget]].\n",
    "Repos/widget.git.md": "---\ntags:\n  - type/repo\n---\nThe repo behind [[Widget]].\n",
    "Systems/router.md": "---\ntags: [machine/router]\n---\nLinks to [[widget.git]] and [[Nowhere]].\n\n![[pic.png|200]]\n",
    "Templates/Project.md": "---\ntitle: {{title}}\n---\nnever indexed\n",
    "Inbox/Idea (phone conflict 2026-01-14).md": "---\ntitle: copy\n---\nconflict copy\n",
    "CLAUDE.md": "agent notes\n",
    "Loose.md": "No frontmatter. > [!note] hi\n\n> [!warning] Careful\n\n> [!tip]\n> Body of a callout without a title\n\n| a | b |\n|---|---|\n| [[Widget\\|the widget]] | [[Widget#Why\\|why]] |\n",
}


def sh(*args, cwd=None):
    subprocess.run(args, cwd=cwd, check=True, capture_output=True)


def make_repo(root, subdir="personal"):
    base = os.path.join(root, subdir)
    for rel, text in NOTES.items():
        path = os.path.join(base, rel)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w") as f:
            f.write(text)
    os.makedirs(os.path.join(base, "Attachments"), exist_ok=True)
    with open(os.path.join(base, "Attachments", "pic.png"), "wb") as f:
        f.write(b"\x89PNG")
    sh("git", "init", "-q", "-b", "main", cwd=root)
    sh("git", "-c", "user.name=t", "-c", "user.email=t@t", "add", "-A", cwd=root)
    sh("git", "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "-m", "init", "--date", "2026-01-02T00:00:00",
       cwd=root)


class VaultTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        make_repo(self.tmp)
        self.v = vaultkit.Vault(self.tmp, "personal")
        self.v.revision = "r1"
        self.v.index()

    def tearDown(self):
        shutil.rmtree(self.tmp)

    def test_skips(self):
        self.assertNotIn("Templates/Project.md", self.v.notes)
        self.assertNotIn("CLAUDE.md", self.v.notes)
        self.assertFalse(any("phone conflict" in r for r in self.v.notes))
        self.assertIn("Loose.md", self.v.notes)

    def test_resolution_is_stable(self):
        # the rule <vault>/Repos/<name>.git.md relies on
        self.assertEqual(self.v.resolve("Repos/widget.git"), "Repos/widget.git.md")
        self.assertEqual(self.v.resolve("widget.git"), "Repos/widget.git.md")
        self.assertEqual(self.v.resolve("Widget"), "Projects/Widget.md")
        self.assertEqual(self.v.resolve("widget"), "Projects/Widget.md")
        self.assertEqual(self.v.resolve("projects/widget.md"), "Projects/Widget.md")
        self.assertEqual(self.v.resolve("Elsewhere/Widget"), "Projects/Widget.md")   # last segment fallback
        self.assertIsNone(self.v.resolve("Nowhere"))

    def test_links_and_backlinks(self):
        self.assertEqual(self.v.notes["Projects/Widget.md"].links, {"Repos/widget.git.md"})
        self.assertEqual(self.v.backlinks["Repos/widget.git.md"], {"Projects/Widget.md", "Systems/router.md"})

    def test_note_fields(self):
        n = self.v.notes["Projects/Widget.md"]
        self.assertTrue(n.published)
        self.assertEqual(n.ntype, "project")
        self.assertEqual(n.stage, "budding")
        self.assertEqual(n.slug, "Projects/Widget")
        self.assertTrue(n.description.startswith("A long enough first paragraph"))
        self.assertEqual(self.v.tended["Projects/Widget.md"], "2026-01-02")

    def test_render_modes(self):
        n = self.v.notes["Systems/router.md"]
        garden = self.v.render(n, "/garden")
        self.assertIn('<span class="seed" title="not in the garden">widget.git</span>', garden)
        self.assertIn('<img src="/garden/a/Attachments/pic.png" alt="pic.png" width="200">', garden)
        kura = self.v.render(n, "", mode="kura")
        self.assertIn('<a class="wikilink" href="/n/Repos/widget.git">widget.git</a>', kura)
        self.assertEqual(kura, self.v.render(n, "", mode="all"))
        self.assertIn('class="seed"', kura)                      # [[Nowhere]] stays text
        f = self.v.render(self.v.notes["Repos/widget.git.md"], "/garden")
        self.assertIn('<a class="wikilink" href="/garden/n/Projects/Widget">Widget</a>', f)
        loose = self.v.render(self.v.notes["Loose.md"], "")
        self.assertIn("<strong>Warning:</strong> Careful", loose)
        # a callout without a title keeps its next line as the body (the title used to swallow it)
        self.assertIn("<strong>Tip:</strong><br>\nBody of a callout without a title", loose)
        self.assertNotIn("&gt; Body", loose)
        # Obsidian's table form of an alias, [[Note\\|alias]], resolves like [[Note|alias]]
        self.assertIn('<a class="wikilink" href="/n/Projects/Widget">the widget</a>', loose)
        kura_loose = self.v.render(self.v.notes["Loose.md"], "", mode="kura")
        self.assertIn('<a class="wikilink" href="/n/Projects/Widget">the widget</a>', kura_loose)
        self.assertIn('<a class="wikilink" href="/n/Projects/Widget#Why">why</a>', kura_loose)

    def test_assets(self):
        self.assertTrue(self.v.asset_path("Attachments/pic.png").endswith("personal/Attachments/pic.png"))
        self.assertIsNone(self.v.asset_path("../etc/pic.png"))

    def test_reindex_on_key(self):
        with open(os.path.join(self.tmp, "personal", "New.md"), "w") as f:
            f.write("new\n")
        self.v.index()
        self.assertNotIn("New.md", self.v.notes)                 # same key: cached
        self.v.revision = "r2"
        self.v.index()
        self.assertIn("New.md", self.v.notes)


class TagsTest(unittest.TestCase):
    def test_scalar_tags(self):
        from vaultkit.front import tags_of
        self.assertEqual(tags_of({"tags": 2025}), ["2025"])            # YAML reads `tags: 2025` as an int
        self.assertEqual(tags_of({"tags": "a, #b"}), ["a", "b"])
        self.assertEqual(tags_of({"tags": ["x", 7, None]}), ["x", "7"])
        self.assertEqual(tags_of({"tags": None}), [])
        self.assertEqual(tags_of({}), [])
        self.assertEqual(tags_of(None), [])


class PrefixTest(unittest.TestCase):
    """v0.8: render/link_md take a prefix for Kura's other vaults; "" must leave the output byte-identical."""

    def test_prefix(self):
        tmp = tempfile.mkdtemp()
        try:
            make_repo(tmp)
            v = vaultkit.Vault(tmp, "personal")
            v.index()
            for note in v.notes.values():
                self.assertEqual(v.render(note, "", mode="kura"), v.render(note, "", mode="kura", prefix=""))
                self.assertEqual(v.render(note, "https://g", mode="garden"), v.render(note, "https://g", mode="garden", prefix=""))
            some = next(iter(v.notes.values()))
            html = v.link_md(some.rel[:-3], None, None, "", mode="kura", prefix="/v/work")
            self.assertIn('href="/v/work/n/', html)
        finally:
            shutil.rmtree(tmp)


class MirrorTest(unittest.TestCase):
    def test_clone_then_follow(self):
        tmp = tempfile.mkdtemp()
        try:
            src = os.path.join(tmp, "src")
            os.makedirs(src)
            make_repo(src)
            m = vaultkit.Mirror("file://" + src, os.path.join(tmp, "copy"))
            head, changed = m.update()
            self.assertTrue(changed and head)
            self.assertEqual(m.update(), (head, False))
            with open(os.path.join(src, "personal", "Loose.md"), "a") as f:
                f.write("more\n")
            sh("git", "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qam", "more", cwd=src)
            with open(os.path.join(tmp, "copy", "personal", "Loose.md"), "a") as f:
                f.write("local junk\n")                          # a mirror throws local edits away
            head2, changed = m.update()
            self.assertTrue(changed and head2 != head)
            with open(os.path.join(tmp, "copy", "personal", "Loose.md")) as f:
                self.assertNotIn("local junk", f.read())
        finally:
            shutil.rmtree(tmp)

    def test_borrow_shares_objects_and_sparse(self):
        tmp = tempfile.mkdtemp()
        try:
            src = os.path.join(tmp, "src")
            os.makedirs(src)
            make_repo(src)
            mirror = os.path.join(tmp, "mirror")
            vaultkit.Mirror("file://" + src, mirror).update()
            app = os.path.join(tmp, "app")
            sh("git", "clone", "-q", "--no-local", src, app, cwd=tmp)
            self.assertFalse(vaultkit.borrow(app, mirror, ("personal",)))       # adopted
            self.assertTrue(vaultkit.borrow(app, mirror, ("personal",)))        # idempotent
            count = subprocess.run(["git", "count-objects", "-v"], cwd=app, check=True, capture_output=True,
                                   text=True).stdout
            self.assertIn("in-pack: 0", count)                                  # every object is the mirror's
            self.assertEqual(sorted(d for d in os.listdir(app) if os.path.isdir(os.path.join(app, d))),
                             [".git", "personal"])
            with open(os.path.join(app, "personal", "New.md"), "w") as f:
                f.write("new\n")
            sh("git", "add", "personal/New.md", cwd=app)
            sh("git", "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "new", cwd=app)
            sh("git", "fsck", "--connectivity-only", cwd=app)
            with self.assertRaises(FileNotFoundError):
                vaultkit.borrow(app, os.path.join(tmp, "nowhere"))
        finally:
            shutil.rmtree(tmp)

    def test_auth_env_keeps_token_out_of_argv(self):
        env = vaultkit.auth_env("s3cret")
        self.assertEqual(env["GIT_CONFIG_KEY_0"], "http.extraHeader")
        self.assertNotIn("s3cret", env["GIT_CONFIG_VALUE_0"])   # base64'd, never plain
        self.assertEqual(vaultkit.auth_env(""), {})


class MigrationNamesTest(unittest.TestCase):
    """Legacy and current field names are both read (docs/frontmatter.md); the current ones win."""

    def test_created_and_status(self):
        from vaultkit.notes import created_of, note_status, stage_of
        self.assertEqual(created_of({"date": "2026-01-02"}), "2026-01-02")
        self.assertEqual(created_of({"created": "2026-03-04", "date": "2026-01-02"}), "2026-03-04")
        self.assertEqual(note_status({"tags": ["status/draft"]}), "draft")
        self.assertEqual(note_status({"status": "active", "tags": ["status/draft"]}), "active")
        self.assertEqual(note_status({"board": "wip", "tags": ["status/active"]}), "wip")
        self.assertEqual(note_status({"status": "done", "board": "wip"}), "done")
        self.assertEqual(stage_of({"status": "draft"}), "seedling")
        self.assertEqual(stage_of({"tags": ["status/draft"]}), "seedling")
        self.assertEqual(stage_of({"status": "active"}), "budding")
        self.assertEqual(stage_of({"board": "backlog", "tags": ["status/draft"]}), "seedling")   # legacy names
        self.assertEqual(stage_of({"status": "backlog"}), "seedling")                          # current names
        self.assertEqual(vaultkit.Note("a.md", {"created": "2026-05-06"}, "").planted, "2026-05-06")


class FrontmatterTest(unittest.TestCase):
    def test_edit_keeps_everything_else(self):
        text = "---\ntitle: A\ntags:\n  - topic/x\n# a comment\nnext: do it\n---\nBody [[x]]\n"
        out = vaultkit.edit_front(text, {"publish": True, "next": None, "growth": "evergreen"})
        self.assertEqual(out, "---\ntitle: A\ntags:\n  - topic/x\n# a comment\npublish: true\ngrowth: evergreen\n---\nBody [[x]]\n")
        self.assertEqual(vaultkit.edit_front(out, tags=["a/b"]).split("---")[1].count("a/b"), 1)
        with self.assertRaises(vaultkit.EditError):
            vaultkit.edit_front("no frontmatter", {"x": 1})

    def test_merge_note(self):
        base = "---\ntitle: A\ngrowth: seedling\n---\nbody\n"
        ours = "---\ntitle: A\ngrowth: budding\n---\nbody\n"
        theirs = "---\ntitle: A\ngrowth: seedling\nsummary: new\n---\nnew body\n"
        m = vaultkit.merge_note(base, ours, theirs)
        self.assertIn("growth: budding", m)
        self.assertIn("summary: new", m)
        self.assertTrue(m.endswith("new body\n"))


def git(cwd, *args):
    return subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", *args], cwd=cwd, check=True,
                          capture_output=True, text=True).stdout


class GitSyncTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        src = os.path.join(self.tmp, "src")
        os.makedirs(src)
        make_repo(src)
        self.remote = os.path.join(self.tmp, "remote.git")
        git(self.tmp, "clone", "-q", "--bare", src, self.remote)
        self.ours = os.path.join(self.tmp, "ours")
        self.theirs = os.path.join(self.tmp, "theirs")
        git(self.tmp, "clone", "-q", self.remote, self.ours)
        git(self.tmp, "clone", "-q", self.remote, self.theirs)
        self.pulled = []
        self.sync = vaultkit.GitSync(self.ours, ("garden", "garden@test"), ["personal", ".garden"],
                                     events_dir=".garden/events", label="garden", on_pull=self.pulled.append)

    def tearDown(self):
        shutil.rmtree(self.tmp)

    def edit(self, repo, rel, fn):
        path = os.path.join(repo, "personal", rel)
        with open(path) as f:
            text = f.read()
        with open(path, "w") as f:
            f.write(fn(text))

    def test_commit_push(self):
        self.edit(self.ours, "Projects/Widget.md", lambda t: vaultkit.edit_front(t, {"growth": "evergreen"}))
        os.makedirs(os.path.join(self.ours, ".garden", "events"))
        with open(os.path.join(self.ours, ".garden", "events", "2026-09.jsonl"), "a") as f:
            f.write('{"type": "garden"}\n')
        self.sync.touch("garden Widget")
        self.sync.commit()
        self.assertTrue(self.sync.pull())
        self.sync.push()
        self.assertEqual(self.sync.ahead(), 0)
        log = git(self.remote, "log", "-1", "--format=%an %s")
        self.assertEqual(log.strip(), "garden garden: 1 change (garden Widget)")
        self.assertIn(".garden/events/*.jsonl merge=union", git(self.remote, "show", "HEAD:.gitattributes"))

    def test_conflicting_upstream_edit_is_replayed(self):
        # upstream (a laptop) edits the same frontmatter line region and the body
        self.edit(self.theirs, "Projects/Widget.md",
                  lambda t: t.replace("publish: true", "publish: true\nsummary: from the laptop").replace("the project.", "the project!"))
        git(self.theirs, "commit", "-qam", "laptop edit")
        git(self.theirs, "push", "-q")
        self.edit(self.ours, "Projects/Widget.md", lambda t: vaultkit.edit_front(t, {"publish": False, "growth": "evergreen"}))
        self.sync.touch("unpublish Widget")
        self.sync.commit()
        self.assertTrue(self.sync.pull())
        self.sync.push()
        with open(os.path.join(self.ours, "personal", "Projects", "Widget.md")) as f:
            text = f.read()
        self.assertIn("summary: from the laptop", text)       # theirs kept
        self.assertIn("publish: false", text)                 # ours kept
        self.assertIn("growth: evergreen", text)
        self.assertIn("the project!", text)                   # the body is upstream's
        self.assertNotIn("<<<<<<<", text)
        self.assertEqual(self.sync.ahead(), 0)
        self.assertTrue(self.pulled)


class ShellTest(unittest.TestCase):
    ENV = {"MACHIYA_ROOMS": "shiori=https://shiori.t/, konbini=https://konbini.t,niwa=https://niwa.t,kura=https://kura.t,"
                            "hister=https://hister.t,searxng=https://searxng.t"}

    def test_rooms_and_switcher(self):
        from vaultkit import shell
        links = shell.rooms(self.ENV)
        self.assertEqual(links["shiori"], "https://shiori.t")
        menu = shell.switcher("kura", links)
        self.assertIn('<b data-room="kura"><span class="seal icon" data-room="kura" title="Kura (蔵)" aria-hidden="true">蔵</span>Kura', menu)   # here, not a link
        css = open(os.path.join(ROOT, "ui", "machiya.css"), encoding="utf-8").read()
        for room in ("shiori", "konbini", "niwa", "kura", "hister", "searxng"):         # every icon is drawn
            self.assertIn('.seal.icon[data-room="%s"] { background-image: url("data:image/' % room, css)
        self.assertLess(menu.index("Shiori"), menu.index("Konbini"))                          # front to back
        self.assertLess(menu.index("Niwa"), menu.index("Hister"))                             # neighbours last
        self.assertIn('href="https://searxng.t/" data-room="searxng"', menu)
        self.assertEqual(shell.switcher("kura", {}), "")                                       # standalone: none
        self.assertEqual(shell.rooms({}), {})

    def test_page_header_tabs_footer(self):
        from vaultkit import shell
        links = shell.rooms(self.ENV)
        ctx = shell.prefs("theme=auto; textSize=large")
        self.assertEqual((ctx.theme, ctx.text), ("system", "large"))
        body = shell.header("niwa", [("/", "home", "Niwa"), ("/tags", "tags", "Tags")], "tags", links)
        body += shell.footer("niwa", {"text": "synced abc1234 · 3 notes", "state": "stale"}, [("/api/status", "Status")])
        html = shell.page(ctx, "niwa", "t", body, tabs=[("/", "niwa", "Niwa")], current="niwa", links=links)
        self.assertIn('class="theme-system room-niwa" data-room="niwa" data-text="large"', html)
        self.assertIn('<span class="seal icon" data-room="niwa" title="Niwa (庭)" aria-hidden="true">庭</span><span class="word">Niwa</span>', html)
        self.assertIn('<b class="here">Tags</b>', html)
        self.assertIn('href="/settings"', html)
        self.assertIn("<span>Rooms</span>", html)                                              # the phone Rooms tab
        self.assertIn('<a href="/settings"><span class="neighbour">', html)                    # Settings on phones
        self.assertIn('class="status stale">Niwa · synced abc1234', html)
        self.assertLess(html.index("/static/machiya.css"), html.index("</head>"))
        self.assertRegex(html, r'/static/machiya\.css\?v=[0-9a-f]{10}"')              # versioned by content

    def test_settings_page(self):
        from vaultkit import shell
        ctx = shell.Prefs("day", "small")
        page = shell.settings_page([shell.appearance_section(ctx),
                                    ("Reading", [shell.toggle("Preview Pane", "previewPane", True, cookie=True)],
                                     "Preview Pane shows a note beside the list."),
                                    shell.apps_section("kura", shell.rooms(self.ENV), {"hister": False}),
                                    shell.about_section("kura", "0.3.0", "synced abc 3 min ago")], "kura")
        self.assertIn('<option value="day" selected>Tokyo Night Day</option>', page)
        self.assertIn('<option value="small" selected>Small</option>', page)
        self.assertIn('data-set="previewPane" data-cookie checked', page)
        self.assertIn('data-set="show_hister">', page)                                         # off
        self.assertNotIn('show_kura', page)                                                     # not itself
        self.assertIn("<h2>About</h2>", page)

class SourceLinkTest(unittest.TestCase):
    """MACHIYA_SOURCE_URL: the AGPL section 13 offer of the source, in the footer and in About, only when set."""

    def with_url(self, value):
        from unittest import mock
        env = {k: v for k, v in os.environ.items() if k != "MACHIYA_SOURCE_URL"}
        if value is not None:
            env["MACHIYA_SOURCE_URL"] = value
        return mock.patch.dict(os.environ, env, clear=True)

    def test_unset_changes_nothing(self):
        from vaultkit import shell
        with self.with_url(None):
            self.assertEqual(shell.source_url(), "")
            self.assertEqual(shell.footer("kura"), '<footer class="foot"><span>Part of Machiya</span></footer>')
            self.assertEqual(shell.footer("kura", {"text": "synced", "state": "ok"}, [("/a", "A")]),
                             '<footer class="foot"><span class="status">Kura · synced</span><a href="/a">A</a>'
                             '<span>Part of Machiya</span></footer>')
            rows = shell.about_section("kura", "0.3.0", "synced abc", "v0.9.0 (abc)")[1]
            self.assertEqual(len(rows), 3)
            self.assertNotIn("Source", "".join(rows))
            self.assertNotIn("Licence", "".join(rows))

    def test_set_shows_a_link_in_the_footer_and_about(self):
        from vaultkit import shell
        with self.with_url("https://github.com/example/kura?a=1&b=2"):
            foot = shell.footer("kura", None, [("/s", "Settings")])
            self.assertIn('<a href="https://github.com/example/kura?a=1&amp;b=2" rel="noopener">Source code</a>', foot)
            self.assertTrue(foot.index("Settings") < foot.index("Source code") < foot.index("Part of Machiya"))
            rows = "".join(shell.about_section("kura", "0.3.0")[1])
            self.assertIn(">Source code</span>", rows)
            self.assertIn('href="https://github.com/example/kura?a=1&amp;b=2"', rows)
            self.assertIn("GNU AGPL-3.0-or-later", rows)

    def test_only_a_plain_http_address_counts(self):
        from vaultkit import shell
        for bad in ("javascript:alert(1)", "ftp://x/y", "//evil.example", "https://a b", 'https://x/"onmouseover="',
                    "https://x/<script>", "https://x/'y", "   ", ""):
            with self.with_url(bad):
                self.assertEqual(shell.source_url(), "", bad)
                self.assertNotIn("Source code", shell.footer("kura"))
        with self.with_url("  http://forge.example/o/r  "):
            self.assertEqual(shell.source_url(), "http://forge.example/o/r")
        self.assertEqual(shell.source_url({"MACHIYA_SOURCE_URL": "https://x.example/r"}), "https://x.example/r")
        self.assertEqual(shell.source_url({}), "")



class SharedUITest(unittest.TestCase):
    def setUp(self):
        import importlib
        self.shell = importlib.import_module("vaultkit.shell")

    def test_shared_cookie_wins(self):
        p = self.shell.prefs("theme=day; machiya_theme=night; textSize=small; machiya_show_hister=false; group=family")
        self.assertEqual((p.theme, p.text), ("night", "small"))
        self.assertEqual(p.extra.get("show_hister"), "false")
        self.assertEqual(p.extra.get("group"), "family")
        self.assertTrue(self.shell.is_shared("show_niwa") and not self.shell.is_shared("group"))

    def test_search_box_and_handoff(self):
        box = self.shell.search_box("a <b>", placeholder="Search Cards")
        self.assertIn('class="search"', box)
        self.assertIn('value="a &lt;b&gt;"', box)
        links = {"shiori": "https://shiori.t"}
        h = self.shell.handoff("vault sync & more", links)
        self.assertIn('href="https://shiori.t/#/search?q=vault%20sync%20%26%20more"', h)
        self.assertEqual(self.shell.handoff("x", {}), "")
        self.assertEqual(self.shell.handoff("", links), "")

    def test_service_worker_and_offline_row(self):
        js = self.shell.service_worker("abc123", ["/static/kura.css?v=1", "/offline"], notes={"match": "^/n/", "limit": 200},
                                       network=["^/search"], pins="/api/offline")
        first, rest = js.split("\n", 1)
        self.assertEqual(first, 'importScripts("/static/machiya-sw.js?v=%s");' % self.shell.UI_VERSION["machiya-sw.js"])
        cfg = json.loads(rest.strip()[len("machiyaSW("):-2])
        self.assertEqual(cfg["version"], "abc123-" + self.shell.UI_VERSION["machiya-sw.js"])   # a new core renames the cache
        self.assertEqual((cfg["precache"], cfg["notes"]["limit"], cfg["pins"]), (["/static/kura.css?v=1", "/offline"], 200, "/api/offline"))
        self.assertNotEqual(self.shell.UI_VERSION["machiya-sw.js"], "0")                       # the core ships in ui/
        row = self.shell.offline_row()
        self.assertIn("data-clear-offline", row)
        self.assertIn("Clear Offline Copies", row)
        self.assertEqual(self.shell.OFFLINE_PIN, '<meta name="machiya-offline" content="pin">')

    def test_cookie_domain_on_body(self):
        old = self.shell.COOKIE_DOMAIN
        try:
            self.shell.COOKIE_DOMAIN = "tail.ts.net"
            html = self.shell.page(self.shell.prefs(""), "kura", "t", "", links={})
            self.assertIn('data-cookie-domain="tail.ts.net"', html)
            self.shell.COOKIE_DOMAIN = ""
            self.assertNotIn("data-cookie-domain", self.shell.page(self.shell.prefs(""), "kura", "t", "", links={}))
        finally:
            self.shell.COOKIE_DOMAIN = old


class EnvFileTest(unittest.TestCase):
    TEXT = """# Kura on a server
KURA_BIND=127.0.0.1
export KURA_PORT = 8080
KURA_USERS="me@passkey"
KURA_TOKEN='a b#c $HOME'
KURA_NOTE=plain  # a comment after the value
KURA_HASH=abc#def
KURA_EMPTY=
KURA_PATH=$HOME/x ~/y \\n
"""

    def test_parse_rules(self):
        from vaultkit import envfile
        env = dict(envfile.parse(self.TEXT))
        self.assertEqual(env["KURA_BIND"], "127.0.0.1")
        self.assertEqual(env["KURA_PORT"], "8080")                      # export prefix, spaces around =
        self.assertEqual(env["KURA_USERS"], "me@passkey")
        self.assertEqual(env["KURA_TOKEN"], "a b#c $HOME")              # quoted: literal
        self.assertEqual(env["KURA_NOTE"], "plain")
        self.assertEqual(env["KURA_HASH"], "abc#def")                   # no space before #: part of the value
        self.assertEqual(env["KURA_EMPTY"], "")
        self.assertEqual(env["KURA_PATH"], "$HOME/x ~/y \\n")         # no expansion
        for bad in ("just words", "1KEY=x", "KEY='open", "=x"):
            with self.assertRaises(envfile.EnvFileError):
                envfile.parse(bad)
        for bad in ("KURA TOKEN=s3cret-value", "KURA_TOKEN='s3cret-value"):     # errors never echo a secret
            with self.assertRaises(envfile.EnvFileError) as cm:
                envfile.parse(bad, "/etc/kura.env")
            self.assertNotIn("s3cret", str(cm.exception))
            self.assertIn("/etc/kura.env, line 1", str(cm.exception))

    def test_load_real_environment_wins(self):
        from vaultkit import envfile
        tmp = tempfile.mkdtemp()
        try:
            path = os.path.join(tmp, "kura.env")
            with open(path, "w") as f:
                f.write(self.TEXT)
            env = {"KURA_BIND": "0.0.0.0"}
            applied = envfile.load(path, env)
            self.assertEqual(env["KURA_BIND"], "0.0.0.0")               # already set: left alone
            self.assertNotIn("KURA_BIND", applied)
            self.assertEqual(env["KURA_PORT"], "8080")
            env2 = {"KURA_ENV_FILE": path}
            self.assertEqual(envfile.load_for("kura", [], env2), path)  # <APP>_ENV_FILE
            self.assertEqual(env2["KURA_USERS"], "me@passkey")
            env3 = {}
            self.assertEqual(envfile.load_for("kura", ["--env-file", path], env3), path)
            self.assertEqual(envfile.load_for("kura", ["--env-file=" + path], {}), path)
            self.assertEqual(envfile.load_for("kura", [], {}), "")      # none named: nothing to do
            with self.assertRaises(OSError):
                envfile.load(os.path.join(tmp, "missing.env"), {})
        finally:
            shutil.rmtree(tmp)


class VendorTest(unittest.TestCase):
    def test_vendor_and_drift(self):
        tmp = tempfile.mkdtemp()
        try:
            here = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
            sh("bash", os.path.join(here, "vendor.sh"), tmp)
            copy = os.path.join(tmp, "vaultkit")
            self.assertEqual(verify.check(copy), [])
            self.assertTrue(os.path.exists(os.path.join(copy, "ui", "machiya.css")))
            with open(os.path.join(copy, "ui", "machiya.css"), "a") as f:
                f.write("/* local tweak */\n")
            self.assertEqual(len(verify.check(copy)), 1)                                       # ui/ is checked too
            sh("bash", os.path.join(here, "vendor.sh"), tmp)
            self.assertEqual(verify.check(copy), [])
            with open(os.path.join(copy, "vault.py"), "a") as f:
                f.write("# local fix\n")
            self.assertEqual(len(verify.check(copy)), 1)
        finally:
            shutil.rmtree(tmp)


if __name__ == "__main__":
    unittest.main()
