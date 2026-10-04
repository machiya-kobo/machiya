"""Frontmatter line editing: change a note's top-level scalar keys or its tags without touching anything else
(the body, key order, comments, formatting of other keys), so Niwa's garden fields and Konbini's board fields are
written the same way.
"""
import hashlib
import json
import re

import yaml

from .front import FRONT_RE, note_front, tags_of


class EditError(Exception):
    """A note that can't be edited safely (status: an HTTP-ish code for callers that serve it)."""

    def __init__(self, status, message, **extra):
        super().__init__(message)
        self.status, self.message, self.extra = status, message, extra


def yaml_scalar(value):
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return repr(value)
    text = str(value)
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", text):
        return text  # YAML date, like every note's date: field
    if re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9 _./()+-]*", text) and not text.endswith(" "):
        try:
            if yaml.safe_load("k: " + text)["k"] == text:
                return text
        except yaml.YAMLError:
            pass
    return json.dumps(text, ensure_ascii=False)  # JSON strings are valid YAML


def key_extent(lines, key):
    """(start, end) of a top-level key and its indented continuation lines."""
    for i, line in enumerate(lines):
        if re.match(r"^%s:(\s|$)" % re.escape(key), line):
            j = i + 1
            while j < len(lines) and (lines[j].startswith((" ", "\t")) or lines[j].startswith("- ")):
                j += 1
            return i, j
    return None


def edit_front(text, scalars=None, tags=None):
    """Set (or, with None/"", remove) top-level scalar keys and optionally replace the tags block."""
    m = FRONT_RE.match(text)
    if not m:
        raise EditError(422, "note has no frontmatter")
    lines = m.group(1).split("\n")
    for key, value in (scalars or {}).items():
        ext = key_extent(lines, key)
        new = [] if value in (None, "") else ["%s: %s" % (key, yaml_scalar(value))]
        if ext:
            lines[ext[0]:ext[1]] = new
        elif new:
            lines.extend(new)
    if tags is not None:
        ext = key_extent(lines, "tags")
        block = ["tags:"] + ["  - %s" % t for t in tags]
        if ext:
            lines[ext[0]:ext[1]] = block
        else:
            lines[1:1] = block
    inner = "\n".join(lines)
    fm = yaml.safe_load(inner)
    if not isinstance(fm, dict):
        raise EditError(500, "frontmatter edit produced invalid YAML")
    start = text.index(m.group(1))
    return text[:start] + inner + text[start + len(m.group(1)):]


def merge_note(base, ours, theirs):
    """Three-way merge of a note a service edited (ours) with an upstream edit (theirs). Services write
    frontmatter only, so the body is always theirs; a key we changed takes our value, every other key keeps
    upstream's, and tags merge as add/remove sets (status/* follows our move). Returns the merged text, or
    None if it can't be merged safely."""
    fb, fo, ft = note_front(base) or {}, note_front(ours), note_front(theirs)
    if fo is None or ft is None:
        return None
    scalars = {}
    for key in set(fb) | set(fo):
        if key == "tags" or fo.get(key) == fb.get(key) or fo.get(key) == ft.get(key):
            continue
        if isinstance(fo.get(key), (list, dict)) or isinstance(ft.get(key), (list, dict)):
            return None  # services never write lists; don't guess
        scalars[key] = fo.get(key)
    tb, to, tt = tags_of(fb), tags_of(fo), tags_of(ft)
    added = [t for t in to if t not in tb]
    removed = set(tb) - set(to)
    moved = any(t.startswith("status/") for t in added)
    tags = [t for t in tt if t not in removed and not (moved and t.startswith("status/"))]
    tags += [t for t in added if t not in tags]
    try:
        return edit_front(theirs, scalars, tags if tags != tt else None)
    except (EditError, yaml.YAMLError):
        return None


def version_of(text):
    m = FRONT_RE.match(text)
    return hashlib.sha1((m.group(1) if m else "").encode()).hexdigest()[:12]
