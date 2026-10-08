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


class IndexSwapTest(unittest.TestCase):
    """v0.28: a rebuild is built aside and put in place at the end; while it runs, readers see the previous index whole
    (its notes, links, backlinks and dates together), never new notes beside old dates or a half-filled backlink map."""

    def test_readers_see_the_old_index_until_the_new_one_is_ready(self):
        tmp = tempfile.mkdtemp()
        try:
            make_repo(tmp)
            seen = []

            class Watched(vaultkit.Vault):
                def tended_dates(inner):                     # late in the build: what a reader would see right now
                    if hasattr(inner, "notes"):
                        seen.append((inner.notes, inner.backlinks, inner.tended))
                    return super().tended_dates()

            v = Watched(tmp, "personal")
            v.revision = "r1"
            v.index()
            before = (v.notes, v.backlinks, v.tended)
            v.revision = "r2"
            v.index()
            self.assertEqual(len(seen), 1)
            for got, old in zip(seen[0], before):
                self.assertIs(got, old)                      # still the previous index, all of it
            self.assertIsNot(v.notes, before[0])            # and the new one is in place afterwards
        finally:
            shutil.rmtree(tmp)


class IncrementalReadTest(unittest.TestCase):
    """v0.28: read_notes re-reads only the files whose stat changed; after adds, edits, deletes and renames its result
    and the index built from it must equal a full read from scratch."""

    def snapshot(self, root):
        v = vaultkit.Vault(root)
        v.revision = "x"
        v.tended_dates = lambda: {}
        v.index()
        return ([(r, n.title, n.tags, sorted(n.links), n.fm, n.text) for r, n in v.notes.items()],
                v.by_name, {k: sorted(b) for k, b in v.backlinks.items()})

    def fresh(self, root):
        from vaultkit import notes
        saved = dict(notes._READ)
        notes._READ.clear()
        try:
            return notes.read_notes(root), self.snapshot(root)
        finally:
            notes._READ.clear()
            notes._READ.update(saved)

    def test_changes_are_picked_up_exactly(self):
        from vaultkit import notes
        tmp = tempfile.mkdtemp()
        try:
            def write(rel, text):
                full = os.path.join(tmp, rel)
                os.makedirs(os.path.dirname(full), exist_ok=True)
                with open(full, "w") as f:
                    f.write(text)
            for i in range(6):
                write("N/n%d.md" % i, "---\ntitle: N%d\ntags: [a]\n---\nsee [[n%d]] and [[gone]]\n" % (i, (i + 1) % 6))
            notes.read_notes(tmp)                           # warm the cache
            write("N/n1.md", "---\ntitle: Edited\ntags: [b]\n---\nnow [[n4]]\n")                       # edit
            write("N/n1b.md", "---\ntitle: Same size\n---\nx\n")                                    # add
            write("N/gone.md", "---\ntitle: Gone\n---\nnew target for the old links\n")             # add a target
            os.remove(os.path.join(tmp, "N/n2.md"))                                                  # delete
            os.makedirs(os.path.join(tmp, "M"))
            os.rename(os.path.join(tmp, "N/n3.md"), os.path.join(tmp, "M/n3.md"))                    # rename
            st = os.stat(os.path.join(tmp, "N/n4.md"))
            write("N/n4.md", "---\ntitle: N4\ntags: [a]\n---\nsee [[n5]] and [[gone]]\n".replace("N4", "Z4"))   # same size, same mtime
            os.utime(os.path.join(tmp, "N/n4.md"), ns=(st.st_atime_ns, st.st_mtime_ns))
            got = notes.read_notes(tmp), self.snapshot(tmp)
            self.assertEqual(got, self.fresh(tmp))
            self.assertIn("Z4", dict((r, t) for r, _, t in got[0])["N/n4.md"])    # the change time caught it
        finally:
            shutil.rmtree(tmp)

    def test_cached_frontmatter_is_a_copy(self):
        from vaultkit import notes
        tmp = tempfile.mkdtemp()
        try:
            with open(os.path.join(tmp, "a.md"), "w") as f:
                f.write("---\ntitle: A\ntags: [x]\n---\nbody\n")
            notes.read_notes(tmp)
            first = notes.read_notes(tmp)[0][1]
            first["tags"].append("mutated")
            self.assertEqual(notes.read_notes(tmp)[0][1], {"title": "A", "tags": ["x"]})
        finally:
            shutil.rmtree(tmp)


class SanitizeTest(unittest.TestCase):
    """vaultkit.sanitize (v0.13): a note's HTML never runs, in a page or an API answer."""

    def clean(self, markup, **kw):
        from vaultkit.sanitize import clean
        return clean(markup, **kw)

    def test_scripts_handlers_and_frames_go(self):
        for bad in ('<script>alert(1)</script>', '<img src=x onerror=alert(1)>', '<svg onload=alert(1)>',
                    '<iframe src="https://e.example"></iframe>', '<style>*{}</style>', '<object data=x></object>',
                    '<form action=/signout method=post><button>go</button></form>', '<script/>alert(1)',
                    '<a href="javascript:alert(1)">x</a>', '<a href=" java\tscript:alert(1)">x</a>',
                    '<a href="JAVASCRIPT:alert(1)">x</a>', '<a href="data:text/html,<script>1</script>">x</a>',
                    '<img src="vbscript:x">', '<a href="//evil.example/">x</a>', '<div style="x" onclick="y">d</div>',
                    '<input type="text" value="x" onfocus=alert(1) autofocus>', '<body onload=alert(1)>'):
            out = self.clean(bad)
            for word in ("script", "onerror", "onload", "onclick", "onfocus", "iframe", "javascript", "data:",
                         "vbscript", "style", "evil", "<form", "<button", 'type="text"'):
                self.assertNotIn(word, out.lower(), (bad, out))

    def test_what_a_note_needs_stays(self):
        keep = ('<p><a class="wikilink" href="/n/A%20B">A</a> <a href="https://x.example/?a=1&amp;b=2">x</a> '
                '<a href="gemini://g.example/">g</a> <a href="obsidian://open?vault=v">o</a> <a href="#h">h</a></p>'
                '<img src="/a/i.png" alt="i" width="200"><pre><code class="language-mermaid">a --&gt; b</code></pre>'
                '<table border="1"><tr><td align="right">1</td></tr></table>')
        self.assertEqual(self.clean(keep), keep)
        api = self.clean(keep, base="https://kura.example", schemes=("http", "https", "mailto", "obsidian"))
        self.assertIn('href="https://kura.example/n/A%20B"', api)
        self.assertNotIn("gemini:", api)

    def test_task_boxes_are_disabled_checkboxes_only(self):
        self.assertEqual(self.clean('<input type="checkbox" checked>'), '<input type="checkbox" checked disabled>')
        self.assertEqual(self.clean('<input type="checkbox" name="n" onclick="x">'), '<input type="checkbox" disabled>')
        self.assertEqual(self.clean('<input type="image" src="x">'), "")

    def test_output_is_balanced(self):
        self.assertEqual(self.clean("<p><b>x</p>y"), "<p><b>x</b></p>y")
        self.assertEqual(self.clean("<div><em>x"), "<div><em>x</em></div>")
        self.assertEqual(self.clean("</div>x"), "x")

    def test_autolink(self):
        out = self.clean("<p>see https://e.example/a_(b)). or gopher://g.example:70/1x, "
                         "<code>http://no.example</code> <a href=\"https://a.example\">https://a.example</a></p>",
                         autolink=True)
        self.assertIn('<a href="https://e.example/a_(b)">https://e.example/a_(b)</a>).', out)
        self.assertIn('<a href="gopher://g.example:70/1x">', out)
        self.assertIn("<code>http://no.example</code>", out)
        self.assertEqual(out.count("a.example"), 2)                      # an existing link isn't linked again
        self.assertNotIn("<a", self.clean("<p>https://e.example</p>"))   # off unless asked


class RenderSafetyTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.v = vaultkit.Vault(self.tmp, git=lambda *a: "")

    def tearDown(self):
        shutil.rmtree(self.tmp)

    def render(self, text, **kw):
        return self.v.render(vaultkit.Note("N.md", {}, text), "", **kw)

    def test_raw_html_in_a_note_never_runs(self):
        out = self.render('hi <script>window.x=1</script><img src=x onerror="alert(1)">\n\n<div onclick="y">d</div>')
        self.assertNotIn("script", out)
        self.assertNotIn("onerror", out)
        self.assertNotIn("onclick", out)
        self.assertIn("<div>d</div>", out)

    def test_task_lists_and_autolinks(self):
        out = self.render("- [ ] todo\n- [x] done\n- plain\n\nread https://e.example/x.")
        self.assertIn('<li class="task"><input type="checkbox" disabled> todo</li>', out)
        self.assertIn('<li class="task"><input type="checkbox" checked disabled> done</li>', out)
        self.assertIn("<li>plain</li>", out)
        self.assertIn('<a href="https://e.example/x">https://e.example/x</a>.', out)

    def test_smallweb_autolinks(self):
        out = self.render("<gemini://g.example/a> and `<gopher://code.example>`\n\n```\n<gemini://fence.example>\n```")
        self.assertIn('<a href="gemini://g.example/a">gemini://g.example/a</a>', out)
        self.assertIn("<code>&lt;gopher://code.example&gt;</code>", out)
        self.assertIn("&lt;gemini://fence.example&gt;", out)

    def test_table_alignment_survives(self):
        out = self.render("| a | b |\n|:-|-:|\n| 1 | 2 |")
        self.assertIn('<td align="right">2</td>', out)
        self.assertNotIn("style", out)

    def test_retro_tables_keep_their_border(self):
        self.assertIn('<table border="1" cellpadding="4" cellspacing="0">', self.render("| a |\n|-|\n| 1 |", retro=True))


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

    def test_a_failed_fetch_is_reported(self):
        """MACH-F-4: update() used to return the old HEAD as if it had synced when the fetch failed."""
        tmp = tempfile.mkdtemp()
        try:
            src = os.path.join(tmp, "src")
            os.makedirs(src)
            make_repo(src)
            m = vaultkit.Mirror("file://" + src, os.path.join(tmp, "copy"))
            head, _ = m.update()
            self.assertEqual(m.failed, "")
            os.rename(src, src + ".gone")
            self.assertEqual(m.update(), (head, False))
            self.assertIn("fetch", m.failed)
            os.rename(src + ".gone", src)
            m.update()
            self.assertEqual(m.failed, "")
            broken = vaultkit.Mirror("file://" + os.path.join(tmp, "nowhere"), os.path.join(tmp, "copy2"))
            self.assertEqual(broken.update(), ("", False))
            self.assertIn("clone", broken.failed)
        finally:
            shutil.rmtree(tmp)

    def test_credentials_in_urls_are_redacted(self):
        """KURA-9: Git.run printed argv and git's stderr as they were."""
        from vaultkit.git import redact
        self.assertEqual(redact("fatal: https://user:s3cret@forge.example/x.git and ssh://git@h/x"),
                         "fatal: https://***@forge.example/x.git and ssh://***@h/x")
        g = vaultkit.Git(tempfile.gettempdir())
        g.run("ls-remote", "https://user:s3cret@127.0.0.1:9/x.git", timeout=10)
        self.assertTrue(g.error)
        self.assertNotIn("s3cret", g.error)

    def test_auth_env_keeps_token_out_of_argv(self):
        env = vaultkit.auth_env("s3cret")
        self.assertEqual(env["GIT_CONFIG_KEY_0"], "http.extraHeader")
        self.assertNotIn("s3cret", env["GIT_CONFIG_VALUE_0"])   # base64'd, never plain
        self.assertEqual(vaultkit.auth_env(""), {})


