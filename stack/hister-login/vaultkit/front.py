"""Frontmatter and the small text helpers every vault service needs."""
import datetime
import re

import yaml

FRONT_RE = re.compile(r"\A---\r?\n(.*?)\r?\n---[ \t]*(?:\r?\n|\Z)", re.S)
WIKILINK_RE = re.compile(r"\[\[([^\]|#]+)(?:#[^\]|]*)?(?:\|([^\]]+))?\]\]")

# Unresolved git conflict lines. A note with these doesn't parse, so a service
# can list it as broken instead of silently dropping it.
CONFLICT_RE = re.compile(r"^(<<<<<<< |=======[ \t]*$|>>>>>>> )", re.M)

# A sync tool can write an edit that clashed with git next to the note as
# "<note> (phone conflict <date>).md". Copies carry the original's frontmatter, so they're
# never indexed as notes (no duplicates); a service may list them to merge.
PHONE_CONFLICT = " (phone conflict "


def _str(value):
    if value is None:
        return ""
    if isinstance(value, (datetime.date, datetime.datetime)):
        return value.isoformat()
    return str(value).strip()


def _unlink(value):
    """'[[Some Note]]' -> 'Some Note'."""
    m = WIKILINK_RE.search(_str(value))
    return (m.group(2) or m.group(1)).strip() if m else _str(value)


def note_front(text):
    """The frontmatter dict, or None when there is none or it doesn't parse."""
    m = FRONT_RE.match(text)
    if not m:
        return None
    try:
        fm = yaml.safe_load(m.group(1))
    except yaml.YAMLError:
        return None
    return fm if isinstance(fm, dict) else None


def tags_of(fm):
    tags = (fm or {}).get("tags")
    if tags is None or tags == "":
        return []
    if not isinstance(tags, (list, tuple)):                 # "a, b", a bare scalar like `tags: 2025` (YAML int), …
        tags = str(tags).replace(",", " ").split()
    return [str(t).lstrip("#").strip() for t in tags if t]
