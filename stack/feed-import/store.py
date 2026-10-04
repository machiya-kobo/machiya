"""feed-import's state: one sqlite file under FEED_IMPORT_DATA. It is the cursor: every entry a reader has shown us,
per reader and id, and what became of it. Losing it is safe (Hister is asked before anything is added, so nothing is
added twice), only slower: the next run is a backfill.

entries.status:
- pending   the original couldn't be fetched yet (a timeout, 429, 5xx): the entry itself is kept (`entry`, JSON) and
            tried again from here on the next runs, MAX_TRIES runs in all, however deep it is in the reader's list
- added     sent to Hister (content: original or copy)
- known     Hister already had the URL: left alone (a starred one may have been labelled)
- skipped   not sent: no http(s) URL, no content at all, or Hister's skip rules (406)
- rejected  Hister refused it (422 sensitive content, another 4xx): final
entries.starred is 1 once the starred stream has been handled for it (labelled, or found already labelled).
"""
import os
import sqlite3
import time

SCHEMA = """
CREATE TABLE IF NOT EXISTS entries (
  reader TEXT, id TEXT, status TEXT, tries INTEGER DEFAULT 0, url TEXT, label TEXT, content TEXT,
  starred INTEGER DEFAULT 0, first_seen INTEGER, updated INTEGER, error TEXT, stream TEXT, entry TEXT,
  PRIMARY KEY (reader, id));
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT);
"""
FIELDS = ("status", "tries", "url", "label", "content", "starred", "error", "stream", "entry")


class Store:
    def __init__(self, path):
        if path != ":memory:":
            os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        self.db = sqlite3.connect(path, isolation_level=None)
        self.db.row_factory = sqlite3.Row
        self.db.executescript(SCHEMA)

    def get(self, reader, id):
        row = self.db.execute("SELECT * FROM entries WHERE reader=? AND id=?", (reader, id)).fetchone()
        return dict(row) if row else None

    def put(self, reader, id, **kw):
        bad = set(kw) - set(FIELDS)
        if bad:
            raise ValueError("unknown fields: %s" % sorted(bad))
        now = int(time.time())
        if self.get(reader, id) is None:
            self.db.execute("INSERT INTO entries (reader, id, first_seen, updated, status) VALUES (?,?,?,?,?)",
                            (reader, id, now, now, "pending"))
        if kw:
            sets = ", ".join("%s=?" % k for k in kw)
            self.db.execute("UPDATE entries SET %s, updated=? WHERE reader=? AND id=?" % sets,
                            list(kw.values()) + [now, reader, id])

    def pending(self, reader):
        return [dict(r) for r in self.db.execute(
            "SELECT * FROM entries WHERE reader=? AND status='pending' AND entry IS NOT NULL ORDER BY first_seen", (reader,))]

    def count(self, reader):
        return self.db.execute("SELECT count(*) FROM entries WHERE reader=?", (reader,)).fetchone()[0]

    def counts(self):
        out = {}
        for reader, status, n in self.db.execute("SELECT reader, status, count(*) FROM entries GROUP BY 1, 2"):
            out.setdefault(reader, {})[status] = n
        return out

    def meta(self, key, value=None):
        if value is None:
            row = self.db.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
            return row[0] if row else None
        self.db.execute("INSERT OR REPLACE INTO meta VALUES (?,?)", (key, str(value)))
