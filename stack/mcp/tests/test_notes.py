"""notes_create / notes_update against real git repos (a bare origin, the writer's clone, and a second clone playing another
device or the board). The rules are configuration: LAYOUT is a vault whose folders, tags and blocks are managed by other tools,
set explicitly; Defaults and Settings test the neutral defaults and each setting. Needs vaultkit's dependencies (markdown, pyyaml)."""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from datetime import date

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import mcp                      # noqa: E402,F401  (puts vaultkit on the path)
import notes_writer             # noqa: E402
from rooms import ToolError     # noqa: E402
from textver import text_version  # noqa: E402
import test_mcp as T            # noqa: E402

GIT_ENV = dict(os.environ, GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@x", GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@x")


def git(repo, *args):
    return subprocess.run(["git", "-C", repo, *args], check=True, capture_output=True, text=True, env=GIT_ENV).stdout


def note(tags, status="active", extra="", body="# T\n\nBody.\n"):
    return "---\ntitle: T\ncreated: 2026-09-01\ntags:\n%s\nstatus: %s\nsummary: \"s\"\n%s---\n\n%s" % (
        "\n".join("  - " + t for t in tags), status, extra, body)


# The rules of a vault with managed folders, as the settings an operator would write.
LAYOUT_ENV = {
    "MCP_NOTES_SUBDIR": "personal", "MCP_NOTES_SPARSE": "personal",
    "MCP_NOTES_NEVER_WRITE": "Templates/,Archive/,Generated/,Reports/Runs/,Hubs/Catalog.md,*(conflict copy*",
    "MCP_NOTES_NEVER_CREATE": "Projects/,Catalog/,Hubs/,Reports/Incidents/,*.mirror.md",
    "MCP_NOTES_DEFAULT_FOLDER": "Inbox/",
    "MCP_NOTES_REFUSED_TAGS": "type/project,type/issue,type/hub,area/projects,effort/*",
    "MCP_NOTES_REQUIRED_TAGS": "type/,area/",
    "MCP_NOTES_CREATE_TAGS": "type/reference,type/log,type/idea",
    "MCP_NOTES_CARDS": "Projects/,type/project",
    "MCP_NOTES_STATUS_LOCKED": "Catalog/,type/issue",
    "MCP_NOTES_MANAGED_MARKERS": "list-sync,catalog-sync",
}
LAYOUT = mcp.Config(dict(LAYOUT_ENV)).notes_rules

SEED = {
    "Notes/Existing.md": note(["type/reference", "area/tools", "topic/python"], body="# Existing\n\n## Overview\n\nOne.\n\n### Deeper\n\nDeep.\n\n## Notes\n\nTwo.\n\n```\n## Not a heading\n```\n\n## Related Notes\n\n- a\n"),
    "Notes/Idea.md": note(["type/idea", "area/tools"], status="draft"),
    "Notes/Table.md": note(["type/log", "area/tools"], body="# Table\n\n| a | b |\n|---|---|\n| 1 | 2 |\n"),
    "Projects/Card.md": note(["type/project", "area/projects", "area/tools"], status="wip", extra="project: card\n"),
    "Projects/Plain.md": note(["type/reference", "area/tools"]),
    "Notes/Tagged Card.md": note(["type/project", "area/tools"], status="active"),
    "Templates/Note.md": "---\ntitle: {{title}}\ntags:\n  - type/reference\n  - area/\n  - topic/\n---\n",
    "Archive/Old.md": note(["type/reference", "area/tools"], status="archive"),
    "Catalog/x.mirror.md": note(["type/reference", "area/tools"], extra="source: x\n", body="# x\n\n<!-- catalog-sync:start -->\nauto\n<!-- catalog-sync:end -->\n\nMine.\n"),
    "Reports/Incidents/Inc.md": note(["type/issue", "area/ops"]),
    "Reports/Runs/Run.md": note(["type/log", "area/ops"]),
    "Hubs/Index.md": note(["type/hub", "area/tools"]),
    "Hubs/Catalog.md": note(["type/hub", "area/tools"]),
    "Generated/host.md": note(["type/log", "area/infra"]),
    "Garden/Pub.md": note(["type/reference", "area/reading"], extra="publish: true\n"),
    "Notes/Synced.md": note(["type/reference", "area/tools"], body="# Synced\n\n## Log\n\n<!-- list-sync:start -->\nauto\n<!-- list-sync:end -->\n\n## Mine\n\nText.\n"),
    "Inbox/.gitkeep": "",
}


def make_vault(tmp, subdir="personal"):
    origin, seed, repo, other = (os.path.join(tmp, n) for n in ("origin.git", "seed", "clone", "other"))
    os.makedirs(seed)
    subprocess.run(["git", "init", "-q", "-b", "main", seed], check=True, env=GIT_ENV)
    for rel, text in SEED.items():
        path = os.path.join(seed, subdir, rel)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            f.write(text)
    git(seed, "add", "-A")
    git(seed, "commit", "-q", "-m", "seed")
    subprocess.run(["git", "clone", "-q", "--bare", seed, origin], check=True, env=GIT_ENV)
    subprocess.run(["git", "clone", "-q", origin, repo], check=True, env=GIT_ENV)
    subprocess.run(["git", "clone", "-q", origin, other], check=True, env=GIT_ENV)
    return origin, repo, other


