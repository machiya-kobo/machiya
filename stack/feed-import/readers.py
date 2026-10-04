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
from dataclasses import dataclass, field


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
