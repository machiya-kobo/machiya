"""Notes: reading them off disk and the fields every view uses (title, tags, stage, type, summary)."""
import copy
import datetime
import html
import os
import re
import stat

from .front import FRONT_RE, PHONE_CONFLICT, _str, note_front, tags_of

STAGES = [("evergreen", "Evergreen", "green"), ("budding", "Budding", "teal"), ("seedling", "Seedling", "yellow")]
STAGE_MARK = {"evergreen": "[E]", "budding": "[B]", "seedling": "[S]"}
CONFIDENCE = ("certain", "likely", "possible", "speculative")
TYPES = [("note", "Notes"), ("project", "Projects"), ("idea", "Ideas"), ("map", "Maps"), ("log", "Logs")]
SKIP_DIRS = ("Templates",)         # besides dot directories

LINK_RE = re.compile(r"(?<!!)\[\[([^\]|#]+)(#[^\]|]*)?(?:\|([^\]]+))?\]\]")


def e(text):
    return html.escape(str(text or ""), quote=True)


def read_notes(root):
    """(rel, frontmatter, text) for every .md under root: dot directories and Templates/ skipped, sync-tool conflict copies left out, frontmatter {} when missing. os.walk order, unsorted: the first note seen wins a
    wikilink name (Vault.by_name), so callers must not reorder it.

    Symlinks are never followed (v0.22, KURA-2): a symlinked note is skipped (os.walk already doesn't descend into a
    symlinked folder), and the file is opened with O_NOFOLLOW, so a committed `x.md -> /proc/self/environ` reads
    nothing."""
    out, was, now = [], _READ.get(os.path.abspath(root), {}), {}
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if not d.startswith(".") and d not in SKIP_DIRS]
        for name in filenames:
            if name.endswith(".md") and PHONE_CONFLICT not in name:
                full = os.path.join(dirpath, name)
                try:
                    st = os.lstat(full)
                except OSError:
                    continue
                if not stat.S_ISREG(st.st_mode):        # a symlink or not a file: read_file would refuse it too
                    continue
                key = (st.st_dev, st.st_ino, st.st_size, st.st_mtime_ns, st.st_ctime_ns)
                hit = was.get(full)
                if hit and hit[0] == key:
                    fm, text = hit[1], hit[2]
                else:
                    text = read_file(full)
                    if text is None:
                        continue
                    fm = note_front(text) or {}
                now[full] = (key, fm, text)
                out.append((os.path.relpath(full, root), copy.deepcopy(fm), text))
    _READ[os.path.abspath(root)] = now
    return out


# v0.28: what read_notes last read under each root, by file: (stat key, frontmatter, text). A file whose device, inode,
# size, modification and change times are all unchanged isn't read or parsed again (the change time can't be set back
# by a tool that keeps the modification time). Each call replaces its root's entry, so a deleted note drops out.
_READ = {}


def read_file(path):
    """A vault file's text, or None when it is a symlink, not a regular file or unreadable (v0.22). Opened with
    O_NOFOLLOW where the platform has it, and checked again with fstat, so nothing outside the vault is read through
    a link."""
    try:
        fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0))
    except OSError:
        return None
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode) or os.path.islink(path):
            return None
        with os.fdopen(fd, encoding="utf-8", errors="replace") as f:
            fd = None
            return f.read()
    except OSError:
        return None
    finally:
        if fd is not None:
            os.close(fd)


def safe_path(root, rel):
    """The absolute path for writing `rel` under `root`, or ValueError (v0.22, KURA-2): `rel` must be relative with no
    `..`, and no part of it under root may be a symlink (the file itself included, when it exists), so a write never
    lands outside the vault or on a file a link points at. The parent folders may not exist yet."""
    if not isinstance(rel, str) or not rel or "\0" in rel or os.path.isabs(rel):
        raise ValueError("not a relative path")
    parts = rel.replace("\\", "/").split("/")
    if any(p in ("", ".", "..") for p in parts):
        raise ValueError("not a plain relative path")
    path = os.path.realpath(root)
    for part in parts:
        path = os.path.join(path, part)
        if os.path.islink(path):
            raise ValueError("%s is a symlink" % os.path.relpath(path, os.path.realpath(root)))
    return path


BOARD_STATUSES = ("backlog", "ready", "wip", "blocked", "done", "archived")
NOTE_STATUSES = ("draft", "active", "archive")


