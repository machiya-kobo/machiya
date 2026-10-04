"""The settings that follow a person (docs/contracts/prefs.md): the schema, its validation, and the store.

Three kinds of setting (docs/ui.md "Settings"):
  - **Shared** (SHARED): one value for every Machiya app, kept in the account (hister-login's store; a standalone
    room's own store): `theme`, `palette`, `text_size`, `apps_hidden`, and Shiori's `pills`.
  - **Per-app** (`<app>.<key>`, APPS): one app's own settings, also kept in the account; the app that reads a key
    validates its value, the store only checks its shape.
  - **This Device**: never sent anywhere (the device's own text size, offline copies, addresses, secrets).

`validate(changes)` checks a PUT's {"key": value or None} and returns it normalised (`auto` -> `system`, Apps in the
house's order); `Store` keeps {key: (value, updated)} per principal with a revision number, for the helper and for a
standalone room alike. `python3 -m vaultkit.prefs` prints the schema as JSON (docs/contracts/prefs.schema.json, which
Shiori's tests read). Standard library only.
"""
import json
import os
import re
import sqlite3
import threading
import time
from contextlib import closing

from . import palettes

VERSION = 1
THEMES = ("system", "day", "night")                              # Appearance; `auto` is read as `system`
TEXT_SIZES = ("xsmall", "small", "standard", "large", "xlarge")   # the house's five steps
APPS = ("shiori", "konbini", "niwa", "kura", "hister", "searxng", "machiya")   # the switcher's rows, in its order
SHARED = {
    "theme": {"label": "Appearance", "values": list(THEMES), "default": "system"},
    "palette": {"label": "Theme", "values": list(palettes.PALETTES), "default": palettes.DEFAULT},
    "text_size": {"label": "Text Size", "values": list(TEXT_SIZES), "default": "standard"},
    "apps_hidden": {"label": "Apps", "list_of": list(APPS), "default": ""},
    # Shiori's pill row (order and hidden), the same on every Shiori surface (the owner, 2026-10-05); the rooms have
    # no pills. JSON {"order": [pill id, ...], "hidden": [pill id, ...]}: the store checks the shape, Shiori the ids
    "pills": {"label": "Pills", "json": {"order": "[pill id]", "hidden": "[pill id]"}, "default": ""},
}
PILL_RE = re.compile(r"[a-z0-9_-]{1,32}\Z")
RESERVED = ("language", "time_zone")                             # named now, refused until something uses them
APP_NAMES = ("kura", "niwa", "konbini", "shiori", "machiya")     # the per-app namespaces (machiya = landing)
APP_KEY_RE = re.compile(r"(%s)\.[a-z0-9_]{1,48}\Z" % "|".join(APP_NAMES))
MAX_APP_VALUE = 1024                                             # bytes of UTF-8, per-app values
MAX_KEYS = 100                                                   # per principal
MAX_BODY = 512 * 1024                                            # a PUT's body
# the browser's names for the shared keys (machiya.js's localStorage keys and the machiya_<key> cookies)
LOCAL = {"theme": "theme", "palette": "palette", "text_size": "textSize"}


class PrefsError(ValueError):
    """A PUT the schema or the limits refuse; the message is safe to show."""


def is_app_key(key):
    return isinstance(key, str) and bool(APP_KEY_RE.match(key))


def known(key):
    """A key the store accepts: one of SHARED, or <app>.<key>."""
    return key in SHARED or is_app_key(key)


def _apps(value):
    """'searxng, kura,kura' -> 'kura,searxng' (the house's order, no repeats); PrefsError for an unknown app."""
    names = [x.strip() for x in value.split(",") if x.strip()]
    bad = [n for n in names if n not in APPS]
    if bad:
        raise PrefsError("apps_hidden: unknown app %r (one of %s)" % (bad[0], ", ".join(APPS)))
    return ",".join(a for a in APPS if a in names)


def _pills(value):
    """'{"order": [...], "hidden": [...]}' -> the same, compact; PrefsError for any other shape."""
    try:
        data = json.loads(value)
    except (ValueError, RecursionError):
        raise PrefsError("pills: JSON {\"order\": [...], \"hidden\": [...]}")
    if not isinstance(data, dict) or set(data) - {"order", "hidden"}:
        raise PrefsError("pills: only order and hidden")
    for k in ("order", "hidden"):
        ids = data.get(k, [])
        if not isinstance(ids, list) or len(ids) > 32 or not all(isinstance(i, str) and PILL_RE.match(i) for i in ids):
            raise PrefsError("pills: %s is a list of up to 32 pill ids" % k)
    return json.dumps({k: data[k] for k in ("order", "hidden") if k in data}, separators=(",", ":"))