class RootVault(unittest.TestCase):
    """A vault at the repository root (no subfolder) is the default: notes are created, committed and pushed there."""

    def test_create_and_update_at_the_repo_root(self):
        tmp = tempfile.mkdtemp(prefix="notes-root-")
        self.addCleanup(shutil.rmtree, tmp, True)
        origin, repo, _other = make_vault(tmp, subdir="")
        w = notes_writer.NotesWriter(repo, today=lambda: date(2026, 1, 15))
        r = w.create(path="Inbox/Root Note.md", title="Root Note", body="Hello.", tags=["type/reference", "area/tools"], summary="", status="draft", agent="t")
        self.assertTrue(r["created"])
        self.assertTrue(os.path.exists(os.path.join(repo, "Inbox", "Root Note.md")))
        self.assertIn("notes: create Inbox/Root Note.md", subprocess.run(["git", "-C", origin, "log", "-1", "--format=%s"], capture_output=True, text=True, check=True).stdout)
        cur = w.read("Inbox/Root Note.md")
        out = w.update(path="Inbox/Root Note.md", expected_version=text_version(cur), mode="append", text="More.", agent="t")
        self.assertTrue(out["commit"])


class Vendored(unittest.TestCase):
    def test_the_vendored_vaultkit_matches_its_manifest(self):
        import vaultkit.verify
        here = os.path.realpath(os.path.dirname(vaultkit.verify.__file__))
        self.assertTrue(here.endswith(os.path.join("stack", "mcp", "vaultkit")), here)   # the copy beside mcp.py, not another
        self.assertEqual(vaultkit.verify.check(here), [])


class Base(unittest.TestCase):
    RULES = LAYOUT

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="notes-test-")
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.origin, self.repo, self.other = make_vault(self.tmp)
        self.w = notes_writer.NotesWriter(self.repo, subdir="personal", env=None, today=lambda: date(2026, 1, 15), rules=notes_writer.Rules(**self.RULES))

    def read(self, rel):
        return self.w.read(rel)

    def create(self, **kw):
        a = dict(path="Inbox/New Note.md", title="New Note", body="Hello.", tags=["type/reference", "area/tools"], summary="A note.",
                 status="draft", agent="t")
        a.update(kw)
        return self.w.create(**a)

    def update(self, rel, mode, **kw):
        cur = self.read(rel)
        a = dict(path=rel, expected_version=text_version(cur), mode=mode, agent="t")
        a.update(kw)
        return self.w.update(**a)

    def refused(self, fn, *needles, owner=None):
        with self.assertRaises(ToolError) as cm:
            fn()
        for n in needles:
            self.assertIn(n.lower(), cm.exception.message.lower())
        if owner is not None:
            self.assertEqual(bool(cm.exception.extra.get("needs_owner")), owner)
        return cm.exception

    def other_push(self, rel, text, msg="other edit"):
        git(self.other, "pull", "-q", "--ff-only")
        path = os.path.join(self.other, "personal", rel)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            f.write(text)
        git(self.other, "add", "-A")
        git(self.other, "commit", "-q", "-m", msg)
        git(self.other, "push", "-q", "origin", "HEAD")