def note_status(fm):
    """A note's status: the `status:` field (docs/frontmatter.md), else the older `status/*` tag, else
    ''. Cards have board values (backlog...archived, falling back to the older `board:` field); other notes have
    draft/active/archive. Both spellings are read."""
    value = _str(fm.get("status")).lower()
    if value:
        return value
    board = _str(fm.get("board")).lower()
    if board:
        return board
    return next((t[7:] for t in tags_of(fm) if t.startswith("status/") and len(t) > 7), "")


def created_of(fm):
    """`created:` (the schema name), else the older `date:`, as YYYY-MM-DD or ''."""
    return (_str(fm.get("created")) or _str(fm.get("date")))[:10]


def stage_of(fm):
    """`growth:` wins. Otherwise drafts and ideas are seedlings and everything
    else is budding: evergreen has to be chosen."""
    growth = _str(fm.get("growth")).lower()
    if growth in ("seedling", "budding", "evergreen"):
        return growth
    tags = tags_of(fm)
    # A card in Backlog is a draft too: older notes carry the tag status/draft, newer ones only `status: backlog`,
    # so both keep it a seedling.
    if note_status(fm) in ("draft", "backlog") or "status/draft" in tags or "type/idea" in tags:
        return "seedling"
    return "budding"


def type_of(rel, fm):
    tags = tags_of(fm)
    if "type/moc" in tags or rel.startswith("MOC/"):
        return "map"
    for t in ("project", "idea", "log"):
        if "type/" + t in tags:
            return t
    return "note"


def relative(value, today=None):
    """'2024-03-01' -> '2 years ago' (today, yesterday, 3 days ago, 2 weeks ago, ...)."""
    try:
        d = datetime.date.fromisoformat(_str(value)[:10])
    except ValueError:
        return ""
    n = ((today or datetime.date.today()) - d).days
    if n <= 0:
        return "today"
    if n == 1:
        return "yesterday"
    if n < 14:
        return "%d days ago" % n
    if n < 60:
        return "%d weeks ago" % (n // 7)
    if n < 365:
        return "%d months ago" % (n // 30)
    years = n // 365
    return "a year ago" if years == 1 else "%d years ago" % years


PARA_SKIP = ("|", "<!--", "- ", "* ", "#", ">", "!", "```", "**Status", "**Host")


def first_paragraph(text, limit=160):
    body = FRONT_RE.sub("", text, count=1)
    body = re.sub(r"<!--.*?-->", "", body, flags=re.S)
    fence = False
    for para in re.split(r"\n\s*\n", body):
        lines = []
        for l in para.splitlines():
            if l.startswith("```"):
                fence = not fence
                continue
            if fence or not l.strip() or l.lstrip().startswith(PARA_SKIP):
                continue
            lines.append(l.strip())
        if lines:
            t = " ".join(lines)
            t = LINK_RE.sub(lambda m: (m.group(3) or m.group(1)).split("/")[-1], t)
            t = re.sub(r"[*_`]", "", t).strip().rstrip(",;: ")
            if len(t) < 24 and (lines[0].startswith("**") or ":" in t or not t.endswith(".")) and len(lines) == 1:
                continue  # a bold label or a field line, not prose
            return t if len(t) <= limit else t[:limit - 1].rstrip() + "…"
    return ""


class Note:
    __slots__ = ("rel", "fm", "text", "title", "tags", "published", "stage", "links", "slug",
                 "confidence", "pinned", "planted", "ntype", "_description")

    def __init__(self, rel, fm, text):
        self.rel, self.fm, self.text = rel, fm, text
        self.title = _str(fm.get("title")) or os.path.splitext(os.path.basename(rel))[0]
        self.tags = tags_of(fm)
        self.published = fm.get("publish") is True
        self.stage = stage_of(fm)
        conf = _str(fm.get("confidence")).lower()
        self.confidence = conf if conf in CONFIDENCE else ""
        self.pinned = fm.get("garden_pin") is True
        self.planted = created_of(fm)
        self.ntype = type_of(rel, fm)
        self.links = set()
        self.slug = rel[:-3] if rel.endswith(".md") else rel
        self._description = None

    @property
    def description(self):
        if self._description is None:
            self._description = _str(self.fm.get("summary")) or first_paragraph(self.text)
        return self._description
