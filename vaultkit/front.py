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


def _shares_a_node(node):
    """Does the node graph reach any node twice? The composer turns an alias (*name) into a second reference to the
    anchored node, so a graph with a shared node is a document with an alias; one without is safe to construct (no
    expansion). Iterative, so a deep document can't exhaust the stack."""
    seen, stack = set(), [node]
    while stack:
        n = stack.pop()
        if id(n) in seen:
            return True
        seen.add(id(n))
        if isinstance(n, yaml.SequenceNode):
            stack.extend(n.value)
        elif isinstance(n, yaml.MappingNode):
            for k, v in n.value:
                stack.extend((k, v))
    return False


class AliasError(yaml.YAMLError):
    """The frontmatter uses a YAML alias (refused since v0.22)."""


def _load_c(text):
    """v0.28: the document through LibYAML (PyYAML's C parser, about five times faster), with the same guard: the
    node graph is composed in C, checked for aliases, and only then constructed."""
    loader = yaml.CSafeLoader(text)
    try:
        node = loader.get_single_node()
        if node is None:
            return None
        if _shares_a_node(node):
            raise AliasError("YAML aliases are not allowed in frontmatter")
        return loader.construct_document(node)
    finally:
        loader.dispose()


def _load_py(text):
    loader = _NoAliases(text)
    try:
        return loader.get_single_data()
    finally:
        loader.dispose()


def load_yaml(text):
    """yaml.safe_load without aliases and at most MAX_FRONT characters (v0.22); YAMLError otherwise. v0.28: through
    LibYAML when PyYAML has it; anything LibYAML refuses (other than an alias) is read by the pure-Python loader as
    before, so a note that loaded before still loads, the same."""
    if len(text) > MAX_FRONT:
        raise yaml.YAMLError("frontmatter is larger than %d bytes" % MAX_FRONT)
    if _C:
        try:
            return _load_c(text)
        except AliasError:
            raise
        except yaml.YAMLError:
            pass
    return _load_py(text)


_C = hasattr(yaml, "CSafeLoader")


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