def check(key, value):
    """One key's value, normalised; PrefsError when the schema refuses it. None (remove) is always fine."""
    if not isinstance(key, str):
        raise PrefsError("a key is a string")
    if key in RESERVED:
        raise PrefsError("%s is reserved: no app uses it yet" % key)
    if not known(key):
        raise PrefsError("%s: not a setting (theme, palette, text_size, apps_hidden or <app>.<key>, <app> one of %s)"
                         % (key[:64], ", ".join(APP_NAMES)))
    if value is None:
        return None
    if not isinstance(value, str):
        raise PrefsError("%s: a value is a string (or null to remove it)" % key)
    if key == "theme":
        value = "system" if value == "auto" else value
    if key == "apps_hidden":
        return _apps(value)
    if key == "pills":
        return _pills(value)
    if key in SHARED:
        if value not in SHARED[key]["values"]:
            raise PrefsError("%s: one of %s" % (key, ", ".join(SHARED[key]["values"])))
        return value
    try:
        size = len(value.encode("utf-8"))
    except UnicodeError:
        raise PrefsError("%s: not valid text" % key)
    if size > MAX_APP_VALUE:
        raise PrefsError("%s: a value is at most %d bytes" % (key, MAX_APP_VALUE))
    if any(ord(c) < 32 or ord(c) == 127 for c in value):
        raise PrefsError("%s: no control characters" % key)
    return value


def validate(changes):
    """A PUT's {"key": value or None} -> the same, normalised. PrefsError for anything the schema refuses."""
    if not isinstance(changes, dict):
        raise PrefsError("prefs must be an object of key: string")
    if len(changes) > MAX_KEYS:
        raise PrefsError("at most %d keys" % MAX_KEYS)
    return {k: check(k, v) for k, v in changes.items()}


def shared_only(prefs):
    """The Shared keys of a prefs dict whose values the schema accepts (for a first render)."""
    out = {}
    for k in SHARED:
        try:
            v = check(k, prefs.get(k)) if isinstance(prefs, dict) else None
        except PrefsError:
            v = None
        if v is not None:
            out[k] = v
    return out


def answer(rev, values, updated):
    """The JSON body every GET and PUT answers (the contract's shape; "prefs" alone is what old clients read)."""
    return {"v": VERSION, "rev": rev, "prefs": values, "updated": updated}


def etag(rev):
    return '"%d"' % int(rev)


def matches(if_none_match, rev):
    """True when an If-None-Match header names this revision (a 304)."""
    if not isinstance(if_none_match, str):
        return False
    tags = [t.strip() for t in if_none_match.split(",")]
    return "*" in tags or etag(rev) in tags or ("W/" + etag(rev)) in tags


def schema():
    """The schema as JSON-able data (docs/contracts/prefs.schema.json)."""
    return {"v": VERSION, "shared": SHARED, "reserved": list(RESERVED), "apps": list(APP_NAMES),
            "app_key": APP_KEY_RE.pattern, "max_app_value": MAX_APP_VALUE, "max_keys": MAX_KEYS,
            "local": LOCAL}


# -- the store -------------------------------------------------------------------------------------------------------

