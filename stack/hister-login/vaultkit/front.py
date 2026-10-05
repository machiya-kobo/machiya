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

# v0.22 (LEAD-4): frontmatter bigger than this isn't parsed (the note reads as having none), and YAML aliases are
# refused, so a 450-byte "billion laughs" can't make a room build a 5 GB title.
MAX_FRONT = 64 * 1024


class _NoAliases(yaml.SafeLoader):
    """yaml.SafeLoader that refuses aliases (*name). Anchors alone are harmless and still load."""

    def compose_node(self, parent, index):
        if self.check_event(yaml.AliasEvent):
            event = self.peek_event()
            raise yaml.composer.ComposerError(None, None, "YAML aliases are not allowed in frontmatter",
                                              event.start_mark)
        return super().compose_node(parent, index)


def load_yaml(text):
    """yaml.safe_load without aliases and at most MAX_FRONT characters (v0.22); YAMLError otherwise."""
    if len(text) > MAX_FRONT:
        raise yaml.YAMLError("frontmatter is larger than %d bytes" % MAX_FRONT)
    loader = _NoAliases(text)
    try:
        return loader.get_single_data()
    finally:
        loader.dispose()


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
    """The frontmatter dict, or None when there is none or it doesn't parse (v0.22: or uses YAML aliases, or is bigger
    than MAX_FRONT)."""
    m = FRONT_RE.match(text)
    if not m:
        return None
    try:
        fm = load_yaml(m.group(1))
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
