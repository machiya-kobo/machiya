"""NotesWriter: the one place machiya-mcp writes the vault (notes_create, notes_update). Needs vaultkit (vendored
beside this file, or at ../../vaultkit in the machiya repo) and git; imported only when a write clone is configured.

In short: the default vault only (the clone's subdirectory), paths and names checked, the owner's tags and fields
refused, every update carries the version the caller read, one commit per call as a bot author, pushed at once through
vaultkit's GitSync (pull first; a rejected push is pulled and retried once; conflict markers are never committed), and
the file is re-read afterwards to say whether it landed as written. Which folders, tags and blocks belong to other
tools is configuration (`Rules`, from MCP_NOTES_*), not code."""
import os
import re
import subprocess
import threading
from datetime import datetime
from zoneinfo import ZoneInfo

from vaultkit.front import CONFLICT_RE, FRONT_RE, note_front, tags_of
from vaultkit.frontmatter import EditError, edit_front, yaml_scalar
from vaultkit.git import borrow
from vaultkit.gitsync import GitSync

from rooms import ToolError
from textver import text_version

MAX_BODY = 100_000
NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 _.()&',!-]*$")
TAG_RE = re.compile(r"^[a-z0-9]+/[A-Za-z0-9][A-Za-z0-9._-]*$")
CARD_STATUS = {"backlog", "ready", "wip", "blocked", "done", "archived"}
NOTE_STATUS = {"draft", "active", "archive"}
HEADING_RE = re.compile(r"^(#{1,6})[ \t]+(.+?)[ \t]*#*[ \t]*$")
FENCE_RE = re.compile(r"^[ \t]*(```|~~~)")
SECRET_RE = re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----|AKIA[0-9A-Z]{16}|gh[pousr]_[A-Za-z0-9]{30,}|xox[abprs]-[A-Za-z0-9-]{10,}"
                       r"|(?i:(?:password|passwd|secret|token|api[_-]?key)[\"']?\s*[:=]\s*[\"']?[A-Za-z0-9+/_=.-]{16,})")


def _matcher(entry):
    """A vault-relative path rule: a prefix (a folder `Dir/` or a file), or, with `*`, a glob over the whole path. Case-insensitive,
    because common file systems see `Dir/` and `dir/` as one folder."""
    low = entry.lower()
    if "*" in low:
        rx = re.compile(".*".join(re.escape(x) for x in low.split("*")), re.S)
        return lambda path: bool(rx.fullmatch(path))
    return lambda path: path.startswith(low)


class Rules:
    """What the write tools leave alone, from configuration (mcp.Config.notes_rules). The defaults protect templates and
    archives only; a vault whose folders, tags or blocks are managed by other tools lists them here.

    never_write      paths never written, by create or update (prefixes, files, `*` globs)
    never_create     paths a new note may not take (a note there may still be updated)
    default_folder   where notes_create puts a note when the caller gives a title only
    refused_tags     tags never set from here: exact, or `prefix*`
    required_tags    tag prefixes (`kind/`) a note must carry at least one of
    create_tags      on a new note, tags in the namespaces named here must be one of these
    card_markers     folders (`Dir/`) and tags that mark a note as a board card, whose status and tags have their own tools
    status_locked    folders (`Dir/`) and tags whose notes' status is managed by another tool
    managed_markers  names of `<!-- name:start -->` / `<!-- name:end -->` blocks another tool rewrites: never edited"""

    def __init__(self, never_write=("Templates/", "Archive/"), never_create=(), default_folder="Inbox", refused_tags=(),
                 required_tags=(), create_tags=(), card_markers=(), status_locked=(), managed_markers=()):
        self.never_write = [(e, _matcher(e)) for e in never_write]
        self.never_create = [(e, _matcher(e)) for e in never_create]
        self.default_folder = default_folder.strip("/") or "Inbox"
        self.refused_tags = tuple(refused_tags)
        self.required_tags = tuple(required_tags)
        self.create_tags = tuple(create_tags)
        self.card_folders = tuple(e for e in card_markers if e.endswith("/"))
        self.card_tags = frozenset(e for e in card_markers if not e.endswith("/"))
        self.locked_folders = tuple(e for e in status_locked if e.endswith("/"))
        self.locked_tags = frozenset(e for e in status_locked if not e.endswith("/"))
        self.marker_re = (re.compile(r"<!--\s*(%s):(?:start|end)\s*-->" % "|".join(re.escape(m) for m in managed_markers))
                          if managed_markers else None)

    @staticmethod
    def first(rules, low):
        return next((entry for entry, match in rules if match(low)), None)

    def tag_refused(self, tag):
        return any(tag.startswith(r[:-1]) if r.endswith("*") else tag == r for r in self.refused_tags)

    def describe(self):
        """The part of these rules a caller needs to choose tags and a folder, for the tool description."""
        out = []
        if self.required_tags:
            out.append("a note needs at least one tag starting with each of %s" % ", ".join(self.required_tags))
        if self.create_tags:
            out.append("a new note's tags in those namespaces are one of %s" % ", ".join(self.create_tags))
        out.append("the default folder is %s/" % self.default_folder)
        return "; ".join(out) + "."