class Create(Base):
    def test_writes_the_generated_frontmatter_commits_and_pushes(self):
        r = self.create()
        text = self.read("Inbox/New Note.md")
        self.assertEqual(text, "---\ntitle: New Note\ncreated: 2026-01-15\ntags:\n  - type/reference\n  - area/tools\naliases: []\n"
                               "status: draft\nsummary: A note.\n---\n\n# New Note\n\nHello.\n")
        self.assertTrue(r["created"] and r["pushed"] and "conflict" not in r)
        self.assertEqual(r["version"], text_version(text))
        self.assertIn("notes: create Inbox/New Note.md (mcp:t)", git(self.origin, "log", "-1", "--format=%s"))
        self.assertEqual(git(self.origin, "log", "-1", "--format=%an"), "machiya-mcp\n")
        self.assertEqual(git(self.origin, "show", "HEAD:personal/Inbox/New Note.md"), text)

    def test_empty_summary_and_an_existing_h1(self):
        self.create(summary="", body="# Own Title\n\nText.")
        text = self.read("Inbox/New Note.md")
        self.assertIn('summary: ""', text)
        self.assertEqual(text.count("# "), 1)

    def test_path_rules(self):
        for bad in ("../x.md", "/abs.md", "Inbox/.hidden.md", "Inbox/x.txt", "Inbox/a:b.md", "Inbox/x (conflict copy 2026).md", "a//b.md",
                    "Inbox/CLAUDE.md", "Templates/T.md", "Archive/A.md", "Generated/s.md", "Reports/Runs/r.md", "Inbox/" + "x" * 250 + ".md", "", "Root.md"):
            self.refused(lambda bad=bad: self.create(path=bad), "")
        for folder in ("Projects/X.md", "Catalog/X.md", "Hubs/X.md", "Reports/Incidents/X.md"):
            self.refused(lambda folder=folder: self.create(path=folder), "not created here")

    def test_folder_names_are_compared_case_insensitively(self):
        for bad in ("generated/x.md", "GENERATED/x.md", "projects/x.md", "templates/x.md", "archive/x.md", "reports/runs/x.md",
                    "hubs/x.md", "Notes/claude.md", "Notes/CLAUDE.MD"):
            self.refused(lambda bad=bad: self.create(path=bad), "")
        self.refused(lambda: self.w.update("generated/host.md", "0" * 12, "append", "t", text="x"), "never written")
        self.refused(lambda: self.w.update("hubs/catalog.md", "0" * 12, "append", "t", text="x"), "never written")

    def test_the_top_level_folder_must_exist_with_its_exact_name(self):
        e = self.refused(lambda: self.create(path="Inbx/Foo.md"), "not an existing folder", owner=True)
        self.refused(lambda: self.create(path="inbox/Foo.md"), "exact name")
        r = self.create(path="Notes/Deep/Er/Nested.md", title="Nested")            # new folders below an existing one are fine
        self.assertTrue(r["created"])

    def test_git_stems_and_secrets_in_title_and_summary_and_hash_tags(self):
        self.refused(lambda: self.create(path="Inbox/thing.mirror.md", title="thing.mirror"), ".mirror.md")
        self.refused(lambda: self.create(summary="token = abcdefghijklmnop1234567"), "secret")
        self.refused(lambda: self.create(title="password: abcdefghijklmnop1234567"), "secret")
        r = self.create(tags=["#type/reference", "#area/tools"])
        self.assertIn("tags:\n  - type/reference\n  - area/tools\n", self.read("Inbox/New Note.md"))
        self.update("Notes/Existing.md", "frontmatter", tags_add=["#topic/python", "#type/log"])
        self.assertIn("  - type/log\n", self.read("Notes/Existing.md"))
        self.refused(lambda: self.update("Notes/Existing.md", "frontmatter", summary="key AKIAABCDEFGHIJKLMNOP"), "secret")

    def test_tag_rules(self):
        e = self.refused(lambda: self.create(tags=["type/reference", "area/tools", "topic/brand-new"]), "unknown tag")
        self.assertTrue(e.extra["needs_owner"])
        for tags in (["status/active", "type/reference", "area/tools"], ["type/project", "area/tools"], ["type/issue", "area/tools"],
                     ["type/hub", "area/tools"], ["type/reference", "area/projects"], ["type/reference", "area/tools", "effort/s"],
                     ["type/reference"], ["area/tools"], ["nonsense", "area/tools"], []):
            self.refused(lambda tags=tags: self.create(tags=tags), "")
        self.assertFalse(os.path.exists(self.w.path_of("Inbox/New Note.md")))
        self.create(tags=["type/log", "area/tools", "topic/python"], path="Inbox/Logged.md", title="Logged")

    def test_status_and_one_line_fields(self):
        self.refused(lambda: self.create(status="archive"), "draft or active")
        self.refused(lambda: self.create(status="wip"), "draft or active")
        self.refused(lambda: self.create(summary="two\nlines"), "one line")
        self.refused(lambda: self.create(title="a\nb"), "one line")
        self.refused(lambda: self.create(title=""), "title")
        self.create(status="active")

    def test_body_rules(self):
        for bad, why in (("x\n<!-- list-sync:start -->\n", "list-sync"), ("<!--catalog-sync:end-->", "catalog-sync"),
                         ("Title\n=======\n", "conflict marker"), ("<<<<<<< HEAD\n", "conflict marker"), ("\x00", "NUL"),
                         ("token = abcdefghijklmnop1234567", "secret"), ("-----BEGIN RSA PRIVATE KEY-----", "secret"),
                         ("---\ntitle: x\n---\nbody", "frontmatter"), ("x" * 100_001, "at most")):
            self.refused(lambda bad=bad: self.create(body=bad), why)
        self.assertFalse(self.w.sync.dirty())

    def test_name_must_be_new_across_the_whole_vault_and_case_insensitive(self):
        self.refused(lambda: self.create(path="Inbox/Existing.md", title="Existing"), "already exists")
        self.refused(lambda: self.create(path="Inbox/existing.md", title="existing"), "already exists")
        self.create()
        self.refused(lambda: self.create(path="Notes/new note.md"), "already exists")

    def test_dates_use_the_injected_local_day(self):
        w = notes_writer.NotesWriter(self.repo, subdir="personal", today=lambda: date(2027, 1, 2))
        w.create("Inbox/Later.md", "Later", "x", ["type/idea", "area/tools"], "", "draft", "t")
        self.assertIn("created: 2027-01-02", self.read("Inbox/Later.md"))

    def test_a_concurrent_push_to_another_file_is_merged(self):
        self.other_push("Notes/Other.md", note(["type/reference", "area/tools"]))
        r = self.create()
        self.assertTrue(r["pushed"])
        self.assertEqual(git(self.origin, "show", "HEAD:personal/Notes/Other.md"), note(["type/reference", "area/tools"]))
        self.assertIn("Inbox/New Note.md", git(self.origin, "ls-tree", "-r", "--name-only", "HEAD"))


