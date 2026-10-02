"""smallweb's state, one sqlite file under SMALLWEB_DATA. All of it can be thrown away:
- tofu: the Gemini certificate first seen per host:port (trust on first use);
- cache: search answers (1 h) and fetched pages (10 min), kept stale for when an engine is down;
- queries: when each engine was last asked (the hourly cap);
- saves: what was sent to Hister (url + body hash), so an unchanged page is sent once.
"""
import json
import os
import sqlite3
import threading
import time

SCHEMA = """
CREATE TABLE IF NOT EXISTS tofu (hostport TEXT PRIMARY KEY, sha256 TEXT, not_after INTEGER, first_seen INTEGER,
                                 last_seen INTEGER);
CREATE TABLE IF NOT EXISTS cache (kind TEXT, key TEXT, ts INTEGER, value BLOB, PRIMARY KEY (kind, key));
CREATE TABLE IF NOT EXISTS queries (engine TEXT, ts INTEGER);
CREATE INDEX IF NOT EXISTS queries_engine_ts ON queries (engine, ts);
CREATE TABLE IF NOT EXISTS saves (url TEXT PRIMARY KEY, sha256 TEXT, ts INTEGER, status INTEGER);
"""


class Store:
    def __init__(self, path):
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        self.db = sqlite3.connect(path, check_same_thread=False, isolation_level=None)
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.executescript(SCHEMA)
        self.lock = threading.Lock()

    def q(self, sql, args=()):
        with self.lock:
            return self.db.execute(sql, args).fetchall()

    # -- TOFU ------------------------------------------------------------------------------------------------------

    def tofu_check(self, hostport, sha256, not_after, now=None):
        """"new" (pinned now), "ok", "renewed" (the old certificate had expired: re-pinned) or "mismatch"."""
        now = int(now or time.time())
        row = self.q("SELECT sha256, not_after FROM tofu WHERE hostport=?", (hostport,))
        if not row:
            self.q("INSERT INTO tofu VALUES (?,?,?,?,?)", (hostport, sha256, not_after, now, now))
            return "new"
        old, old_after = row[0]
        if old == sha256:
            self.q("UPDATE tofu SET last_seen=? WHERE hostport=?", (now, hostport))
            return "ok"
        if old_after and old_after < now:
            self.tofu_pin(hostport, sha256, not_after, now)
            return "renewed"
        return "mismatch"

    def tofu_pin(self, hostport, sha256, not_after, now=None):
        now = int(now or time.time())
        self.q("INSERT OR REPLACE INTO tofu VALUES (?,?,?,COALESCE((SELECT first_seen FROM tofu WHERE hostport=?),?),?)",
               (hostport, sha256, not_after, hostport, now, now))

    def tofu_count(self):
        return self.q("SELECT count(*) FROM tofu")[0][0]

    # -- cache -----------------------------------------------------------------------------------------------------

    def get(self, kind, key, ttl):
        """(value, fresh) or (None, False). Stale values are returned too, fresh=False, so a failure can fall back."""
        row = self.q("SELECT ts, value FROM cache WHERE kind=? AND key=?", (kind, key))
        if not row:
            return None, False
        ts, value = row[0]
        return value, time.time() - ts < ttl

    def put(self, kind, key, value):
        self.q("INSERT OR REPLACE INTO cache VALUES (?,?,?,?)", (kind, key, int(time.time()), value))

    def get_json(self, kind, key, ttl):
        value, fresh = self.get(kind, key, ttl)
        return (json.loads(value), fresh) if value is not None else (None, False)

    def put_json(self, kind, key, obj):
        self.put(kind, key, json.dumps(obj))

    def prune(self, older_than=7 * 86400):
        self.q("DELETE FROM cache WHERE ts < ?", (int(time.time()) - older_than,))
        self.q("DELETE FROM queries WHERE ts < ?", (int(time.time()) - 86400,))

    # -- the hourly cap -----------------------------------------------------------------------------------------------

    def queries_last_hour(self, engine):
        return self.q("SELECT count(*) FROM queries WHERE engine=? AND ts > ?", (engine, int(time.time()) - 3600))[0][0]

    def count_query(self, engine):
        self.q("INSERT INTO queries VALUES (?,?)", (engine, int(time.time())))

    # -- Hister saves -------------------------------------------------------------------------------------------------

    def saved_hash(self, url):
        row = self.q("SELECT sha256 FROM saves WHERE url=? AND status=201", (url,))
        return row[0][0] if row else None

    def record_save(self, url, sha256, status):
        self.q("INSERT OR REPLACE INTO saves VALUES (?,?,?,?)", (url, sha256, int(time.time()), status))

    def save_count(self):
        return self.q("SELECT count(*) FROM saves WHERE status=201")[0][0]