def one_line(text, what, limit):
    text = (text or "").strip()
    if "\n" in text or "\r" in text:
        raise ToolError("%s must be one line" % what)
    if len(text) > limit:
        raise ToolError("%s is at most %d characters" % (what, limit))
    return text


def check_text(text, what="text", rules=None):
    if len(text) > MAX_BODY:
        raise ToolError("%s is at most %d characters" % (what, MAX_BODY))
    if "\x00" in text:
        raise ToolError("%s has a NUL character" % what)
    bad = CONFLICT_RE.search(text)
    if bad:     # the board flags a note as broken on any of these, including a bare ======= (a setext underline)
        raise ToolError("%s has a line the vault reads as a git conflict marker (%r): rewrite it (use # headings, not a ======= underline)"
                        % (what, text[bad.start():text.find("\n", bad.start()) if text.find("\n", bad.start()) > 0 else None][:30]))
    block = rules.marker_re.search(text) if rules and rules.marker_re else None
    if block:
        raise ToolError("%s touches a %s marker: that block belongs to another tool" % (what, block.group(1)))
    if SECRET_RE.search(text):
        raise ToolError("%s looks like it holds a secret (key, token or password): refer to it by name instead" % what)


def sections(lines):
    """[(index, level, title)] of the headings outside code fences."""
    out, fence = [], False
    for i, line in enumerate(lines):
        if FENCE_RE.match(line):
            fence = not fence
        elif not fence:
            m = HEADING_RE.match(line)
            if m:
                out.append((i, len(m.group(1)), m.group(2).strip()))
    return out


def bootstrap(repo, url, reference="", sparse=(), env=None):
    """Clone the vault repo once (borrowing the mirror's objects when there is one); a no-op when it's there."""
    if os.path.isdir(os.path.join(repo, ".git")):
        return
    os.makedirs(os.path.dirname(repo.rstrip("/")) or ".", exist_ok=True)
    cmd = ["git", "clone", "-q"] + (["--reference", reference, "--no-checkout"] if reference else []) + [url, repo]
    subprocess.run(cmd, check=True, timeout=1800, env=dict(os.environ, **(env or {})))
    if reference:
        borrow(repo, reference, tuple(sparse))
        subprocess.run(["git", "-C", repo, "checkout", "-q"], check=True, timeout=600, env=dict(os.environ, **(env or {})))