class Update(Base):
    EX = "Notes/Existing.md"

    def test_version_is_required_and_a_stale_one_is_a_conflict(self):
        e = self.refused(lambda: self.w.update(self.EX, "0" * 12, "append", "t", text="x"), "changed since you read")
        self.assertEqual(e.extra["code"], "version_conflict")
        self.assertEqual(e.extra["current_version"], text_version(self.read(self.EX)))
        self.other_push(self.EX, self.read(self.EX) + "\nremote edit\n")
        v = text_version(SEED[self.EX])
        self.refused(lambda: self.w.update(self.EX, v, "append", "t", text="x"), "changed since")

    def test_append_paragraph_and_line(self):
        self.update(self.EX, "append", text="Added.")
        self.assertTrue(self.read(self.EX).endswith("- a\n\nAdded.\n"))
        self.update("Notes/Table.md", "append", text="| 3 | 4 |", join="line")
        self.assertTrue(self.read("Notes/Table.md").endswith("| 1 | 2 |\n| 3 | 4 |\n"))
        self.assertIn("notes: update Notes/Table.md (mcp:t)", git(self.origin, "log", "-1", "--format=%s"))

    def test_replace_section_respects_levels_and_code_fences(self):
        self.update(self.EX, "replace_section", heading="Overview", text="New overview.")
        text = self.read(self.EX)
        self.assertIn("## Overview\n\nNew overview.\n\n## Notes", text)
        self.assertNotIn("### Deeper", text)                     # the deeper heading belonged to the section
        self.assertIn("```\n## Not a heading\n```", text)
        self.update(self.EX, "replace_section", heading="## notes", text="Changed.")
        self.assertIn("## Notes\n\nChanged.\n\n## Related Notes", self.read(self.EX))
        self.refused(lambda: self.update(self.EX, "replace_section", heading="Not a heading", text="x"), "no heading")
        self.update(self.EX, "replace_section", heading="Fresh", text="Made.", create_if_missing=True)
        self.assertTrue(self.read(self.EX).endswith("## Fresh\n\nMade.\n"))
        self.refused(lambda: self.update(self.EX, "replace_section", text="x"), "heading")

    def test_replace_body_needs_confirm_and_keeps_the_frontmatter(self):
        self.refused(lambda: self.update(self.EX, "replace_body", text="# New\n"), "confirm", owner=True)
        self.update(self.EX, "replace_body", text="# New\n\nAll new.", confirm=True)
        text = self.read(self.EX)
        self.assertTrue(text.startswith("---\ntitle: T\n") and text.endswith("---\n\n# New\n\nAll new.\n"))

    def test_sync_blocks_are_protected(self):
        self.refused(lambda: self.update("Notes/Synced.md", "replace_body", text="x", confirm=True), "list-sync")
        self.refused(lambda: self.update("Notes/Synced.md", "replace_section", heading="Log", text="x"), "list-sync")
        self.refused(lambda: self.update("Notes/Synced.md", "append", text="<!-- list-sync:end -->"), "list-sync")
        self.update("Notes/Synced.md", "replace_section", heading="Mine", text="Mine now.")
        self.assertIn("auto", self.read("Notes/Synced.md"))
        self.update("Catalog/x.mirror.md", "append", text="More.")
        self.refused(lambda: self.update("Catalog/x.mirror.md", "append", text="<!-- catalog-sync:start -->"), "sync")

    def test_frontmatter_tags_summary_status_by_line_edits(self):
        before = self.read(self.EX)
        self.update(self.EX, "frontmatter", tags_add=["topic/python", "type/log"], summary="New summary", status="draft")
        after = self.read(self.EX)
        self.assertIn("tags:\n  - type/reference\n  - area/tools\n  - topic/python\n  - type/log\n", after)
        self.assertIn("summary: New summary\n", after)
        self.assertIn("status: draft\n", after)
        self.assertEqual(after.split("---\n", 2)[2], before.split("---\n", 2)[2])        # the body untouched
        self.assertTrue(after.startswith("---\ntitle: T\ncreated: 2026-09-01\ntags:"))   # key order kept
        self.update(self.EX, "frontmatter", tags_remove=["type/log"])
        self.assertNotIn("type/log", self.read(self.EX))
        self.refused(lambda: self.update(self.EX, "frontmatter", tags_add=["topic/never-seen"]), "unknown tag", owner=True)
        self.refused(lambda: self.update(self.EX, "frontmatter", tags_remove=["type/reference"]), "type/")
        self.refused(lambda: self.update(self.EX, "frontmatter"), "nothing to change")
        self.refused(lambda: self.update(self.EX, "frontmatter", status="wip"), "draft, active or archive")
        self.refused(lambda: self.update(self.EX, "frontmatter", summary="a\nb"), "one line")

    def test_cards_catalog_notes_issues_and_published_notes(self):
        for rel in ("Projects/Card.md", "Notes/Tagged Card.md", "Projects/Plain.md"):
            self.refused(lambda rel=rel: self.update(rel, "frontmatter", status="done"), "board card")
        self.refused(lambda: self.update("Projects/Card.md", "frontmatter", tags_add=["topic/python"]), "board_tag")
        self.update("Projects/Card.md", "frontmatter", summary="Card summary is fine")
        self.update("Projects/Card.md", "append", text="Body text is fine.")
        self.refused(lambda: self.update("Catalog/x.mirror.md", "frontmatter", status="draft"), "managed by another tool")
        self.refused(lambda: self.update("Reports/Incidents/Inc.md", "frontmatter", status="archive"), "managed by another tool")
        self.update("Catalog/x.mirror.md", "frontmatter", summary="Summary and tags stay editable")
        self.update("Reports/Incidents/Inc.md", "append", text="Timeline line.")
        e = self.refused(lambda: self.update("Garden/Pub.md", "append", text="x"), "published", owner=True)
        r = self.update("Garden/Pub.md", "append", text="x", confirm=True)
        self.assertTrue(r["published_note"])
        self.assertIn("publish: true", self.read("Garden/Pub.md"))

    def test_paths_that_are_never_written_or_dont_exist(self):
        for rel in ("Templates/Note.md", "Archive/Old.md", "Generated/host.md", "Reports/Runs/Run.md", "Hubs/Catalog.md", "../x.md"):
            self.refused(lambda rel=rel: self.w.update(rel, "0" * 12, "append", "t", text="x"), "")
        self.refused(lambda: self.w.update("Notes/Nope.md", "0" * 12, "append", "t", text="x"), "does not exist")
        self.update("Hubs/Index.md", "append", text="- [[Existing]]", join="line")          # a folder that is only never-created stays updatable

    def test_no_change_and_line_endings(self):
        body = self.read(self.EX).split("---\n", 2)[2].strip("\n")
        r = self.update(self.EX, "replace_body", text=body, confirm=True)
        self.assertFalse(r["changed"])
        self.assertEqual(len(git(self.origin, "log", "--oneline").splitlines()), 1)
        path = self.w.path_of("Notes/Table.md")
        with open(path, "w", newline="") as f:
            f.write(SEED["Notes/Table.md"].replace("\n", "\r\n"))
        git(self.repo, "add", "-A")
        git(self.repo, "commit", "-q", "-m", "crlf")
        self.update("Notes/Table.md", "append", text="| 5 | 6 |", join="line")
        self.assertNotIn("\r", self.read("Notes/Table.md"))

    def test_a_conflicting_concurrent_edit_is_reported_not_silently_lost(self):
        v = text_version(self.read(self.EX))
        self.other_push(self.EX, self.read(self.EX).replace("Two.", "Two, by remote."))
        self.w.ready = lambda: None            # the race: another edit lands after our pull, before our push
        r = self.w.update(self.EX, v, "replace_section", "t", heading="Notes", text="Mine.")
        self.assertIn("conflict", r)
        self.assertNotIn("<<<<", self.read(self.EX))
        self.assertIn("by remote", self.read(self.EX))
        self.assertEqual(git(self.origin, "show", "HEAD:personal/" + self.EX), self.read(self.EX))


