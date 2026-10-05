"""code-import's state: one sqlite file under CODE_IMPORT_DATA. Losing it is safe, only slower: the next run is a full
one, and Hister is asked about every URL before anything is added or deleted (a document whose metadata.source is
`code` is recognised as ours again).

repos:  every repo seen, per host and the host's repo id (stable across renames): its name, URL, branch and content
        stamp when last imported, and whether it is included (else why not: fork, archived, excluded, twin).
docs:   every document URL, per repo and key (repo, doc:<path>, issue:<n>, release:<id>), what it was built from
        (`src`, a fingerprint: an unchanged one is skipped without asking anyone) and what became of it:
        - added     sent to Hister: ours (deleted when its source goes away)
        - known     Hister already had the URL as somebody else's page (the owner browsed it): left alone, never
                    overwritten or deleted
        - refused   our secret scan refused it (CODE_IMPORT_SECRETS=refuse), or a file named like a secret
        - rejected  Hister refused it (422 sensitive content, another 4xx); tried again when its source changes
meta:   cursors (issues per scope, the last full run), and per source the last complete reconcile
        (`reconcile:<source>`: {checked, missing, at}).
http:   ETag and body per URL for conditional GETs (GitHub's 304s are free); pruned after a week unused.
"""
import os
import sqlite3
import time

SCHEMA = """
CREATE TABLE IF NOT EXISTS repos (
  host TEXT, id TEXT, full_name TEXT, url TEXT, branch TEXT, stamp TEXT, included INTEGER, reason TEXT,
  updated INTEGER, PRIMARY KEY (host, id));
CREATE TABLE IF NOT EXISTS docs (
  url TEXT PRIMARY KEY, host TEXT, repo_id TEXT, kind TEXT, key TEXT, src TEXT, status TEXT, error TEXT,
  updated INTEGER);
CREATE INDEX IF NOT EXISTS docs_repo ON docs (host, repo_id);
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE IF NOT EXISTS http (url TEXT PRIMARY KEY, etag TEXT, body TEXT, used INTEGER);
"""


class Store:
    def __init__(self, path):
        if path != ":memory:":
            os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        self.db = sqlite3.connect(path, isolation_level=None)
        self.db.row_factory = sqlite3.Row
        self.db.executescript(SCHEMA)

    # -- repos ---------------------------------------------------------------------------------------------------------

    def repo(self, host, id):
        row = self.db.execute("SELECT * FROM repos WHERE host=? AND id=?", (host, id)).fetchone()
        return dict(row) if row else None

    def repos(self, host):
        return [dict(r) for r in self.db.execute("SELECT * FROM repos WHERE host=?", (host,))]

    def put_repo(self, host, id, **kw):
        if self.repo(host, id) is None:
            self.db.execute("INSERT INTO repos (host, id) VALUES (?,?)", (host, id))
        kw["updated"] = int(time.time())
        self.db.execute("UPDATE repos SET %s WHERE host=? AND id=?" % ", ".join("%s=?" % k for k in kw),
                        list(kw.values()) + [host, id])

    def drop_repo(self, host, id):
        self.db.execute("DELETE FROM repos WHERE host=? AND id=?", (host, id))

    # -- docs ----------------------------------------------------------------------------------------------------------

    def doc(self, url):
        row = self.db.execute("SELECT * FROM docs WHERE url=?", (url,)).fetchone()
        return dict(row) if row else None

    def docs(self, host, repo_id, kinds=None):
        rows = [dict(r) for r in self.db.execute("SELECT * FROM docs WHERE host=? AND repo_id=?", (host, repo_id))]
        return [r for r in rows if kinds is None or r["kind"] in kinds]

    def put_doc(self, url, **kw):
        if self.doc(url) is None:
            self.db.execute("INSERT INTO docs (url) VALUES (?)", (url,))
        kw["updated"] = int(time.time())
        self.db.execute("UPDATE docs SET %s WHERE url=?" % ", ".join("%s=?" % k for k in kw), list(kw.values()) + [url])

    def forget(self, url):
        """Forget what a document was built from (the reconcile found it gone from Hister): the next put() of it
        rebuilds and sends it again."""
        self.db.execute("UPDATE docs SET src=NULL WHERE url=?", (url,))

    def drop_doc(self, url):
        self.db.execute("DELETE FROM docs WHERE url=?", (url,))

    def counts(self):
        out = {}
        for host, kind, status, n in self.db.execute("SELECT host, kind, status, count(*) FROM docs GROUP BY 1, 2, 3"):
            out.setdefault(host, {}).setdefault(kind, {})[status] = n
        return out

    # -- meta and the HTTP cache --------------------------------------------------------------------------------------

    def meta(self, key, value=None):
        if value is None:
            row = self.db.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
            return row[0] if row else None
        self.db.execute("INSERT OR REPLACE INTO meta VALUES (?,?)", (key, str(value)))

    def meta_prefix(self, prefix):
        return [(r[0], r[1]) for r in self.db.execute("SELECT key, value FROM meta WHERE key LIKE ? ORDER BY key",
                                                       (prefix.replace("%", "") + "%",))]

    def drop_meta(self, key):
        self.db.execute("DELETE FROM meta WHERE key=?", (key,))

    def http_get(self, url):
        row = self.db.execute("SELECT etag, body FROM http WHERE url=?", (url,)).fetchone()
        if row:
            self.db.execute("UPDATE http SET used=? WHERE url=?", (int(time.time()), url))
        return (row[0], row[1]) if row else None

    def http_put(self, url, etag, body):
        self.db.execute("INSERT OR REPLACE INTO http VALUES (?,?,?,?)", (url, etag, body, int(time.time())))

    def http_prune(self, older_than):
        """Forget cached answers not asked for since `older_than` (an old cursor's URL, a deleted repo's)."""
        self.db.execute("DELETE FROM http WHERE used < ?", (int(older_than),))