class NotesWriter:
    def __init__(self, repo, subdir="", author=("machiya-mcp", "machiya-mcp@localhost"), env=None, today=None,
                 tz="UTC", rules=None):
        self.rules = rules or Rules()
        self.repo, self.subdir = repo.rstrip("/"), subdir.strip("/")
        self.root = os.path.join(self.repo, self.subdir)
        self.sync = GitSync(self.repo, author, paths=[self.subdir or "."], label="notes", env=env)
        self.lock = threading.Lock()
        self.today = today or (lambda: datetime.now(ZoneInfo(tz)).date())     # the day in the configured zone, not UTC's
        self._tags = (None, set())

    # -- what exists -----------------------------------------------------------------------------------------------
    def known_tags(self):
        head = self.sync.head()
        if self._tags[0] == head and head:
            return self._tags[1]
        tags = set()
        for base, dirs, files in os.walk(self.root):
            dirs[:] = [d for d in dirs if not d.startswith(".") and d != "Templates"]
            for name in files:
                if name.endswith(".md"):
                    try:
                        with open(os.path.join(base, name), encoding="utf-8") as f:
                            tags.update(t for t in tags_of(note_front(f.read())) if TAG_RE.match(t))
                    except (OSError, UnicodeDecodeError):
                        continue
        self._tags = (head, tags)
        return tags

    def check_tags(self, tags, what="tags", creating=False):
        bad_form = [t for t in tags if not TAG_RE.match(t) or t.startswith("status/")]
        if bad_form:
            raise ToolError("%s: %s are not tags of the form type/name (status/* tags are retired)" % (what, ", ".join(bad_form)))
        refused = [t for t in tags if self.rules.tag_refused(t)]
        if refused:
            raise ToolError("%s: %s are managed by other tools (a board card is made with board_add_backlog) and are not set from here"
                            % (what, ", ".join(refused)))
        if creating and self.rules.create_tags:
            spaces = {t.split("/")[0] + "/" for t in self.rules.create_tags}
            bad_type = [t for t in tags if t.startswith(tuple(spaces)) and t not in self.rules.create_tags]
            if bad_type:
                raise ToolError("a new note's tags in %s are one of %s" % (", ".join(sorted(spaces)), ", ".join(sorted(self.rules.create_tags))))
        unknown = [t for t in tags if t not in self.known_tags()]
        if unknown:
            raise ToolError("unknown tag(s) %s: a new tag is the owner's to create; use existing tags" % ", ".join(unknown), needs_owner=True)

    def check_required_tags(self, tags, keeps=False):
        missing = [p for p in self.rules.required_tags if not any(t.startswith(p) for t in tags)]
        if missing:
            raise ToolError("a note %s at least one tag starting with each of %s (missing: %s)"
                            % ("keeps" if keeps else "needs", ", ".join(self.rules.required_tags), ", ".join(missing)))

    def check_path(self, path, creating):
        rel = (path or "").strip()
        if not rel or len(rel) > 200 or rel.startswith("/") or "\\" in rel or "\x00" in rel:
            raise ToolError("path is a vault-relative Folder/Name.md of at most 200 characters")
        parts = rel.split("/")
        if any(p in ("", ".", "..") or p.startswith(".") for p in parts) or not parts[-1].endswith(".md"):
            raise ToolError("path must be Folder/Name.md without '..' or hidden parts")
        stem = parts[-1][:-3]
        if not all(NAME_RE.match(p) for p in parts[:-1] + [stem]):
            raise ToolError("names may use letters, digits, spaces and - _ . ( ) & ' , ! only")
        low = rel.lower()            # common file systems see Dir/ and dir/ as one folder
        if parts[-1].lower() == "claude.md":
            raise ToolError("CLAUDE.md files are not written from here")
        hit = Rules.first(self.rules.never_write, low)
        if hit:
            raise ToolError("%s is never written from here: it is protected or managed by another tool" % hit)
        if creating:
            if "/" not in rel:
                raise ToolError("a new note goes in a folder (default %s/), not the vault root" % self.rules.default_folder)
            hit = Rules.first(self.rules.never_create, low)
            if hit:
                if any(hit.lower() == f.lower() for f in self.rules.card_folders):
                    raise ToolError("not created here: cards are made with board_add_backlog")
                raise ToolError("not created here: %s is managed by another tool" % hit)
            top = parts[0]
            folders = [d for d in os.listdir(self.root) if os.path.isdir(os.path.join(self.root, d))] if os.path.isdir(self.root) else []
            if top not in folders:
                same = [d for d in folders if d.lower() == top.lower()]
                if same:
                    raise ToolError("use the folder's exact name, %s/ (not %s/)" % (same[0], top))
                raise ToolError("%r is not an existing folder: a new top-level folder is the owner's to create (nested folders "
                                "below an existing one are fine)" % top, needs_owner=True)
        full = os.path.realpath(os.path.join(self.root, rel))
        if not full.startswith(os.path.realpath(self.root) + os.sep):
            raise ToolError("path leaves the vault")
        walk = self.root
        for p in parts[:-1]:
            walk = os.path.join(walk, p)
            if os.path.islink(walk):
                raise ToolError("path goes through a symlink")
        return rel

    def path_of(self, rel):
        return os.path.join(self.root, rel)

    def read(self, rel):
        try:
            with open(self.path_of(rel), encoding="utf-8") as f:
                return f.read()
        except FileNotFoundError:
            return None

    # -- git ---------------------------------------------------------------------------------------------------------
    def ready(self):
        if not os.path.isdir(os.path.join(self.repo, ".git")) or not self.sync.pull():
            raise ToolError("the vault clone can't sync with its remote right now; nothing was written. Retry in a moment.")

    def commit(self, rel, verb, agent, intended):
        """Commit one file, push, and report whether it landed as written."""
        relrepo = "%s/%s" % (self.subdir, rel) if self.subdir else rel
        before = self.sync.head()
        self.sync.git("add", "--", relrepo)
        self.sync.git("commit", "-q", "-m", "notes: %s %s (%s)" % (verb, rel, agent if agent.startswith("mcp:") else "mcp:" + agent[:60]))
        if self.sync.head() == before:
            if verb == "create":
                try:
                    os.unlink(self.path_of(rel))
                except OSError:
                    pass
            raise ToolError("the write could not be committed; nothing changed")
        self.sync.push()
        if self.sync.ahead():
            self.sync.pull()
            self.sync.push()
        final = self.read(rel)
        out = {"path": rel, "commit": self.sync.head()[:12], "pushed": not self.sync.ahead(), "version": text_version(final or "")}
        if final != intended:
            out["conflict"] = ("someone edited this note at the same moment and the merge didn't keep your change as written: "
                               "read the note again and redo it")
        if not out["pushed"]:
            out["warning"] = "committed in the write clone but not pushed yet; the next write or sync retries"
        return out

    # -- create ------------------------------------------------------------------------------------------------------
    def create(self, path, title, body, tags, summary, status, agent):
        title = one_line(title, "title", 120)
        summary = one_line(summary, "summary", 200)
        if not title:
            raise ToolError("title is required")
        check_text(title, "title", self.rules)
        check_text(summary, "summary", self.rules)
        tags = [x.strip().lstrip("#") for x in tags]
        if status not in ("draft", "active"):
            raise ToolError("status is draft or active for a new note")
        check_text(body or "", "body", self.rules)
        if FRONT_RE.match(body or ""):
            raise ToolError("body must not start with its own frontmatter block: the tool writes it")
        self.check_tags(tags, creating=True)
        self.check_required_tags(tags)
        rel = self.check_path(path, creating=True)
        heading = "" if re.match(r"\s*#\s", body or "") else "# %s\n\n" % title
        lines = ["---", "title: %s" % yaml_scalar(title), "created: %s" % self.today().isoformat(), "tags:" if tags else "tags: []"]
        lines += ["  - %s" % t for t in dict.fromkeys(tags)]
        lines += ["aliases: []", "status: %s" % status, "summary: %s" % yaml_scalar(summary), "---", ""]
        text = "\n".join(lines) + "\n" + heading + (body or "").strip("\n") + "\n"
        with self.lock:
            self.ready()
            if os.path.exists(self.path_of(rel)):
                raise ToolError("%s already exists: read it and use notes_update" % rel)
            stem = os.path.basename(rel)[:-3].lower()      # wikilinks resolve by file name across the whole vault
            for base, dirs, files in os.walk(self.root):
                dirs[:] = [d for d in dirs if not d.startswith(".")]
                same = [f for f in files if f.lower() == stem + ".md"]
                if same:
                    raise ToolError("a note named %r already exists (%s): links resolve by name, so pick another name or update that one"
                                    % (stem, os.path.relpath(os.path.join(base, same[0]), self.root)))
            os.makedirs(os.path.dirname(self.path_of(rel)), exist_ok=True)
            with open(self.path_of(rel), "w", encoding="utf-8") as f:
                f.write(text)
            return dict(self.commit(rel, "create", agent, text), created=True)

    # -- update ------------------------------------------------------------------------------------------------------
    def update(self, path, expected_version, mode, agent, text="", heading="", create_if_missing=False, join="paragraph",
               confirm=False, tags_add=(), tags_remove=(), summary=None, status=None):
        rel = self.check_path(path, creating=False)
        if mode not in ("append", "replace_section", "replace_body", "frontmatter"):
            raise ToolError("mode is append, replace_section, replace_body or frontmatter")
        if mode == "replace_body" and confirm is not True:
            raise ToolError("replace_body overwrites the whole note: ask the owner, then call again with confirm=true", needs_owner=True)
        if mode in ("append", "replace_section", "replace_body"):
            check_text(text, "text", self.rules)
            if mode != "replace_body" and not text.strip():
                raise ToolError("text is required")
        with self.lock:
            self.ready()
            current = self.read(rel)
            if current is None:
                raise ToolError("%s does not exist: create it with notes_create" % rel)
            if text_version(current) != expected_version:
                raise ToolError("the note changed since you read it (an edit from another device, or the board rewriting a card's frontmatter): "
                                "read it again and redo the edit", code="version_conflict", current_version=text_version(current))
            m = FRONT_RE.match(current)
            head, body = (current[:m.end()], current[m.end():]) if m else ("", current)
            fm = note_front(current) or {}
            tags_now = tags_of(fm)
            is_card = "project" in fm or fm.get("status") in CARD_STATUS or bool(self.rules.card_tags & set(tags_now))
            published = str(fm.get("publish")).strip().lower() in ("true", "yes", "1")
            if published and confirm is not True:
                raise ToolError("this note is published in the garden: an edit changes the public site. Ask the owner, then call again "
                                "with confirm=true", needs_owner=True)
            if mode == "frontmatter":
                new = self.edit_frontmatter(current, fm, is_card, tags_add, tags_remove, summary, status, rel, tags_now)
            elif mode == "append":
                sep = "\n" if join == "line" else "\n\n"
                new = head + (body.rstrip("\n") + sep if body.strip() else "") + text.strip("\n") + "\n"
            elif mode == "replace_body":
                block = self.rules.marker_re.search(body) if self.rules.marker_re else None
                if block:
                    raise ToolError("this note has a %s block: use replace_section or append, not replace_body" % block.group(1))
                new = head + ("\n" if head else "") + text.strip("\n") + "\n"
            else:
                new = head + self.replace_section(body, heading, text, create_if_missing)
            new = new.replace("\r\n", "\n").rstrip("\n") + "\n"
            if new == current:
                return {"path": rel, "changed": False, "version": expected_version}
            if len(new) > MAX_BODY * 2 or CONFLICT_RE.search(new):
                raise ToolError("the edit would make the note too large or carry conflict markers")
            if note_front(new) is None and m:
                raise ToolError("the edit would leave the frontmatter unreadable")
            with open(self.path_of(rel), "w", encoding="utf-8") as f:
                f.write(new)
            out = dict(self.commit(rel, "update", agent, new), changed=True, mode=mode)
            if published:
                out["published_note"] = True
            return out

    def edit_frontmatter(self, text, fm, is_card, tags_add, tags_remove, summary, status, rel="", tags_now=()):
        if not FRONT_RE.match(text):
            raise ToolError("this note has no frontmatter to edit")
        scalars, tags = {}, None
        if summary is not None:
            scalars["summary"] = one_line(summary, "summary", 200)
            check_text(scalars["summary"], "summary", self.rules)
        if status is not None:
            if is_card or rel.startswith(self.rules.card_folders):
                raise ToolError("this note is a board card: its status is changed with board_move, not here")
            locked = next(iter([f for f in self.rules.locked_folders if rel.startswith(f)] + sorted(self.rules.locked_tags & set(tags_now))), None)
            if locked:
                raise ToolError("this note's status is managed by another tool (%s): only tags and summary are changed here" % locked)
            if status not in NOTE_STATUS:
                raise ToolError("status is draft, active or archive")
            scalars["status"] = status
        if tags_add or tags_remove:
            if is_card:
                raise ToolError("this note is a board card: its tags are changed with board_tag, not here")
            tags_add = [x.strip().lstrip("#") for x in tags_add]
            tags_remove = [x.strip().lstrip("#") for x in tags_remove]
            self.check_tags(list(tags_add), "tags_add")
            cur = list(tags_now) or tags_of(fm)
            new = [t for t in cur if t not in set(tags_remove)] + [t for t in tags_add if t not in cur]
            if new != cur:
                self.check_required_tags(new, keeps=True)
                tags = new
        if not scalars and tags is None:
            raise ToolError("nothing to change: give summary, status, tags_add or tags_remove")
        try:
            return edit_front(text, scalars, tags)
        except EditError as e:
            raise ToolError(e.message)

    def replace_section(self, body, heading, text, create_if_missing):
        lines = body.split("\n")
        heads = sections(lines)
        want = (heading or "").strip().lstrip("#").strip().lower()
        if not want:
            raise ToolError("heading is required for replace_section")
        hit = next(((i, lv) for i, lv, t in heads if t.lower() == want), None)
        if hit is None:
            if not create_if_missing:
                raise ToolError("no heading %r in the note; pass create_if_missing to add it at the end" % heading)
            return body.rstrip("\n") + "\n\n## %s\n\n%s\n" % (heading.strip().lstrip("#").strip(), text.strip("\n"))
        start, level = hit
        end = next((i for i, lv, t in heads if i > start and lv <= level), len(lines))
        block = next((m for m in (self.rules.marker_re.search(l) for l in lines[start:end]) if m), None) if self.rules.marker_re else None
        if block:
            raise ToolError("that section holds a %s block: it isn't edited from here" % block.group(1))
        new = lines[:start + 1] + [""] + text.strip("\n").split("\n") + [""] + lines[end:]
        return "\n".join(new).rstrip("\n") + "\n"