class Defaults(Base):
    """No settings: only templates and archives are protected, and nothing else about the vault's layout is assumed."""
    RULES = {}

    def test_the_defaults_protect_templates_and_archives_only(self):
        r = notes_writer.Rules()
        self.assertEqual([e for e, _ in r.never_write], ["Templates/", "Archive/"])
        self.assertEqual(r.never_create, [])
        self.assertEqual((r.refused_tags, r.required_tags, r.create_tags, r.default_folder), ((), (), (), "Inbox"))
        self.assertEqual(mcp.Config({}).notes_rules, {
            "never_write": ["Templates/", "Archive/"], "never_create": [], "default_folder": "Inbox", "refused_tags": [], "required_tags": [],
            "create_tags": [], "card_markers": [], "status_locked": [], "managed_markers": []})
        for rel in ("Templates/T.md", "Archive/A.md", "archive/a.md"):
            self.refused(lambda rel=rel: self.create(path=rel), "never written")

    def test_other_folders_are_open_to_new_notes_when_they_exist(self):
        for i, rel in enumerate(("Generated/New.md", "Hubs/New.md", "Catalog/New.md", "Reports/Incidents/New.md", "Projects/New.md", "Inbox/thing.mirror.md")):
            self.assertTrue(self.create(path=rel.replace("New", "New%d" % i).replace("thing", "thing%d" % i), title="Note %d" % i)["created"], rel)
        self.refused(lambda: self.create(path="Nowhere/X.md"), "not an existing folder", owner=True)

    def test_no_tag_rules_by_default_but_tags_must_still_exist_and_have_the_kind_name_form(self):
        self.create(tags=["type/reference"])
        self.assertIn("  - type/reference\n", self.read("Inbox/New Note.md"))
        self.create(path="Inbox/Two.md", title="Two", tags=["area/projects"])
        self.create(path="Inbox/Three.md", title="Three", tags=["type/idea"])            # any existing tag, any combination
        self.refused(lambda: self.create(path="Inbox/Four.md", title="Four", tags=["topic/never-seen"]), "unknown tag", owner=True)
        self.refused(lambda: self.create(path="Inbox/Four.md", title="Four", tags=["status/active"]), "status/")
        self.refused(lambda: self.create(path="Inbox/Four.md", title="Four", tags=["nonsense"]), "type/name")

    def test_a_note_may_have_no_tags_at_all(self):
        self.create(tags=[])
        self.assertIn("tags: []\n", self.read("Inbox/New Note.md"))
        self.assertIsNotNone(notes_writer.note_front(self.read("Inbox/New Note.md")))

    def test_no_managed_blocks_by_default(self):
        self.update("Notes/Synced.md", "replace_section", heading="Log", text="Mine now.")
        self.create(path="Inbox/Marker.md", title="Marker", body="<!-- list-sync:start -->\nx\n<!-- list-sync:end -->")

    def test_nothing_is_a_card_or_locked_by_default(self):
        self.update("Projects/Plain.md", "frontmatter", status="draft")
        self.update("Notes/Tagged Card.md", "frontmatter", tags_add=["topic/python"])
        self.update("Reports/Incidents/Inc.md", "frontmatter", status="archive")
        self.refused(lambda: self.update("Projects/Card.md", "frontmatter", status="done"), "board card")     # `project:` and a board status still mark one

    def test_the_default_folder_is_inbox_and_the_error_says_so(self):
        self.refused(lambda: self.create(path="Root.md"), "default Inbox/")
        self.assertEqual(notes_writer.Rules(default_folder="/Notes/").default_folder, "Notes")


