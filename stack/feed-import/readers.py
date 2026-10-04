"""The reader interface: what feed-import needs from a feed reader, and nothing else.

A reader (NewsBlur, later Miniflux, FreshRSS, Feedbin) answers two questions, newest first:
- read(known, backfill): the entries its owner has READ, one by one;
- starred(known): the entries its owner has STARRED (saved).

`known(id)` says whether feed-import has already handled that entry for this stream, so a reader can stop paging once
a whole page is known (it must keep going when `backfill` is true). A reader only ever READS: it never marks
anything read or unread, stars, tags or changes its server's state in any other way.

Each entry carries the reader's own copy of the story (`html`), so feed-import can store that when the original article
can't be fetched. Readers put their own metadata in `meta`, with keys prefixed by the reader's name (`newsblur_*`),
Hister's convention for importers.
"""
import base64
import json
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime


class ReaderError(Exception):
    """The reader couldn't be asked (unreachable, refused the credentials, answered nonsense). Nothing is recorded."""


@dataclass
class Entry:
    id: str                              # the reader's own stable id (NewsBlur: the story hash)
    url: str                             # the article's permalink, as the feed gave it
    title: str = ""
    html: str = ""                       # the reader's copy of the story (the feed's content); may be empty
    published: int = 0                   # unix seconds, the story's own date
    read_at: int | None = None           # when it was read, if the reader knows (NewsBlur doesn't)
    starred_at: int | None = None        # when it was starred, for a starred entry
    feed: str = ""                       # the feed's title
    folder: str = ""                     # the reader's folder (category) of that feed
    tags: list = field(default_factory=list)    # the owner's own tags on the entry (NewsBlur: a saved story's tags)
    meta: dict = field(default_factory=dict)    # reader-prefixed metadata for Hister


class Reader:
    """Base class. Subclasses set `name` and implement read() and starred() as generators of Entry."""
    name = ""

    def read(self, known, backfill=False):
        raise NotImplementedError

    def starred(self, known):
        raise NotImplementedError


class Client:
    """The HTTP side every reader shares: JSON GETs (and a reader's own read-only POSTs), spaced `gap` seconds apart,
    every failure a ReaderError. `missing` lists statuses that mean "nothing here" (returned as None)."""

    def __init__(self, base, headers=None, gap=1.0, timeout=60, name="reader"):
        self.base, self.headers, self.gap, self.timeout, self.name = base.rstrip("/"), dict(headers or {}), gap, timeout, name
        self.last_call = 0.0

    def call(self, path, params=(), data=None, missing=(), raw=False):
        wait = self.last_call + self.gap - time.time()
        if wait > 0:
            time.sleep(wait)
        url = self.base + path + ("?" + urllib.parse.urlencode(list(params)) if params else "")
        headers = {"Accept": "application/json", "User-Agent": "machiya-feed-import (read-only)", **self.headers}
        body = None
        if data is not None:
            body = urllib.parse.urlencode(list(data)).encode()
            headers["Content-Type"] = "application/x-www-form-urlencoded"
        req = urllib.request.Request(url, data=body, method="POST" if body is not None else "GET", headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as r:
                payload = r.read()
        except urllib.error.HTTPError as e:
            if e.code in missing:
                return None
            raise ReaderError("%s %s: HTTP %d" % (self.name, path, e.code))
        except (urllib.error.URLError, OSError) as e:
            raise ReaderError("%s %s: %s" % (self.name, path, e))
        finally:
            self.last_call = time.time()
        if raw:
            return payload.decode("utf-8", "replace")
        try:
            return json.loads(payload)
        except ValueError:
            raise ReaderError("%s %s: not JSON" % (self.name, path))


def basic(user, password):
    return "Basic " + base64.b64encode(("%s:%s" % (user, password)).encode()).decode()


def unix(value):
    """RFC 3339 / ISO 8601 (Miniflux, Feedbin) or a number -> unix seconds; 0 if unreadable."""
    if value is None or value == "":
        return 0
    if isinstance(value, (int, float)) or str(value).isdigit():
        return int(value)
    text = str(value).strip().replace("Z", "+00:00")
    if "." in text:                                   # Python wants at most 6 fractional digits
        head, _, tail = text.partition(".")
        digits = tail[:len(tail) - len(tail.lstrip("0123456789"))]      # the fraction only, not the offset
        text = head + "." + digits[:6] + tail[len(digits):]
    try:
        return int(datetime.fromisoformat(text).timestamp())
    except ValueError:
        return 0


def secret_file(path, what):
    try:
        with open(path) as f:
            value = f.read().strip()
    except (OSError, TypeError):
        value = ""
    if not value:
        raise SystemExit("feed-import: %s names no readable file" % what)
    return value
