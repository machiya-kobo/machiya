"""Vault: the index of one vault folder (notes, wikilink names, images, links, backlinks, last-changed dates)
and the Markdown renderer.

Rendering: python-markdown (tables, fenced code, Obsidian-style soft line breaks) after rewriting ![[embeds]]
(images from the vault), Markdown images, [[wikilinks]] and callouts. How a wikilink renders depends on `mode`:
  - "garden": a published target links to <base>/n/<slug>, anything else is plain text (class "seed")
  - "all" (alias "kura"): every note links to /n/<slug> except Templates/

Wikilink resolution (don't change it: links like [[Repos/widget.git|widget]] depend on it): the lowercased target,
without .md, is looked up among lowercased basenames and lowercased full slugs; failing that, its last path
segment is. The first note seen (os.walk order) wins a name.

The index is rebuilt when key() changes. Subclass and override key() (a revision string) and source() (the
(rel, frontmatter, text) list) to plug in a service's own cache; the defaults are `self.revision` and
read_notes(root).
"""
import os
import re
from urllib.parse import quote

import markdown

from .git import Git
from .notes import FRONT_RE, LINK_RE, Note, e, read_notes

IMAGE_EXT = (".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg")
EMBED_RE = re.compile(r"!\[\[([^\]|#]+)(?:#[^\]|]*)?(?:\|([^\]]*))?\]\]")
MDIMG_RE = re.compile(r"!\[([^\]]*)\]\(([^)\s]+)\)")
CALLOUT_RE = re.compile(r"^> \[!(\w+)\][+-]?[ \t]*(.*)$", re.M)
HIDDEN = ("Templates/",)
IGNORED = ("CLAUDE.md",)            # agent instructions, not notes


class Vault:
    def __init__(self, repo, subdir="", git=None):
        self.repo, self.subdir = repo, subdir
        self.root = os.path.join(repo, subdir) if subdir else repo
        self.git = git or Git(repo).run
        self.revision = ""
        self._key = None

    # -- plug points ---------------------------------------------------

    def key(self):
        return self.revision

    def source(self):
        return read_notes(self.root)

    # -- index ---------------------------------------------------------

    def index(self):
        key = self.key()
        if key == self._key:
            return
        notes, by_name, assets = {}, {}, {}
        for rel, fm, text in self.source():
            if os.path.basename(rel) in IGNORED:
                continue
            n = Note(rel, fm, text)
            notes[rel] = n
            base = os.path.splitext(os.path.basename(rel))[0].lower()
            by_name.setdefault(base, rel)
            by_name.setdefault(n.slug.lower(), rel)
        for dirpath, dirnames, filenames in os.walk(self.root):
            dirnames[:] = [d for d in dirnames if not d.startswith(".")]
            for name in filenames:
                if name.lower().endswith(IMAGE_EXT):
                    assets.setdefault(name, os.path.relpath(os.path.join(dirpath, name), self.root))
        self.notes, self.by_name, self.assets = notes, by_name, assets
        for n in notes.values():
            for m in LINK_RE.finditer(n.text):
                target = self.resolve(m.group(1))
                if target and target != n.rel:
                    n.links.add(target)
        self.backlinks = {}
        for n in notes.values():
            for t in n.links:
                self.backlinks.setdefault(t, set()).add(n.rel)
        self.tended = self.tended_dates()
        self._key = key

    def resolve(self, target):
        t = target.strip().rstrip("\\").strip().lower()     # Obsidian escapes the alias pipe inside a table: [[Note\|alias]]
        if t.endswith(".md"):
            t = t[:-3]
        return self.by_name.get(t) or self.by_name.get(t.split("/")[-1])

    def tended_dates(self):
        """{rel: YYYY-MM-DD of the note's latest commit}."""
        out = self.git("log", "--format=@%as", "--name-only", "--", self.subdir or ".")
        dates, current = {}, None
        prefix = self.subdir + "/" if self.subdir else ""
        for line in out.splitlines():
            if line.startswith("@"):
                current = line[1:]
            elif line and line.startswith(prefix) and current:
                dates.setdefault(line[len(prefix):], current)
        return dates

    def get(self, slug):
        self.index()
        return self.notes.get(slug + ".md") or self.notes.get(slug)

    def asset_path(self, rel):
        """Absolute path of a vault image, or None (only images the index knows, so no path escapes)."""
        self.index()
        if self.assets.get(os.path.basename(rel)) != rel:
            return None
        return os.path.join(self.root, rel)

    # -- rendering -----------------------------------------------------

    def render(self, note, base, retro=False, mode="garden", prefix=""):
        """prefix (v0.8): a path put before /n/ and /a/ (Kura's other vaults: "/v/work"); "" = unchanged output."""
        self.index()
        body = FRONT_RE.sub("", note.text, count=1)

        def embed(m):
            name, size = m.group(1).strip(), (m.group(2) or "").strip()
            if name.lower().endswith(IMAGE_EXT):
                path = self.assets.get(os.path.basename(name))
                if not path:
                    return "*(missing image: %s)*" % name
                url = "%s%s/a/%s" % (base, prefix, quote(path))
                if retro and not name.lower().endswith((".jpg", ".jpeg", ".gif")):
                    return "[image: %s](%s)" % (name, url)
                width = ' width="%s"' % size if size.isdigit() else ""
                if retro and not width:
                    width = ' width="480"'  # Netscape on 640/800 px screens
                return '<img src="%s" alt="%s"%s>' % (url, e(name), width)
            return self.link_md(name, None, None, base, mode, prefix)

        def link(m):
            return self.link_md(m.group(1), m.group(2), m.group(3), base, mode, prefix)

        def mdimg(m):
            src = m.group(2)
            if re.match(r"https?://", src):
                return m.group(0)
            name = os.path.basename(src.replace("%20", " "))
            path = self.assets.get(name)
            return ('<img src="%s%s/a/%s" alt="%s">' % (base, prefix, quote(path), e(m.group(1) or name))) if path else ""

        def callout(m):
            kind, title = m.group(1).lower(), m.group(2)
            return "> **%s:** %s" % (e(kind.title()), title) if title else "> **%s:**" % e(kind.title())

        body = EMBED_RE.sub(embed, body)
        body = MDIMG_RE.sub(mdimg, body)
        body = LINK_RE.sub(link, body)
        body = CALLOUT_RE.sub(callout, body)
        out = markdown.markdown(body, extensions=["tables", "fenced_code", "sane_lists", "nl2br"],
                                output_format="html")
        if retro:
            out = out.replace("<table>", '<table border="1" cellpadding="4" cellspacing="0">')
        return out

    def link_md(self, target, anchor, alias, base, mode="garden", prefix=""):
        rel = self.resolve(target)
        target = target.rstrip("\\")                      # the escaped alias pipe of a table cell leaves a backslash
        anchor = anchor.rstrip("\\") if anchor else anchor
        label = alias or target.split("/")[-1]
        note = self.notes.get(rel) if rel else None
        if mode in ("all", "kura") and note and not note.rel.startswith(HIDDEN):    # every note links
            return '<a class="wikilink" href="%s/n/%s%s">%s</a>' % (prefix, quote(note.slug), e(anchor or ""), e(label))
        if note and note.published:
            return '<a class="wikilink" href="%s/n/%s">%s</a>' % (base, quote(note.slug), e(label))
        return '<span class="seed" title="not in the garden">%s</span>' % e(label)