class Settings(Base):
    """Each setting on its own, against the default seed."""
    RULES = {}

    def rules(self, **kw):
        self.w = notes_writer.NotesWriter(self.repo, subdir="personal", today=lambda: date(2026, 1, 15), rules=notes_writer.Rules(**kw))

    def test_never_write_prefixes_files_and_globs(self):
        self.rules(never_write=["Generated/", "Hubs/Catalog.md", "*.mirror.md", "*(conflict copy*"])
        for rel in ("Generated/x.md", "generated/x.md", "Hubs/Catalog.md", "Inbox/y.mirror.md", "Inbox/a (conflict copy 1).md"):
            self.refused(lambda rel=rel: self.create(path=rel), "never written")
        self.refused(lambda: self.update("Catalog/x.mirror.md", "append", text="x"), "never written")
        self.refused(lambda: self.update("Generated/host.md", "append", text="x"), "never written")
        self.create(path="Hubs/Other.md", title="Other")
        self.create(path="Templates/T2.md", title="T2")          # setting it replaces the default list: nothing else is implied

    def test_never_create_allows_updates_and_says_which_entry(self):
        self.rules(never_create=["Hubs/", "Projects/", "*.mirror.md"])
        e = self.refused(lambda: self.create(path="Hubs/New.md"), "not created here", "Hubs/")
        self.refused(lambda: self.create(path="Inbox/z.mirror.md"), "*.mirror.md")
        self.refused(lambda: self.create(path="Projects/New.md"), "not created here")
        self.update("Hubs/Index.md", "append", text="- more", join="line")

    def test_card_folders_get_the_board_message(self):
        self.rules(never_create=["Projects/"], card_markers=["Projects/"])
        self.refused(lambda: self.create(path="Projects/New.md"), "board_add_backlog")

    def test_refused_tags_exact_and_prefix(self):
        self.rules(refused_tags=["type/project", "effort/*"])
        self.refused(lambda: self.create(tags=["type/project"]), "type/project")
        self.refused(lambda: self.update("Notes/Existing.md", "frontmatter", tags_add=["type/project"]), "type/project")
        self.create(tags=["area/projects"])
        e = self.refused(lambda: self.create(path="Inbox/E.md", title="E", tags=["effort/s"]), "effort/s")
        self.assertFalse(e.extra.get("needs_owner"))

    def test_required_tags_on_create_and_when_removing(self):
        self.rules(required_tags=["type/", "area/"])
        self.refused(lambda: self.create(tags=["type/reference"]), "area/")
        self.refused(lambda: self.create(tags=["area/tools"]), "type/")
        self.refused(lambda: self.create(tags=[]), "type/")
        self.refused(lambda: self.update("Notes/Existing.md", "frontmatter", tags_remove=["area/tools"]), "keeps", "area/")
        self.create()
        self.rules(required_tags=["area/"])
        self.create(path="Inbox/Only Area.md", title="Only Area", tags=["area/tools"])

    def test_create_tags_limit_only_the_named_namespaces(self):
        self.rules(create_tags=["type/reference", "type/log"])
        self.refused(lambda: self.create(tags=["type/idea"]), "type/reference")
        self.create(tags=["type/log", "area/tools", "topic/python"])
        self.update("Notes/Idea.md", "frontmatter", tags_add=["type/log"])          # the limit is for new notes
        self.rules(create_tags=["type/idea"])
        self.refused(lambda: self.create(path="Inbox/Other.md", title="Other", tags=["type/log"]), "type/idea")
        self.create(path="Inbox/Other.md", title="Other", tags=["type/idea", "area/tools"])

    def test_cards_status_locked_and_managed_markers(self):
        self.rules(card_markers=["Projects/", "type/project"], status_locked=["Catalog/", "type/issue"], managed_markers=["list-sync"])
        for rel in ("Projects/Plain.md", "Notes/Tagged Card.md"):
            self.refused(lambda rel=rel: self.update(rel, "frontmatter", status="done"), "board card")
        self.refused(lambda: self.update("Notes/Tagged Card.md", "frontmatter", tags_add=["topic/python"]), "board_tag")
        self.update("Projects/Plain.md", "frontmatter", tags_add=["topic/python"])            # a folder alone locks the status, not the tags
        self.refused(lambda: self.update("Catalog/x.mirror.md", "frontmatter", status="draft"), "Catalog/")
        self.refused(lambda: self.update("Reports/Incidents/Inc.md", "frontmatter", status="archive"), "type/issue")
        self.refused(lambda: self.update("Notes/Synced.md", "replace_body", text="x", confirm=True), "list-sync")
        self.update("Catalog/x.mirror.md", "append", text="catalog-sync markers are not managed here: <!-- catalog-sync:start -->")

    def test_a_tag_set_but_nothing_else_leaves_the_rest_open(self):
        self.rules(managed_markers=["a-sync", "b.sync"])
        self.refused(lambda: self.create(body="<!--b.sync:end-->"), "b.sync")
        self.create(path="Inbox/Dot.md", title="Dot", body="<!--bXsync:end-->")           # names are literal, not patterns

    def test_the_rules_describe_themselves_for_the_tool_description(self):
        text = notes_writer.Rules(required_tags=["type/", "area/"], create_tags=["type/log"], default_folder="Notes").describe()
        self.assertIn("type/, area/", text)
        self.assertIn("type/log", text)
        self.assertIn("default folder is Notes/", text)


class NoNewFrontmatter(Base):
    """Sweep MACH-M-2: an edit may change summary, status and tags in the frontmatter and nothing else, and never adds a
    frontmatter block to a note that has none (it could say publish: true, and Niwa would publish the note)."""

    def setUp(self):
        super().setUp()
        self.other_push("Inbox/Bare.md", "Just text.\n")
        self.other_push("Inbox/Empty.md", "")
        self.w.ready()

    def test_replace_body_or_append_cannot_add_a_frontmatter_block(self):
        block = "---\ntitle: x\npublish: true\n---\n\nNow public."
        self.refused(lambda: self.update("Inbox/Bare.md", "replace_body", text=block, confirm=True), "frontmatter")
        self.refused(lambda: self.update("Inbox/Empty.md", "append", text=block), "frontmatter")
        self.assertEqual(self.read("Inbox/Bare.md"), "Just text.\n")
        self.assertEqual(self.read("Inbox/Empty.md"), "")
        self.update("Inbox/Bare.md", "append", text="More text.")                 # an ordinary edit still works
        self.assertEqual(self.read("Inbox/Bare.md"), "Just text.\n\nMore text.\n")

    def test_only_summary_status_and_tags_may_change(self):
        cur = note(["type/reference", "area/tools"])
        notes_writer.front_change(cur, cur.replace('summary: "s"', 'summary: "t"').replace("status: active", "status: draft"))
        for new in (cur.replace("title: T", "title: U"), cur.replace("status: active", "status: active\npublish: true"),
                    "Body only.\n"):
            with self.assertRaises(ToolError):
                notes_writer.front_change(cur, new)
        with self.assertRaises(ToolError):
            notes_writer.front_change("Body.\n", "---\npublish: true\n---\nBody.\n")
        notes_writer.front_change("Body.\n", "Body.\n\nMore.\n")


