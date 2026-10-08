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
from .notes import FRONT_RE, LINK_RE, Note, e, read_notes, safe_path
from .sanitize import clean

IMAGE_EXT = (".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg")
EMBED_RE = re.compile(r"!\[\[([^\]|#]+)(?:#[^\]|]*)?(?:\|([^\]]*))?\]\]")
MDIMG_RE = re.compile(r"!\[([^\]]*)\]\(([^)\s]+)\)")
CALLOUT_RE = re.compile(r"^> \[!(\w+)\][+-]?[ \t]*(.*)$", re.M)
TASK_RE = re.compile(r"<li>(<p>)?\[([ xX])\] ")
SMALLWEB_AUTOLINK_RE = re.compile(r"<((?:gemini|gopher)://[^\s<>\"']+)>", re.I)   # Markdown autolinks only http(s)
# v0.22: the oldest Python-Markdown vaultkit runs with. 3.7 to 3.10 on Python 3.13 run out of memory on a note with two
# unclosed `<!--` in separate paragraphs (their HTML-block preprocessor and the newer html.parser): one note takes a
# room down. 3.11 renders it in milliseconds.
MIN_MARKDOWN = (3, 11)


def markdown_ok(version):
    """Is this Python-Markdown version string at least MIN_MARKDOWN?"""
    parts = re.findall(r"\d+", str(version or ""))[:2]
    return len(parts) == 2 and tuple(int(p) for p in parts) >= MIN_MARKDOWN


if not markdown_ok(getattr(markdown, "__version__", "")):
    raise ImportError("vaultkit needs Python-Markdown %d.%d or later, found %s: older versions can be driven out of "
                      "memory by a single note. Install it with pip (pip install 'markdown>=%d.%d'), in a venv if the "
                      "system's package is older." % (MIN_MARKDOWN + (getattr(markdown, "__version__", "?"),) + MIN_MARKDOWN))

HIDDEN = ("Templates/",)
IGNORED = ("CLAUDE.md",)            # agent instructions, not notes


def resolve_in(by_name, target):
    """A wikilink target -> the note's rel in by_name, or None (the resolution rule in this module's docstring)."""
    t = target.strip().rstrip("\\").strip().lower()     # Obsidian escapes the alias pipe inside a table: [[Note\|alias]]
    if t.endswith(".md"):
        t = t[:-3]
    return by_name.get(t) or by_name.get(t.split("/")[-1])


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
        """v0.28: the new index is built aside (notes, names, images, links, backlinks, dates) and then put in place
        together, so a reader in another thread never sees notes without their links or a half-filled backlink map."""
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
                if name.lower().endswith(IMAGE_EXT) and not os.path.islink(os.path.join(dirpath, name)):   # v0.22
                    assets.setdefault(name, os.path.relpath(os.path.join(dirpath, name), self.root))
        for n in notes.values():
            for m in LINK_RE.finditer(n.text):
                target = resolve_in(by_name, m.group(1))
                if target and target != n.rel:
                    n.links.add(target)
        backlinks = {}
        for n in notes.values():
            for t in n.links:
                backlinks.setdefault(t, set()).add(n.rel)
        self._walk_times = {}
        tended = self.tended_dates()
        tended_at = self._walk_times
        self.notes, self.by_name, self.assets, self.backlinks = notes, by_name, assets, backlinks
        self.tended, self.tended_at = tended, tended_at
        self._key = key

    def resolve(self, target):
        return resolve_in(self.by_name, target)

    def tended_dates(self):
        """{rel: YYYY-MM-DD of the note's latest commit}. v0.22 (KURA-5): core.quotePath=false, so a non-ASCII name
        (町家.md, café notes.md) isn't printed quoted and octal-escaped, and keeps its date.

        v0.28: one walk, then only what's new. The walk remembers the commit it reached; when HEAD has moved on from it,
        only the new commits are walked and their dates laid over the old ones (a note's newest commit wins, as in a
        full walk). After a history rewrite (the old commit isn't an ancestor any more) it walks everything again. The
        same walk gives each note's commit time in seconds, as Vault.tended_at after index()."""
        head = self.git("rev-parse", "HEAD").strip()
        walked = getattr(self, "_walked", None)
        if head and walked and walked[0] == head:
            dates, times = walked[1], walked[2]
        elif head and walked and self.git("merge-base", walked[0], head).strip() == walked[0]:
            new_dates, new_times = self._walk("%s..%s" % (walked[0], head))
            dates, times = dict(walked[1]), dict(walked[2])
            dates.update(new_dates)
            times.update(new_times)
        else:
            dates, times = self._walk(head) if head else self._walk()
        if head:
            self._walked = (head, dates, times)
        self._walk_times = times
        return dates

    def _walk(self, *revs):
        """({rel: date}, {rel: seconds}) of each file's newest commit in `git log revs` under the vault."""
        out = self.git("-c", "core.quotePath=false", "log", "--format=@%as %at", "--name-only", *revs, "--",
                       self.subdir or ".")
        dates, times, current = {}, {}, None
        prefix = self.subdir + "/" if self.subdir else ""
        for line in out.splitlines():
            if line.startswith("@"):
                day, _, at = line[1:].partition(" ")
                current = (day, int(at) if at.isdigit() else 0)
            elif line and line.startswith(prefix) and current:
                rel = line[len(prefix):]
                if rel not in dates:
                    dates[rel], times[rel] = current
        return dates, times

    def get(self, slug):
        self.index()
        return self.notes.get(slug + ".md") or self.notes.get(slug)

    def asset_path(self, rel):
        """Absolute path of a vault image, or None (only images the index knows, so no path escapes; never a symlink,
        v0.22)."""
        self.index()
        if self.assets.get(os.path.basename(rel)) != rel:
            return None
        try:
            return safe_path(self.root, rel)
        except ValueError:
            return None

    # -- rendering -----------------------------------------------------

    def render(self, note, base, retro=False, mode="garden", prefix="", remote_images="load"):
        """prefix (v0.8): a path put before /n/ and /a/ (Kura's other vaults: "/v/work"); "" = unchanged output.
        The result is clean (v0.13, vaultkit.sanitize): raw HTML in a note never runs; "- [ ]" / "- [x]" items are
        checkboxes (class "task"), and bare URLs in the text are links. remote_images (v0.29): "click" for a page,
        where an image from another site waits for a click (a link on a retro page); "load" (the default): as before,
        for API answers and anything another program renders."""
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
                                extension_configs={"tables": {"use_align_attribute": True}}, output_format="html")
        out = TASK_RE.sub(lambda m: '<li class="task">%s<input type="checkbox"%s disabled> '
                          % (m.group(1) or "", " checked" if m.group(2) != " " else ""), out)
        out = SMALLWEB_AUTOLINK_RE.sub(lambda m: '<a href="%s">%s</a>' % (m.group(1), m.group(1)), out)
        mode_img = "link" if remote_images == "click" and retro else remote_images
        out = clean(out, autolink=True, remote_images=mode_img, own="%s%s/a/" % (base, prefix))
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