class SymlinkTest(unittest.TestCase):
    """KURA-2 (sweep 2026-10): a symlink committed to the vault is never followed: not read as a note, not indexed as an
    image, not written through, and not even checked out by a Mirror or a GitSync clone."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.secret = os.path.join(self.tmp, "secret.txt")
        with open(self.secret, "w") as f:
            f.write("TOKEN-OUTSIDE-THE-VAULT\n")
        self.src = os.path.join(self.tmp, "src")
        os.makedirs(self.src)
        make_repo(self.src)
        base = os.path.join(self.src, "personal")
        os.symlink(self.secret, os.path.join(base, "leak.md"))
        os.symlink(self.secret, os.path.join(base, "Attachments", "leak.png"))
        os.makedirs(os.path.join(self.tmp, "outside"))
        with open(os.path.join(self.tmp, "outside", "Far.md"), "w") as f:
            f.write("TOKEN-OUTSIDE-THE-VAULT\n")
        os.symlink(os.path.join(self.tmp, "outside"), os.path.join(base, "Linked"))
        sh("git", "-c", "user.name=t", "-c", "user.email=t@t", "add", "-A", cwd=self.src)
        sh("git", "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "links", cwd=self.src)

    def tearDown(self):
        shutil.rmtree(self.tmp)

    def test_read_notes_and_index_skip_links(self):
        rels = [r for r, _, _ in vaultkit.read_notes(os.path.join(self.src, "personal"))]
        self.assertNotIn("leak.md", rels)
        self.assertFalse([r for r in rels if r.startswith("Linked")])
        self.assertIn("Loose.md", rels)
        v = vaultkit.Vault(self.src, "personal")
        v.revision = "r"
        v.index()
        self.assertNotIn("leak.png", v.assets)
        self.assertIn("pic.png", v.assets)
        self.assertIsNone(v.asset_path("Attachments/leak.png"))
        self.assertIsNone(vaultkit.read_file(os.path.join(self.src, "personal", "leak.md")))
        self.assertIn("Widget", vaultkit.read_file(os.path.join(self.src, "personal", "Projects", "Widget.md")))

    def test_safe_path_refuses_links_and_escapes(self):
        root = os.path.join(self.src, "personal")
        self.assertEqual(vaultkit.safe_path(root, "Inbox/New.md"), os.path.join(os.path.realpath(root), "Inbox", "New.md"))
        for bad in ("leak.md", "Linked/Far.md", "Linked/New.md", "../x.md", "a/../../x.md", "/etc/passwd", "", "a//b.md",
                    "a/\0.md"):
            with self.assertRaises(ValueError, msg=bad):
                vaultkit.safe_path(root, bad)

    def test_mirror_never_checks_out_links(self):
        copy = os.path.join(self.tmp, "copy")
        vaultkit.Mirror("file://" + self.src, copy).update()
        self.assertFalse(os.path.islink(os.path.join(copy, "personal", "leak.md")))
        with open(os.path.join(copy, "personal", "leak.md")) as f:
            self.assertNotIn("TOKEN", f.read())                    # the link's target name, not the file
        texts = [t for _, _, t in vaultkit.read_notes(os.path.join(copy, "personal"))]
        self.assertFalse([t for t in texts if "TOKEN-OUTSIDE" in t])

    def test_an_existing_clone_with_links_is_switched_over(self):
        copy = os.path.join(self.tmp, "old")
        sh("git", "-c", "core.symlinks=true", "clone", "-q", self.src, copy, cwd=self.tmp)     # made before v0.22
        self.assertTrue(os.path.islink(os.path.join(copy, "personal", "leak.md")))
        vaultkit.Mirror("file://" + self.src, copy).update()
        self.assertFalse(os.path.islink(os.path.join(copy, "personal", "leak.md")))
        self.assertFalse(os.path.islink(os.path.join(copy, "personal", "Linked")))

    def test_gitsync_clone_drops_links_and_replay_never_writes_through_one(self):
        remote = os.path.join(self.tmp, "remote.git")
        sh("git", "clone", "-q", "--bare", self.src, remote, cwd=self.tmp)
        ours = os.path.join(self.tmp, "ours")
        sh("git", "-c", "core.symlinks=true", "clone", "-q", remote, ours, cwd=self.tmp)
        sync = vaultkit.GitSync(ours, ("t", "t@t"), ["personal"])
        self.assertTrue(sync.pull())
        self.assertFalse(os.path.islink(os.path.join(ours, "personal", "leak.md")))
        with open(self.secret) as f:
            self.assertEqual(f.read(), "TOKEN-OUTSIDE-THE-VAULT\n")


class MarkdownFloorTest(unittest.TestCase):
    """markdown 3.7-3.10 on Python 3.13 ran out of memory (2 GB+, 30-45 s) on a real note with two unclosed `<!--` in
    separate paragraphs. vaultkit refuses those versions, and such a note renders within a small bound."""

    def test_versions(self):
        from vaultkit.vault import markdown_ok
        for good in ("3.11", "3.11.1", "3.12", "4.0"):
            self.assertTrue(markdown_ok(good), good)
        for bad in ("3.7", "3.8.2", "3.10.2", "", None, "x"):
            self.assertFalse(markdown_ok(bad), bad)

    def test_unclosed_comments_render_within_bounds(self):
        script = ("import resource, sys; resource.setrlimit(resource.RLIMIT_AS, (1 << 30, 1 << 30)); "
                  "sys.path.insert(0, sys.argv[1]); import vaultkit; "
                  "v = vaultkit.Vault(sys.argv[2]); v.revision = 'r'; "
                  "n = vaultkit.Note('a.md', {}, sys.stdin.read()); v.index(); print(len(v.render(n, '', mode='all')))")
        text = "".join("Para %d: a `<!--` here and <!-- there, `/home/` too.\n\n" % i for i in range(40))
        tmp = tempfile.mkdtemp()
        try:
            r = subprocess.run([sys.executable, "-c", script, ROOT, tmp], input=text, capture_output=True, text=True,
                               timeout=30)
        finally:
            shutil.rmtree(tmp)
        self.assertEqual(r.returncode, 0, r.stderr[-500:])
        self.assertGreater(int(r.stdout.split()[-1]), 1000)


class TendedNamesTest(unittest.TestCase):
    def test_non_ascii_names_keep_their_dates(self):
        """KURA-5 (sweep 2026-10): git quoted non-ASCII paths, so these notes had no date."""
        tmp = tempfile.mkdtemp()
        try:
            make_repo(tmp)
            for name in ("町家.md", "café notes.md"):
                with open(os.path.join(tmp, "personal", name), "w") as f:
                    f.write("---\ntitle: x\n---\nx\n")
            sh("git", "-c", "user.name=t", "-c", "user.email=t@t", "add", "-A", cwd=tmp)
            sh("git", "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "names", "--date",
               "2026-02-03T00:00:00", cwd=tmp)
            v = vaultkit.Vault(tmp, "personal")
            v.revision = "r"
            v.index()
            self.assertEqual(v.tended.get("町家.md"), v.tended.get("café notes.md"))
            self.assertTrue(v.tended.get("町家.md"))
        finally:
            shutil.rmtree(tmp)


class YamlBombTest(unittest.TestCase):
    """LEAD-4 (sweep 2026-10): YAML aliases in frontmatter could expand a 450-byte note into a 5 GB title."""

    BOMB = ("---\na: &a [x, x, x, x, x, x, x, x, x, x]\nb: &b [*a, *a, *a, *a, *a, *a, *a, *a, *a, *a]\n"
            "c: &c [*b, *b, *b, *b, *b, *b, *b, *b, *b, *b]\nd: &d [*c, *c, *c, *c, *c, *c, *c, *c, *c, *c]\n"
            "e: &e [*d, *d, *d, *d, *d, *d, *d, *d, *d, *d]\nf: &f [*e, *e, *e, *e, *e, *e, *e, *e, *e, *e]\n"
            "title: *f\n---\nbody\n")

    def test_aliases_are_refused(self):
        self.assertIsNone(vaultkit.note_front(self.BOMB))
        n = vaultkit.Note("bomb.md", vaultkit.note_front(self.BOMB) or {}, self.BOMB)
        self.assertEqual(n.title, "bomb")                          # read as a note without frontmatter
        with self.assertRaises(vaultkit.EditError):
            vaultkit.edit_front(self.BOMB, {"growth": "evergreen"})

    def test_anchors_alone_and_ordinary_notes_still_load(self):
        self.assertEqual(vaultkit.note_front("---\ntitle: &t A\ntags: [x]\n---\n"), {"title": "A", "tags": ["x"]})
        self.assertEqual(vaultkit.note_front("---\ntitle: A\n---\n"), {"title": "A"})

    def test_oversized_frontmatter_is_not_parsed(self):
        from vaultkit.front import MAX_FRONT
        big = "---\ntitle: A\nsummary: %s\n---\n" % ("x" * MAX_FRONT)
        self.assertIsNone(vaultkit.note_front(big))


class FastYamlTest(unittest.TestCase):
    """v0.28: frontmatter goes through LibYAML (PyYAML's C parser) when it's there, with the alias guard on the composed
    node graph; the result must be exactly what the pure-Python loader gives."""

    CASES = ["title: A", "a: &a 1\nb: 2", "tags: [x, y]", "d: 2026-10-08", "t: 2026-10-08T10:00:00", "n: 0o17",
             "n: 017", "b: yes", "b: on", "x: ~", "s: '[[Note]]'", "list:\n  - [[A]]", "k: 1e3", "k: 0x1F", "k: 1_000",
             "u: \"\\u00e9\"", "dup: 1\ndup: 2", "k: |\n  multi\n  line", "k: >\n  folded", "? complex\n: value",
             "k: [a, {b: c}]", "e: 町家", "k: 12:30", "k: 2026-1-8", "k: NaN", "k: .inf", "- a\n- b", "", "plain"]

    def corpus(self):
        out = list(self.CASES)
        for rel, fm, text in vaultkit.read_notes(os.path.join(ROOT, "sample-vault")):
            m = vaultkit.front.FRONT_RE.match(text)
            if m:
                out.append(m.group(1))
        return out

    def test_same_result_as_the_python_loader(self):
        from vaultkit import front
        import yaml
        if not front._C:
            self.skipTest("this PyYAML has no LibYAML")
        for text in self.corpus():
            try:
                want = front._load_py(text)
            except yaml.YAMLError:
                want = "error"
            try:
                got = front.load_yaml(text)
            except yaml.YAMLError:
                got = "error"
            self.assertEqual(got, want, text)

    def test_aliases_are_refused_by_the_c_loader_too(self):
        from vaultkit import front
        import yaml
        if not front._C:
            self.skipTest("this PyYAML has no LibYAML")
        with self.assertRaises(yaml.YAMLError):
            front._load_c(YamlBombTest.BOMB.split("---\n")[1])
        with self.assertRaises(yaml.YAMLError):
            front._load_c("a: &a [1]\nb: *a")
        self.assertEqual(front._load_c("title: &t A\ntags: [x]"), {"title": "A", "tags": ["x"]})

    def test_without_libyaml_the_python_loader_reads_it(self):
        from vaultkit import front
        from unittest import mock
        with mock.patch.object(front, "_C", False):
            self.assertEqual(front.load_yaml("title: A\ntags: [x]"), {"title": "A", "tags": ["x"]})
            self.assertIsNone(vaultkit.note_front(YamlBombTest.BOMB))


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
        for room in ("shiori", "konbini", "niwa", "kura", "hister", "searxng", "machiya"):   # every icon is drawn
            self.assertIn('.seal.icon[data-room="%s"] { background-image: url("data:image/' % room, css)
        self.assertLess(menu.index("Shiori"), menu.index("Konbini"))                          # front to back
        self.assertLess(menu.index("Niwa"), menu.index("Hister"))                             # neighbours last
        self.assertIn('href="https://searxng.t/" data-room="searxng"', menu)
        self.assertEqual(shell.switcher("kura", {}), "")                                       # standalone: none
        self.assertEqual(shell.rooms({}), {})

    def test_house_row_and_footer_link(self):
        """v0.18: with `machiya=<url>` in MACHIYA_ROOMS every Rooms menu ends with "Machiya · home" (v0.19.1; before Settings)
        and the footer's "Part of Machiya" links there; without it, the menu and footer are as before."""
        from vaultkit import shell
        plain = shell.rooms(self.ENV)
        links = shell.rooms(dict(self.ENV, MACHIYA_ROOMS=self.ENV["MACHIYA_ROOMS"] + ",machiya=https://machiya.t/"))
        self.assertEqual(links["machiya"], "https://machiya.t")
        self.assertEqual(shell.room_info("machiya"), ("machiya", "Machiya", "町", "home"))
        menu = shell.switcher("kura", links, settings=True)
        row = ('<hr><a href="https://machiya.t/" data-room="machiya"><span class="seal icon" data-room="machiya" '
               'title="Machiya (町)" aria-hidden="true">町</span>Machiya<small>home</small></a>')
        self.assertIn(row, menu)
        self.assertLess(menu.index("SearXNG"), menu.index(row))                                # after the engines
        self.assertLess(menu.index(row), menu.index("Settings"))                               # before Settings
        self.assertNotIn("machiya", shell.switcher("kura", plain, settings=True))              # no key: as before
        self.assertIn('<b data-room="machiya">', shell.switcher("machiya", links))             # the house itself: here
        self.assertIn("Machiya", shell.switcher("kura", {"machiya": "https://machiya.t"}))     # the only link still shows
        self.assertEqual(shell.switcher("machiya", {"machiya": "https://machiya.t"}), "")     # ... but not to itself
        self.assertTrue(shell.footer("kura", None, house=links).endswith(
            '<a href="https://machiya.t/" class="house">Part of Machiya</a></footer>'))
        self.assertTrue(shell.footer("kura", None, house=plain).endswith('<span>Part of Machiya</span></footer>'))
        self.assertTrue(shell.footer("machiya", None, house=links).endswith('<span>Part of Machiya</span></footer>'))
        self.assertIn('data-set="show_machiya"', "".join(shell.apps_section("kura", links, {})[1]))
        self.assertNotIn("show_machiya", "".join(shell.apps_section("kura", plain, {})[1]))
        self.assertNotIn("show_machiya", "".join(shell.apps_section("machiya", links, {})[1]))
        header = shell.header("machiya", [], "", links)
        self.assertIn('title="Machiya (町)"', header)
        css = open(os.path.join(ROOT, "ui", "machiya.css"), encoding="utf-8").read()
        self.assertIn('.seal.icon[data-room="machiya"] { background-image: url("data:image/svg+xml;base64,', css)

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

    def test_every_text_is_readable_on_shioris_card(self):
        """v0.27: the result card (--card: --bg 40% toward --hl in a dark variant, --hl in a light one) keeps every text a
        card draws at its minimum (fg, fg2, menu-fg, menu-muted, each accent's panel shade), in every palette and both."""
        from vaultkit import palettes as P
        bad = []
        for key in P.PALETTES:
            for mode in ("dark", "light"):
                v = P.tokens(key, mode)
                for t, need in [("fg", 4.5), ("fg2", 4.5), ("menu-fg", 4.5), ("menu-muted", 4.5)] + \
                        [(a + "-panel", P.minimum(mode, a)) for a in P.ACCENTS]:
                    got = P.contrast(v[t], v["card"])
                    if got < need:
                        bad.append("%s %s: --%s on --card %.2f:1" % (key, mode, t, got))
        self.assertEqual(bad, [])

    def test_chips_have_no_fill_of_their_own_colour(self):
        """v0.26.2 (niwa's audit): a chip's text is its colour, and every accent is only guaranteed 4.5:1 on the bare page,
        panel or tint, so a chip draws an outline and no fill (an 18% fill failed 95 of 140 theme and colour pairs)."""
        import re
        css = open(os.path.join(ROOT, "ui", "machiya.css"), encoding="utf-8").read()
        rule = re.search(r"\n\.chip \{([^}]*)\}", css).group(1)
        self.assertIn("background: transparent", rule)
        self.assertIn("border: 1px solid currentColor", rule)
        self.assertNotIn("color-mix", rule)
        # v0.26.3 (kura's audit): a link chip's hover thickens, never fills. (A filter pill's hover fills 26% since v0.27.2,
        # the owner's call: a passing state, see the style guide.)
        for sel in (r"a\.chip\.link:hover",):
            hover = re.search(r"\n" + sel + r"[^{]*\{([^}]*)\}", css).group(1)
            self.assertNotIn("color-mix", hover, sel)
            self.assertNotIn("background", hover, sel)

    def test_tab_icon_is_the_small_variant(self):
        """v0.26.1 (the style guide's icons): the browser tab gets the room's small icon, an SVG with the detail that
        doesn't read at 16 px dropped, plus a 16/32/48 .ico for browsers without SVG favicons; the home-screen icon stays
        the full drawing."""
        from vaultkit import shell
        html = shell.page(shell.Prefs(), "kura", "t", "b")
        self.assertIn('<link rel="icon" href="/static/icons/kura-small.svg" type="image/svg+xml">', html)
        self.assertIn('<link rel="alternate icon" href="/static/icons/kura.ico" sizes="16x16 32x32 48x48">', html)
        self.assertIn('<link rel="apple-touch-icon" href="/static/icons/kura-apple-180.png">', html)
        self.assertNotIn('href="/static/icons/kura.svg"', html)

    def test_prefs_meta(self):
        from vaultkit import shell
        ctx = shell.Prefs()
        plain = shell.page(ctx, "kura", "t", "b")
        self.assertNotIn("machiya-prefs", plain)                                      # no principal: no sync
        html = shell.page(ctx, "kura", "t", "b", prefs_url="/api/prefs")
        self.assertIn('<meta name="machiya-prefs" content="/api/prefs">', html)
        self.assertLess(html.index("machiya-prefs"), html.index("</head>"))
        self.assertLess(html.index("machiya-prefs"), html.index("/static/machiya.js"))
        self.assertEqual(html.replace('<meta name="machiya-prefs" content="/api/prefs">\n', ""), plain)
        self.assertIn('content="/x/api/prefs?a=1&amp;b=&quot;"', shell.prefs_meta('/x/api/prefs?a=1&b="'))
        for bad in ("https://evil.example/api/prefs", "//evil.example/p", "/\\evil", "api/prefs", "/a b", "/a\n",
                    None, 5):
            self.assertEqual(shell.prefs_meta(bad), "", msg=repr(bad))
            self.assertEqual(shell.page(ctx, "kura", "t", "b", prefs_url=bad), plain, msg=repr(bad))

    def test_settings_page(self):
        from vaultkit import shell
        ctx = shell.Prefs("day", "small")
        page = shell.settings_page([shell.appearance_section(ctx),
                                    ("Reading", [shell.toggle("Preview Pane", "previewPane", True, cookie=True)],
                                     "Preview Pane shows a note beside the list."),
                                    shell.apps_section("kura", shell.rooms(self.ENV), {"hister": False}),
                                    shell.about_section("kura", "0.3.0", "synced abc 3 min ago")], "kura")
        self.assertIn('<option value="day" selected>Light</option>', page)
        self.assertIn('<option value="small" selected>Small</option>', page)
        self.assertIn('data-set="previewPane" data-cookie checked', page)
        self.assertIn('data-set="show_hister">', page)                                         # off
        self.assertNotIn('show_kura', page)                                                     # not itself
        self.assertIn('<h2 id="about">About</h2>', page)

class ShellPagesTest(unittest.TestCase):
    """v0.13: titles, security headers, message pages, the signed-in person, the status bar, the prefs footnote."""

    def setUp(self):
        from vaultkit import shell
        self.shell = shell

    def test_titles(self):
        self.assertEqual(self.shell.title("kura", "Lantern"), "Lantern - Kura")
        self.assertEqual(self.shell.title("konbini"), "Konbini")

    def test_security_headers(self):
        h = dict(self.shell.security_headers())
        csp = h["Content-Security-Policy"]
        self.assertIn("script-src 'self';", csp)
        self.assertNotIn("unsafe-eval", csp)
        self.assertNotIn("'unsafe-inline'", csp.split("script-src")[1].split(";")[0])
        for part in ("object-src 'none'", "base-uri 'self'", "form-action 'self'", "frame-ancestors 'self'"):
            self.assertIn(part, csp)
        self.assertEqual((h["X-Content-Type-Options"], h["Referrer-Policy"]), ("nosniff", "same-origin"))

    def test_manifest_colors_follow_the_device(self):
        mc, night, day = self.shell.manifest_colors, self.shell.NIGHT, self.shell.DAY
        self.assertEqual(mc("night", {"Sec-CH-Prefers-Color-Scheme": "light"}), night)     # a choice wins
        self.assertEqual(mc("day", {}), day)
        light = mc("system", {"Sec-CH-Prefers-Color-Scheme": '"light"'})
        self.assertEqual((light["background_color"], light["theme_color"]), (day["background_color"], day["theme_color"]))
        self.assertEqual(light["user_preferences"], {"color_scheme_dark": night})
        for headers in ({}, None, {"Sec-CH-Prefers-Color-Scheme": "dark"}, {"Sec-CH-Prefers-Color-Scheme": "weird"}):
            self.assertEqual(mc("system", headers)["background_color"], night["background_color"], headers)
        self.assertIn(("Accept-CH", "Sec-CH-Prefers-Color-Scheme"), self.shell.security_headers())

    def test_messages(self):
        nf = self.shell.not_found("niwa", "/n/<x>")
        self.assertIn("<h2>Not Found</h2>", nf)
        self.assertIn("/n/&lt;x&gt;", nf)
        self.assertIn('<a class="button primary" href="/">Go to Niwa</a>', nf)
        off = self.shell.offline("konbini")
        self.assertNotIn("Tailscale", off)
        self.assertIn("Konbini can&#x27;t be reached", off)

    def test_who_is_signed_in(self):
        links = {"kura": "https://k", "niwa": "https://n"}
        head = self.shell.header("kura", [], "", links, who="me<")
        self.assertIn('href="/settings#account" title="Signed in as me&lt;"', head)
        self.assertNotIn("Signed in", self.shell.header("kura", [], "", links))
        self.assertNotIn("Signed in", self.shell.header("kura", [], "", links, settings=False, who="me"))
        page = self.shell.page(self.shell.Prefs(), "kura", "t", "", tabs=[("/", "home", "Home")], links=links, who="me")
        self.assertIn('<a href="/settings#account" class="who">', page)
        self.assertIn("<small>signed in</small>", page)

    def test_status_bar_follows_the_theme(self):
        day = self.shell.page(self.shell.Prefs("day"), "kura", "t", "")
        self.assertIn('status-bar-style" content="default"', day)
        for theme in ("night", "system"):
            self.assertIn('status-bar-style" content="black-translucent"', self.shell.page(self.shell.Prefs(theme), "kura", "t", ""))

    def test_appearance_footnote(self):
        ctx = self.shell.Prefs()
        self.assertEqual(self.shell.appearance_section(ctx)[2], "Kept in this browser only.")
        self.assertIn("your other devices", self.shell.appearance_section(ctx, synced=True)[2])


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
            self.assertNotIn("License", "".join(rows))

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



class ChangelogTest(unittest.TestCase):
    """v0.18: GET /api/changelog, an app's own CHANGELOG.md for the landing page."""

    def setUp(self):
        from vaultkit import changelog
        self.c = changelog
        self.dir = tempfile.mkdtemp(prefix="vaultkit-changelog-")
        self.path = os.path.join(self.dir, "CHANGELOG.md")
        self.addCleanup(shutil.rmtree, self.dir)

    def write(self, text):
        with open(self.path, "w", encoding="utf-8") as f:
            f.write(text)

    def test_serves_the_file_with_an_etag(self):
        self.write("# Changelog\n\n## 0.2.0\n\n- Ünïcode.\n")
        status, body, headers = self.c.handle(self.path, {})
        h = dict(headers)
        self.assertEqual((status, body.decode()), (200, "# Changelog\n\n## 0.2.0\n\n- Ünïcode.\n"))
        self.assertEqual(h["Content-Type"], "text/markdown; charset=utf-8")
        self.assertEqual(h["X-Content-Type-Options"], "nosniff")
        self.assertRegex(h["ETag"], r'^"[0-9a-f]{20}"$')
        self.assertEqual(self.c.handle(self.path, {"If-None-Match": h["ETag"]})[:2], (304, b""))
        self.assertEqual(self.c.handle(self.path, {"If-None-Match": '"x", ' + h["ETag"]})[0], 304)
        self.assertEqual(self.c.handle(self.path, {"If-None-Match": '"other"'})[0], 200)
        self.write("# Changelog\n\n## 0.3.0\n")
        self.assertEqual(self.c.handle(self.path, {"If-None-Match": h["ETag"]})[0], 200)      # changed: a new tag

    def test_bounded_at_a_whole_line(self):
        self.write("## 1.0.0\n" + "".join("- line %05d\n" % i for i in range(20000)))
        status, body, _ = self.c.handle(self.path, None)
        self.assertEqual(status, 200)
        self.assertLessEqual(len(body), self.c.LIMIT)
        self.assertTrue(body.endswith(b"\n") and body.startswith(b"## 1.0.0\n"))
        self.assertEqual(self.c.load(self.path, limit=12), b"## 1.0.0\n")

    def test_missing_is_a_404(self):
        status, body, headers = self.c.handle(os.path.join(self.dir, "nope.md"))
        self.assertEqual((status, dict(headers)["Content-Type"]), (404, "text/plain; charset=utf-8"))
        self.assertIsNone(self.c.load(self.dir))                                              # a directory


class PalettesTest(unittest.TestCase):
    """v0.15: ten themes, each dark and light; the stylesheet is generated from vaultkit/palettes.py."""

    def setUp(self):
        from vaultkit import palettes, shell
        self.p, self.shell = palettes, shell

    def test_ten_themes_twenty_variants(self):
        self.assertEqual(list(self.p.PALETTES), ["tokyo-night", "solarized", "nord", "dracula", "catppuccin",
                                                 "gruvbox", "rose-pine", "kanagawa", "everforest", "ayu"])
        for key, (name, dark, light, variants) in self.p.PALETTES.items():
            self.assertEqual(set(variants), {"dark", "light"}, key)
            for mode, raw in variants.items():
                self.assertEqual(set(raw), set(self.p.TOKENS), (key, mode))

    def test_every_variant_is_readable(self):
        for key in self.p.PALETTES:
            for mode in ("dark", "light"):
                v = self.p.variant(key, mode)
                lum = self.p.luminance
                self.assertEqual(lum(v["bg"]) > lum(v["fg"]), mode == "light", (key, mode))
                for t in self.p.TEXT:
                    self.assertGreaterEqual(self.p.contrast(v[t], v["bg"]), self.p.minimum(mode, t), (key, mode, t))

    def test_shared_components_are_readable_where_they_are_drawn(self):
        """Text in the shell's own components >= 4.5:1 on the background it sits on, in every palette and both
        variants (2026-10-06, after genkan's report): the footer's status line and links on --bg, the settings and
        sign-in footnotes on --bg, the phone tab bar's current tab label on --tab-on (its icon, not text, >= 3:1),
        and the text in a settings or sign-in group on --dark (its buttons and pickers on --hl). Each selector's
        colour is read from ui/machiya.css, so moving a rule to another token is checked as drawn."""
        import re
        css = open(os.path.join(ROOT, "ui", "machiya.css"), encoding="utf-8").read()
        css = re.sub(r"/\*.*?\*/", "", css[css.index(self.p.END):], flags=re.S)     # the hand-written rules
        rules = [([s.strip() for s in m.group(1).split(",")], m.group(2))
                 for m in re.finditer(r"([^{}]+)\{([^{}]*)\}", css)]

        def colour(*selectors, default="fg"):
            # the token of the first selector with a `color:` declaration (last rule wins, as in the cascade)
            for sel in selectors:
                found = [c.group(1) for sels, body in rules if sel in sels
                         for c in [re.search(r"(?:^|[;\s])color:\s*var\(--([a-z0-9-]+)", body)] if c]
                if found:
                    return found[-1]
            return default

        def tab_on(mode):
            pat = r"body\.theme-day \.tabbar \{[^}]*" if mode == "light" else r"\.tabbar \{ display: none;[^}]*"
            return re.search(pat + r"--tab-on: var\(--([a-z0-9-]+)\)", css).group(1)

        rooms = re.findall(r"body\.room-[a-z]+ +\{ --room: var\(--([a-z]+)\)", css)
        self.assertEqual(len(rooms), 4)
        here = colour(".tabbar > a.here > span", ".tabbar > a.here", default="room")
        failures = []
        for key in self.p.PALETTES:
            for mode in ("dark", "light"):
                v = self.p.tokens(key, mode)
                pairs = [
                    ("footer status", colour(".foot .status", ".foot"), "bg", 4.5),
                    ("footer link", colour(".foot a"), "bg", 4.5),
                    ("settings footnote", colour(".settings .footnote"), "bg", 4.5),
                    ("sign-in footnote", colour(".signin .footnote"), "bg", 4.5),
                    ("open Rooms tab", colour(".tabbar > details[open] > summary"), tab_on(mode), 4.5),
                    ("settings group text", colour(".settings .item", ".settings .group"), "dark", 4.5),
                    ("settings value", colour(".settings .item .value"), "dark", 4.5),
                    ("settings subhead", colour(".settings .item.subhead"), "dark", 4.5),
                    ("settings button", colour(".settings .item button"), "hl", 4.5),
                    ("settings picker", colour(".settings select"), "hl", 4.5),
                    ("sign-in group text", colour(".signin .item", ".signin .group"), "dark", 4.5),
                    ("sign-in label", colour(".signin .item > span"), "dark", 4.5),
                    ("code block text", colour(".nbody pre code", ".nbody pre"), "dark", 4.5),
                    ("inline code", colour("code"), "dark", 4.5),
                    ("search field text", colour("form.search.searchbar input"), "dark", 4.5),
                ]
                for room in rooms:
                    pairs.append(("current tab label (%s)" % room, room if here == "room" else here, tab_on(mode), 4.5))
                    pairs.append(("current tab icon (%s)" % room, room, tab_on(mode), 3.0))
                for what, fg, bg, need in pairs:
                    got = self.p.contrast(v[fg], v[bg])
                    if got < need:
                        failures.append("%s %s: %s, --%s on --%s, %.2f:1" % (key, mode, what, fg, bg, got))
        self.assertEqual(failures, [], "%d pairs under their minimum" % len(failures))

    PANELS = (".card", ".settings .group", ".signin .group", ".rooms .menu", ".update-toast", ".nbody pre", ".tinted")

    def test_tinted_items_keep_their_text_readable(self):
        """v0.26 (Shiori's design language, owner 2026-10-07): a tinted item is its room colour's panel shade mixed into
        --tint-base at --tint-mix. Every text a panel draws (menu-fg, menu-muted, each accent's panel shade) stays at its
        minimum on it, under each room colour, in every palette and both variants; and the tint is there to see."""
        failures = []
        for key in self.p.PALETTES:
            for mode in ("dark", "light"):
                v = self.p.tokens(key, mode)
                # v0.27: tinted on Shiori's --card, 3% (Ayu dark) to 9%; the 55% outline carries the colour where the fill is faint
                self.assertGreaterEqual(v["tint-mix"], 3, "%s %s: tint-mix %s%% is too faint to see" % (key, mode, v["tint-mix"]))
                base = v[self.p.tint_base(mode)]
                texts = [("menu-fg", 4.5), ("menu-muted", 4.5)] + [(t + "-panel", self.p.minimum(mode, t)) for t in self.p.ACCENTS]
                for tint in self.p.TINTS:
                    bg = self.p.mix(v[tint + "-panel"], base, v["tint-mix"])
                    for name, need in texts:
                        got = self.p.contrast(v[name], bg)
                        if got < need:
                            failures.append("%s %s: --%s on a %s tint, %.2f:1" % (key, mode, name, tint, got))
        self.assertEqual(failures, [], "%d pairs under their minimum" % len(failures))

    def test_a_hovered_pill_stays_readable_and_tinted(self):
        """v0.27.4 (Shiori 0.18.0, the owner): a hovered filter pill is its hover shade on a fill of that same shade at
        HOVER_MIX% over --hl, as the stylesheet mixes it. The text reads at 4.5:1 in every palette, both variants, each
        accent; and the stylesheet draws it that way (the shade swap and the mix)."""
        failures = []
        for key in self.p.PALETTES:
            for mode in ("dark", "light"):
                v = self.p.tokens(key, mode)
                for t in self.p.ACCENTS:
                    got = self.p.contrast(v[t + "-hover"], self.p.mix(v[t + "-hover"], v["hl"], self.p.HOVER_MIX))
                    if got < 4.5:
                        failures.append("%s %s %s: %.2f:1" % (key, mode, t, got))
        self.assertEqual(failures, [], "%d hovered pills under 4.5:1" % len(failures))
        css = open(os.path.join(ROOT, "ui", "machiya.css")).read()
        rule = css[css.index(".pill:hover:not([aria-current])"):]
        rule = rule[:rule.index("}")]
        self.assertIn("color-mix(in srgb, var(--pill) %d%%, var(--hl))" % self.p.HOVER_MIX, rule)
        for t in self.p.ACCENTS:
            self.assertIn("--%s: var(--%s-hover)" % (t, t), rule)
        self.assertIn("--room: var(--room-hover)", rule)

    def test_card_style_has_shioris_four_choices(self):
        """v0.27.5 (the owner: Konbini's stripes tinted, or a choice, as Shiori's Result Style): every accent tints, and
        data-card-style switches a tinted card to solid, a left bar or none; tint is the default."""
        self.assertEqual(tuple(self.p.TINTS), tuple(self.p.ACCENTS))
        css = open(os.path.join(ROOT, "ui", "machiya.css")).read()
        for style in ("solid", "bar", "none"):
            self.assertIn('[data-card-style="%s"] .card.tinted' % style, css)
        self.assertIn("inset 3px 0 0 var(--tint)", css)

    def test_accents_are_readable_on_the_raised_panels(self):
        """v0.25 (owner, 2026-10-07): accent-coloured text on a raised panel (a card, a settings or sign-in group, the
        Rooms menu, the update toast, a code block) >= its minimum on --dark and on --hl, in every palette and both
        variants. A panel swaps each accent, and the house and room colours, for its <accent>-panel shade; this reads
        what each panel declares in ui/machiya.css, so a panel that keeps the page's accents is checked as drawn
        (91 pairs were under before: 68 on --dark in the light variants, 23 on --hl in the dark ones)."""
        import re
        css = open(os.path.join(ROOT, "ui", "machiya.css"), encoding="utf-8").read()
        css = re.sub(r"/\*.*?\*/", "", css[css.index(self.p.END):], flags=re.S)
        rules = [([s.strip() for s in m.group(1).split(",")], dict(re.findall(r"--([a-z0-9-]+):\s*var\(--([a-z0-9-]+)\)", m.group(2))))
                 for m in re.finditer(r"([^{}]+)\{([^{}]*)\}", css)]

        def declared(selector):
            out = {}
            for sels, decl in rules:
                if selector in sels:
                    out.update(decl)
            return out

        rooms = {sel: declared(sel) for sel in ("body", "body.room-shiori", "body.room-konbini", "body.room-niwa", "body.room-kura")}
        for sel, decl in rooms.items():                  # each room names the panel shade of its own accents
            for name in ("room", "room2"):
                if name in decl and name + "-panel" in decl:
                    self.assertEqual(decl[name + "-panel"], decl[name] + "-panel", (sel, name))
        failures = set()
        for panel in self.PANELS:
            inside = declared(panel)
            drawn = [(inside.get(a, a), a) for a in self.p.ACCENTS]          # (the token drawn, the accent it stands for)
            drawn.append((inside.get("house", inside.get("blue", "blue")), "blue"))
            for decl in rooms.values():
                for name in ("room", "room2"):
                    accent = decl.get(name, rooms["body"][name])
                    via = inside.get(name)                                     # --room: var(--room-panel)
                    drawn.append((decl.get(via, rooms["body"].get(via)) if via else accent, accent))
            for key in self.p.PALETTES:
                for mode in ("dark", "light"):
                    v = self.p.tokens(key, mode)
                    for tok, accent in drawn:
                        for surface in ("dark", "hl"):
                            got = self.p.contrast(v[tok], v[surface])
                            if got < self.p.minimum(mode, accent):
                                failures.add("%s %s: --%s on --%s, %.2f:1" % (key, mode, tok, surface, got))
        self.assertEqual(sorted(failures), [], "%d pairs under their minimum" % len(failures))

    def test_tokyo_night_is_unchanged(self):
        night, day = self.p.variant("tokyo-night", "dark"), self.p.variant("tokyo-night", "light")
        self.assertEqual((night["bg"], night["fg"], night["comment"], night["blue"]), ("#1a1b26", "#c0caf5", "#565f89", "#7aa2f7"))
        self.assertEqual((day["bg"], day["fg"], day["comment"], day["blue"]), ("#e1e2e7", "#3760bf", "#5a6391", "#155fc5"))
        # v0.25: the panel shades leave Night's blue as it is and darken Day's only on a panel
        night, day = self.p.tokens("tokyo-night", "dark"), self.p.tokens("tokyo-night", "light")
        self.assertEqual((night["blue-panel"], day["blue-panel"]), ("#7aa2f7", "#1459b9"))

    def test_the_stylesheet_is_the_table(self):
        css = open(os.path.join(ROOT, "ui", "machiya.css"), encoding="utf-8").read()
        block = css[css.index(self.p.BEGIN) + len(self.p.BEGIN):css.index(self.p.END)]
        self.assertEqual(block, self.p.css(), "run: python3 -m vaultkit.palettes, and paste between the markers")

    def test_machiya_js_knows_the_same_themes(self):
        import re
        js = open(os.path.join(ROOT, "ui", "machiya.js"), encoding="utf-8").read()
        listed = re.search(r"const PALETTES = \[([^\]]*)\]", js).group(1)
        self.assertEqual(re.findall(r'"([a-z-]+)"', listed), list(self.p.PALETTES))

    def test_pages_and_manifest_wear_the_theme(self):
        sh = self.shell
        ctx = sh.prefs("palette=nord; theme=day")
        self.assertEqual(ctx.palette, "nord")
        page = sh.page(ctx, "kura", "t", "")
        self.assertIn('class="theme-day palette-nord room-kura"', page)
        self.assertIn('<meta name="theme-color" content="#e5e9f0">', page)
        self.assertIn('class="theme-system room-kura"', sh.page(sh.prefs(""), "kura", "t", ""))   # Tokyo Night: no class
        self.assertEqual(sh.prefs("palette=<script>").palette, "tokyo-night")                      # unknown: the default
        self.assertEqual(sh.prefs("palette=nord; machiya_palette=ayu").palette, "ayu")              # shared cookie wins
        self.assertTrue(sh.is_shared("palette"))
        m = sh.manifest_colors("system", {"Sec-CH-Prefers-Color-Scheme": "light"}, "gruvbox")
        self.assertEqual((m["background_color"], m["user_preferences"]["color_scheme_dark"]["background_color"]),
                         ("#fbf1c7", "#282828"))
        self.assertEqual(sh.manifest_colors("night", None, "dracula")["theme_color"], "#21222c")
        self.assertEqual(sh.manifest_colors("night", None, "nope")["theme_color"], "#16161e")
        section = sh.appearance_section(ctx)
        self.assertEqual(section[0], "Display")
        self.assertIn('<option value="nord" selected>Nord</option>', section[1][0])


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

    def test_search_bar_is_the_headers_second_row(self):
        bar = self.shell.search_bar("a <b>", "/v/work/search", "Search Notes", "Search every note")
        self.assertIn('class="search searchbar"', bar)
        self.assertIn('action="/v/work/search"', bar)
        self.assertIn('value="a &lt;b&gt;"', bar)
        self.assertIn('class="clear"', bar)
        h = self.shell.header("kura", [("/", "home", "Home")], "home", {}, search=bar)
        top, row = h.split('</div><div class="searchrow">', 1)       # after the top bar, inside the header
        self.assertNotIn("form", top)
        self.assertTrue(row.startswith(bar) and row.endswith("</div></header>\n"))
        self.assertNotIn("searchrow", self.shell.header("kura", [("/", "home", "Home")], "home", {}))

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


@unittest.skipUnless(shutil.which("node"), "needs node")
class MenusAndPullTest(unittest.TestCase):
    """The real ui/machiya.js in Node (tests/js/ui_sim.mjs): menus closed on the way out and on return (the owner's
    report of 2026-10-05: Rooms -> Settings -> back showed the menu still open), and pull to refresh in an installed app."""

    @classmethod
    def setUpClass(cls):
        r = subprocess.run(["node", os.path.join(ROOT, "tests", "js", "ui_sim.mjs")], capture_output=True, text=True,
                           timeout=120)
        if r.returncode:
            raise AssertionError("ui_sim.mjs failed:\n" + r.stderr[-3000:])
        out = json.loads(r.stdout)
        cls.menus, cls.pull = out["menus"], out["pull"]

    ALL_CLOSED = {"headerRooms": False, "tabRooms": False, "cardMenu": False, "disclosure": True, "sheet": False,
                  "popover": False}

    def test_a_link_in_the_menu_closes_it_before_the_page_goes(self):
        m = self.menus
        self.assertFalse(m["settingsTap"])           # the report: so the back/forward cache keeps it closed
        self.assertFalse(m["otherRoomTap"])
        self.assertFalse(m["cardMenuTap"])           # a room's own <details data-menu>
        self.assertFalse(m["sheetTap"])              # a <dialog> sheet (Konbini's card sheet)
        self.assertFalse(m["popoverTap"])
        self.assertFalse(m["signoutSubmit"])         # Sign Out, though machiya.js holds that form a moment

    def test_this_page_stays_so_does_the_menu(self):
        m = self.menus
        self.assertTrue(m["blankTap"])               # target=_blank
        self.assertTrue(m["metaTap"])                # a new tab
        self.assertTrue(m["preventedTap"])           # the room handled the tap
        self.assertTrue(m["disclosureTap"])          # an ordinary <details> in the page is content, not a menu
        self.assertTrue(m["sheetFormHandled"])       # the room's own form in its sheet (fetch)
        self.assertFalse(m["sheetFormSubmit"])

    def test_shown_again_means_closed(self):
        m = self.menus
        self.assertFalse(m["bfcache"])
        for case in ("bfcacheAll", "popstate", "pagehide"):
            self.assertEqual(m[case], self.ALL_CLOSED, case)
        self.assertTrue(m["sheetCloseEvent"])        # dialog.close(): the room hears "close"
        # a fresh load closes menus only: a dialog or popover open then is the room's doing
        self.assertEqual(m["freshLoad"], dict(self.ALL_CLOSED, sheet=True, popover=True))
        # a browser without popovers (:popover-open throws): the rest still closes
        self.assertEqual(m["noPopoverSupport"], dict(self.ALL_CLOSED, popover=True))
        self.assertEqual(m["escape"], [False, False])
        self.assertFalse(m["outside"])

    MOVED = "main footer.foot"   # the page's flow; the header, the fixed toast, the dialog, the popover, the tab bar stay

    def mark(self, **kw):
        return dict({"top": "112px", "grow": "1.00", "turn": "270deg", "opacity": "1", "ready": True, "loading": False}, **kw)

    def test_the_content_follows_the_finger(self):
        """The owner's report (2026-10-05, iPhone): the pull reloaded but nothing moved. Now the content comes down after
        the finger (the header and the tab bar stay), with the mark in the gap under the header."""
        p = self.pull
        self.assertTrue(p["htmlClass"])              # machiya.css: no rubber band in the installed app
        self.assertEqual(p["atRest"], {"y": "", "settle": False, "moved": "", "mark": None})
        self.assertEqual(p["quarter"], {"y": "18px", "settle": False, "moved": self.MOVED, "mark": self.mark(
            grow="0.63", turn="69deg", opacity="0.43", ready=False)})
        self.assertEqual(p["sixty"], {"y": "42px", "settle": False, "moved": self.MOVED, "mark": self.mark(
            grow="0.80", turn="162deg", ready=False)})
        self.assertEqual(p["held"], {"y": "95px", "settle": False, "moved": self.MOVED, "mark": self.mark()})
        # once a frame: twenty moves before a frame draw nothing, then one frame draws the last
        self.assertEqual((p["beforeFrame"], p["framesQueued"]), ("", 1))

    def test_the_travel(self):
        t = self.pull["track"]
        self.assertEqual(t, sorted(t))               # never goes back while the finger goes on down
        self.assertEqual(t[2:13], [6, 12, 18, 24, 30, 36, 42, 48, 54, 60, 66])   # 0.6 of the finger up to ready
        steps = [b - a for a, b in zip(t[13:], t[14:])]
        self.assertLess(max(steps), 6)               # past ready, stiffer
        self.assertLess(t[-1], 130)                  # and never past PULL.max (an asymptote)

    def test_pull_reloads_once(self):
        p = self.pull
        # let go past ready: at once the content eases to rest 56 px down, the mark spinning; the reload after 200 ms
        self.assertEqual(p["released"], {"y": "56px", "settle": True, "moved": self.MOVED, "mark": self.mark(loading=True)})
        self.assertEqual((p["reloadsAt0"], p["reloadsAt199"], p["reloads"]), (0, 0, 1))
        self.assertEqual(p["reloadsAfter"], 1)       # a second touchend or pull while it reloads: nothing
        self.assertEqual(p["whileReloading"], p["released"])
        # back from the cache: the page as it was, and a pull works again
        self.assertEqual(p["afterRestore"], {"y": "0px", "settle": False, "moved": "", "mark": self.mark(
            grow="0.50", turn="0deg", opacity="0", ready=False)})
        self.assertEqual(p["reloadsRestored"], 2)
        self.assertEqual(p["justPast"], 1)
        self.assertEqual((p["flatPane"], p["iosStandalone"]), (1, 1))
        self.assertTrue(p["passive"])

    def test_short_of_ready_it_springs_back(self):
        s = self.pull["short"]
        self.assertFalse(s["ready"])
        rest = self.mark(grow="0.50", turn="0deg", opacity="0", ready=False)
        self.assertEqual(s["released"], {"y": "0px", "settle": True, "moved": self.MOVED, "mark": rest})   # easing back
        self.assertEqual(s["springing"], self.MOVED)                                                    # for 200 ms
        self.assertEqual(s["settled"], {"y": "0px", "settle": False, "moved": "", "mark": rest})        # no transform left
        self.assertEqual(s["reloads"], 0)
        # another script takes the gesture mid-pull (Konbini's card drag): it springs back too
        t = self.pull["takenMidPull"]
        self.assertEqual((t["y"], t["settle"], t["reloads"], t["after"]), ("0px", True, 0, ""))

    def test_reduced_motion_snaps(self):
        self.assertEqual(self.pull["still"], {"restedAt0": True, "reloadsAt49": 0, "reloads": 1})

    def test_no_pull_where_it_would_surprise(self):
        midway = {"menuOpensMidPull", "scrollsMidPull"}       # these began as pulls, and sprang back
        for case in ("scrolled", "menuOpen", "sheetOpen", "popoverOpen", "menuOpensMidPull", "innerPane", "field",
                     "tabbar", "optOut", "selection", "twoFingers", "takenOver", "sideways", "upFirst",
                     "scrollsMidPull", "browserTab"):
            r = self.pull[case]
            self.assertEqual((r["reloads"], r["rest"]), (0, True), case)
            self.assertEqual(r["peak"] > 0, case in midway, case)

    def test_browser_tabs_keep_their_own(self):
        self.assertEqual((self.pull["browserListeners"], self.pull["browserClass"]), (0, False))


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