class Symlinks(Base):
    """Sweep MACH-M-3: a symlinked note writes through to its target (a protected template, say), and a failed commit
    left its edit in the clone for the next write to push as "notes: 1 change (sync)"."""

    def push_link(self, rel, target):
        git(self.other, "pull", "-q", "--ff-only")
        path = os.path.join(self.other, "personal", rel)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        os.symlink(target, path)
        git(self.other, "add", "-A")
        git(self.other, "commit", "-q", "-m", "a link")
        git(self.other, "push", "-q", "origin", "HEAD")

    def test_a_symlinked_note_is_refused(self):
        template = self.read("Templates/Note.md")
        self.push_link("Inbox/Link.md", "../Templates/Note.md")
        self.push_link("Inbox/Alias.md", "../Notes/Idea.md")
        self.w.ready()
        for rel in ("Inbox/Link.md", "Inbox/Alias.md"):
            # vaultkit 0.22 checks a committed link out as a plain file (core.symlinks=false, KURA-2): the write then
            # lands in that file, never through it; with a real symlink in the clone it is refused
            try:
                self.update(rel, "append", text="Through the link.")
                self.assertFalse(os.path.islink(os.path.join(self.repo, "personal", rel)))
            except ToolError as e:
                self.assertIn("symlink", e.message.lower())
        self.assertEqual(self.read("Templates/Note.md"), template)
        self.assertNotIn("Through the link", self.read("Notes/Idea.md"))

    def origin_files(self):
        return git(self.origin, "show", "--name-only", "--format=", "main").split()

    EX = "Notes/Existing.md"

    def test_a_failed_commit_leaves_nothing_behind(self):
        before = self.read(self.EX)
        hook = os.path.join(self.repo, ".git", "hooks", "pre-commit")
        with open(hook, "w") as f:
            f.write("#!/bin/sh\nexit 1\n")
        os.chmod(hook, 0o755)
        self.refused(lambda: self.update(self.EX, "append", text="Lost edit."), "could not be committed")
        self.assertEqual(self.read(self.EX), before)
        os.unlink(hook)
        self.update("Notes/Idea.md", "append", text="Another note.")
        self.assertEqual(self.origin_files(), ["personal/Notes/Idea.md"])
        self.assertEqual(self.read(self.EX), before)

    def test_stray_changes_in_the_clone_are_never_pushed(self):
        with open(os.path.join(self.repo, "personal", "Templates", "Note.md"), "a") as f:
            f.write("stray\n")
        with open(os.path.join(self.repo, "personal", "Inbox", "Stray.md"), "w") as f:
            f.write("stray\n")
        self.update("Notes/Idea.md", "append", text="Another note.")
        self.assertEqual(self.origin_files(), ["personal/Notes/Idea.md"])
        self.assertNotIn("stray", self.read("Templates/Note.md"))
        self.assertIsNone(self.read("Inbox/Stray.md"))


