"""Notes: reading them off disk and the fields every view uses (title, tags, stage, type, summary)."""
import datetime
import html
import os
import re

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
    wikilink name (Vault.by_name), so callers must not reorder it."""
    out = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if not d.startswith(".") and d not in SKIP_DIRS]
        for name in filenames:
            if name.endswith(".md") and PHONE_CONFLICT not in name:
                full = os.path.join(dirpath, name)
                try:
                    with open(full, encoding="utf-8", errors="replace") as f:
                        text = f.read()
                except OSError:
                    continue
                out.append((os.path.relpath(full, root), note_front(text) or {}, text))
    return out


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