class Store:
    """{principal: {key: (value, updated)}} plus a revision per principal, in one SQLite file (0600):
    prefs(principal, key, value, updated) is the rooms' table of old (signin.Prefs), so their files open as they are;
    prefs_rev(principal, rev) is new. `updated` is the server's clock (seconds) when the value was written; `rev` goes
    up by one with every write that changes something, and never goes back (not even after delete()), so an ETag a
    client holds can't match a later state. The caller decides who the principal is and validates the keys."""

    def __init__(self, path):
        self.path, self.lock = path, threading.Lock()
        if path != ":memory:":
            try:                        # 0600: other local users don't read anyone's preferences
                os.close(os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600))
            except FileExistsError:
                pass                    # already there (or another process just made it): keep it as it is
        self._mem = sqlite3.connect(":memory:", check_same_thread=False, isolation_level=None) \
            if path == ":memory:" else None
        with self.lock, self._conn() as db:
            db.execute("CREATE TABLE IF NOT EXISTS prefs (principal TEXT NOT NULL, key TEXT NOT NULL, "
                       "value TEXT NOT NULL, updated INTEGER NOT NULL, PRIMARY KEY (principal, key))")
            db.execute("CREATE TABLE IF NOT EXISTS prefs_rev (principal TEXT PRIMARY KEY, rev INTEGER NOT NULL)")

    def _conn(self):
        if self._mem is not None:
            return _NoClose(self._mem)
        # autocommit (writes begin their own); a short wait, since the lock below is held meanwhile
        return closing(sqlite3.connect(self.path, timeout=2, isolation_level=None))

    @staticmethod
    def _principal(principal):
        if not isinstance(principal, str) or not principal or len(principal) > 64:
            raise PrefsError("no principal")
        return principal

    @staticmethod
    def _read(db, principal):
        rows = db.execute("SELECT key, value, updated FROM prefs WHERE principal = ? ORDER BY key", (principal,))
        values, updated = {}, {}
        for k, v, u in rows:
            values[k], updated[k] = v, u
        rev = db.execute("SELECT rev FROM prefs_rev WHERE principal = ?", (principal,)).fetchone()
        return (rev[0] if rev else 0), values, updated

    def snapshot(self, principal):
        """-> (rev, {key: value}, {key: updated})."""
        principal = self._principal(principal)
        with self.lock, self._conn() as db:
            return self._read(db, principal)

    def write(self, principal, changes, stamps=None, only_newer=False):
        """Set each key to its string value, or remove it (None); all or nothing. stamps: {key: updated} to keep (an
        import) instead of now; only_newer: skip a key whose stored `updated` is the same or newer (an import run
        twice changes nothing). -> snapshot. PrefsError when the principal would hold more than MAX_KEYS."""
        principal = self._principal(principal)
        if not isinstance(changes, dict):
            raise PrefsError("prefs must be an object of key: string")
        t = int(time.time())
        with self.lock, self._conn() as db:
            db.execute("BEGIN IMMEDIATE")
            try:
                changed = False
                for k, v in changes.items():
                    row = db.execute("SELECT value, updated FROM prefs WHERE principal = ? AND key = ?",
                                     (principal, k)).fetchone()
                    stamp = int((stamps or {}).get(k, t))
                    if only_newer and row is not None and row[1] >= stamp:
                        continue
                    if v is None:
                        if row is not None:
                            db.execute("DELETE FROM prefs WHERE principal = ? AND key = ?", (principal, k))
                            changed = True
                    elif row is None or row[0] != v or stamps:
                        db.execute("INSERT INTO prefs (principal, key, value, updated) VALUES (?, ?, ?, ?) "
                                   "ON CONFLICT (principal, key) DO UPDATE SET value = excluded.value, "
                                   "updated = excluded.updated", (principal, k, v, stamp))
                        changed = True
                    # the same value again: nothing changes, `updated` stays (the newest choice is still the old one)
                count = db.execute("SELECT COUNT(*) FROM prefs WHERE principal = ?", (principal,)).fetchone()[0]
                if count > MAX_KEYS:
                    raise PrefsError("at most %d keys" % MAX_KEYS)
                if changed:
                    db.execute("INSERT INTO prefs_rev (principal, rev) VALUES (?, 1) "
                               "ON CONFLICT (principal) DO UPDATE SET rev = rev + 1", (principal,))
                db.execute("COMMIT")
            except BaseException:
                try:
                    db.execute("ROLLBACK")
                except sqlite3.Error:
                    pass                # the original error matters, not a failed rollback
                raise
            return self._read(db, principal)

    def delete(self, principal):
        """Every key of one principal (an account removed); its revision goes on, so old ETags never match. -> how
        many keys went."""
        principal = self._principal(principal)
        with self.lock, self._conn() as db:
            db.execute("BEGIN IMMEDIATE")
            try:
                n = db.execute("DELETE FROM prefs WHERE principal = ?", (principal,)).rowcount
                db.execute("INSERT INTO prefs_rev (principal, rev) VALUES (?, 1) "
                           "ON CONFLICT (principal) DO UPDATE SET rev = rev + 1", (principal,))
                db.execute("COMMIT")
            except BaseException:
                db.execute("ROLLBACK")
                raise
            return n

    def principals(self):
        with self.lock, self._conn() as db:
            return [r[0] for r in db.execute("SELECT DISTINCT principal FROM prefs ORDER BY principal")]


class _NoClose:
    """`with` for the one shared in-memory connection (tests): nothing to close."""

    def __init__(self, db):
        self.db = db

    def __enter__(self):
        return self.db

    def __exit__(self, *exc):
        return False


def read_rows(path, principals=None):
    """An old store's rows, read-only: [(principal, key, value, updated)] (for an import). sqlite3.Error when it
    isn't one."""
    uri = "file:%s?mode=ro" % os.path.abspath(path)
    with closing(sqlite3.connect(uri, uri=True, timeout=2)) as db:
        rows = db.execute("SELECT principal, key, value, updated FROM prefs ORDER BY principal, key").fetchall()
    return [r for r in rows if principals is None or r[0] in principals]


def merge(sources):
    """The migration rule (docs/contracts/prefs.md): for each key, the value with the newest `updated` across every
    source wins; a key the schema refuses is left out. sources: [(name, [(key, value, updated)])].
    -> ({key: value}, {key: updated}, {key: source name}, [refused "name: key"])."""
    best, refused = {}, []
    for name, rows in sources:
        for key, value, updated in rows:
            try:
                value = check(key, value)
            except PrefsError:
                refused.append("%s: %s" % (name, key))
                continue
            if value is None:
                continue
            if key not in best or updated > best[key][1]:
                best[key] = (value, int(updated), name)
    return ({k: v[0] for k, v in best.items()}, {k: v[1] for k, v in best.items()},
            {k: v[2] for k, v in best.items()}, refused)


if __name__ == "__main__":
    print(json.dumps(schema(), indent=1, sort_keys=True))