class ThroughTheServer(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="notes-test-")
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.origin, self.repo, self.other = make_vault(self.tmp)
        self.fakes = T.rooms()
        self.addCleanup(lambda: [f.close() for f in self.fakes.values()])
        self.server = T.make_server(self.fakes, MCP_NOTES_DIR=self.repo, **LAYOUT_ENV)
        self.addCleanup(os.unlink, self.server.log_path)

    def test_tools_exist_only_with_a_write_clone(self):
        self.assertIn("notes_create", self.server.tools)
        self.assertIn("notes_update", self.server.tools)
        plain = T.make_server(self.fakes)
        self.addCleanup(os.unlink, plain.log_path)
        self.assertNotIn("notes_create", plain.tools)
        self.assertFalse(self.server.tools["notes_create"]["annotations"]["readOnlyHint"])
        self.assertTrue(self.server.tools["notes_update"]["annotations"]["destructiveHint"])

    def test_create_read_version_update_roundtrip(self):
        r = T.call(self.server, "notes_create", {"title": "My: New Idea!", "body": "Text.", "tags": ["type/idea", "area/tools"], "summary": "An idea."}, agent="tester@box")
        d = T.data(r)
        self.assertFalse(r.get("isError"), d)
        self.assertEqual(d["path"], "Inbox/My New Idea!.md")
        self.assertTrue(d["url"].endswith("/n/Inbox/My%20New%20Idea!"))
        self.assertIn("(mcp:tester@box)", git(self.origin, "log", "-1", "--format=%s"))
        text = open(os.path.join(self.repo, "personal", d["path"])).read()
        # notes_read returns the same version for the same text that Kura serves
        self.fakes["kura"].routes["/api/note"] = lambda q: dict(T.NOTE, markdown=text, backlinks=[], outlinks=[])
        v = T.data(T.call(self.server, "notes_read", {"path": "Projects/Alpha.md"}))["version"]
        self.assertEqual(v, text_version(text))
        r = T.call(self.server, "notes_update", {"path": d["path"], "expected_version": v, "mode": "append", "text": "More."})
        self.assertFalse(r.get("isError"), T.data(r))
        self.assertTrue(open(os.path.join(self.repo, "personal", d["path"])).read().endswith("Text.\n\nMore.\n"))
        r = T.call(self.server, "notes_update", {"path": d["path"], "expected_version": v, "mode": "append", "text": "Stale."})
        self.assertEqual(T.body(r)["code"], "version_conflict")

    def test_refusals_carry_the_owner_question_flag_and_cost_no_day_allowance(self):
        server = T.make_server(self.fakes, MCP_NOTES_DIR=self.repo, MCP_NOTES_PER_DAY="2", **LAYOUT_ENV)
        self.addCleanup(os.unlink, server.log_path)
        for _ in range(4):
            r = T.call(server, "notes_create", {"title": "X", "body": "b", "tags": ["type/idea", "area/tools", "topic/new-one"]})
            self.assertTrue(T.body(r)["needs_owner"])
        ok = [T.call(server, "notes_create", {"title": "Ok %d" % i, "body": "b", "tags": ["type/idea", "area/tools"]}) for i in range(3)]
        self.assertEqual([bool(r.get("isError")) for r in ok], [False, False, True])
        self.assertEqual(T.body(ok[2])["code"], "rate_limited")

    def test_audit_has_the_path_and_never_the_text(self):
        T.call(self.server, "notes_create", {"title": "Audit Me", "body": "very private body text", "tags": ["type/idea", "area/tools"], "summary": "private summary"})
        with open(self.server.log_path) as f:
            raw = f.read()
        row = json.loads(raw.splitlines()[-1])
        self.assertEqual(row["path"], "Inbox/Audit Me.md")          # what changed, and in which commit
        self.assertEqual(len(row["commit"]), 12)
        self.assertNotIn("private", raw)

    def test_the_default_folder_comes_from_the_setting(self):
        r = T.call(self.server, "notes_create", {"title": "Where", "body": "b", "tags": ["type/idea", "area/tools"]})
        self.assertEqual(T.data(r)["path"], "Inbox/Where.md")
        server = T.make_server(self.fakes, MCP_NOTES_DIR=self.repo, **dict(LAYOUT_ENV, MCP_NOTES_DEFAULT_FOLDER="/Notes/"))
        self.addCleanup(os.unlink, server.log_path)
        r = T.call(server, "notes_create", {"title": "Else", "body": "b", "tags": ["type/idea", "area/tools"]})
        self.assertEqual(T.data(r)["path"], "Notes/Else.md")
        r = T.call(server, "notes_create", {"title": "Given", "folder": "Reference", "body": "b", "tags": ["type/idea", "area/tools"]})
        self.assertTrue(T.body(r)["error"].startswith("'Reference' is not an existing folder"))      # an explicit folder wins and must exist
        plain = T.make_server(self.fakes, MCP_NOTES_DIR=self.repo)
        self.addCleanup(os.unlink, plain.log_path)
        self.assertEqual(plain.notes.rules.default_folder, "Inbox")

    def test_the_tool_description_carries_the_vaults_tag_rules(self):
        def desc(server):
            tools = server.handle({"jsonrpc": "2.0", "id": 1, "method": "tools/list"}, "owner", "t")["result"]["tools"]
            return next(t["description"] for t in tools if t["name"] == "notes_create")
        d = desc(self.server)
        self.assertIn("type/, area/", d)
        self.assertIn("type/reference, type/log, type/idea", d)
        self.assertIn("default folder is Inbox/", d)
        plain = T.make_server(self.fakes, MCP_NOTES_DIR=self.repo)
        self.addCleanup(os.unlink, plain.log_path)
        self.assertNotIn("type/", desc(plain))
        self.assertEqual(desc(plain).count("rules:"), 1)

    def test_the_notes_day_follows_the_time_zone_setting(self):
        from datetime import datetime, timedelta, timezone
        from zoneinfo import ZoneInfo
        for zone in ("Pacific/Kiritimati", "Pacific/Pago_Pago"):
            server = T.make_server(self.fakes, MCP_NOTES_DIR=self.repo, MCP_TZ=zone)
            self.addCleanup(os.unlink, server.log_path)
            before = datetime.now(ZoneInfo(zone)).date()
            self.assertIn(server.notes.today(), (before, before + timedelta(days=1)), zone)
        utc = datetime.now(timezone.utc).date()
        self.assertIn(self.server.notes.today(), (utc, utc + timedelta(days=1)))             # no setting: UTC's day
        self.assertEqual(self.server.config.notes_tz, "UTC")

    def test_rules_from_the_environment_reach_the_writer(self):
        r = self.server.notes.rules
        self.assertEqual([e for e, _ in r.never_write][:2], ["Templates/", "Archive/"])
        self.assertIn("Hubs/", [e for e, _ in r.never_create])
        self.assertEqual(r.card_folders, ("Projects/",))
        self.assertEqual(r.refused_tags, ("type/project", "type/issue", "type/hub", "area/projects", "effort/*"))
        r = T.call(self.server, "notes_create", {"title": "No", "path": "Hubs/No.md", "body": "b", "tags": ["type/idea", "area/tools"]})
        self.assertIn("not created here", T.body(r)["error"])

    def test_work_vaults_cannot_be_reached_by_path_tricks(self):
        for p in ("../work/Secret.md", "Inbox/../../work/Secret.md", "/etc/passwd.md"):
            r = T.call(self.server, "notes_create", {"title": "X", "path": p, "body": "b", "tags": ["type/idea", "area/tools"]})
            self.assertTrue(r["isError"], p)
        self.assertEqual(git(self.repo, "status", "--porcelain"), "")


if __name__ == "__main__":
    unittest.main()
