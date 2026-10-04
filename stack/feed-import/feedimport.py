"""feed-import: what its owner reads in a feed reader lands in Hister (Machiya, stack/feed-import).

Every FEED_IMPORT_INTERVAL seconds, for each reader (NewsBlur, Miniflux, FreshRSS, Feedbin; readers.Reader):
  1. starred entries first, then read entries, newest first, until a page holds nothing new (the whole history on the
     first run: the backfill);
  2. an entry's URL that Hister already has is left alone (never re-indexed); a starred one there without a label
     gets the starred label (POST /api/label), nothing else changes;
  3. otherwise the ORIGINAL article is fetched once, as a browser would open it (stack/smallweb's checked fetcher:
     http(s) only, never a private address, at most 5 redirects, 5 MB, 30 s), and sent to Hister as HTML;
  4. when that fails for good (4xx, not HTML, blocked, too big; or a timeout/429/5xx on FEED_IMPORT_MAX_TRIES runs),
     or the page is much shorter than the reader's copy (a paywall's teaser), the READER'S COPY of the story is sent
     instead, under the article's URL, marked `<reader>_content: copy`.
A read entry is a visited page: no label, `added` = when it was read (or the story's date in a backfill). A starred
entry is a saved page: the first of its reader tags that names a Hister topic label, else FEED_IMPORT_STARRED_LABEL
(default `starred`), Hister's skip rules ignored (a deliberate save), `added` = the star time (when first seen if the
reader keeps none; the story's date in a backfill).

  python3 feedimport.py               the service: a run every FEED_IMPORT_INTERVAL seconds, status in status.json
  python3 feedimport.py --once        one run, then exit (non-zero if it failed)
  python3 feedimport.py --import-legacy state.json
                                      take over server's newsblur-import.py state (once, before the first run)
  python3 feedimport.py --dry-run [--limit N] [--no-fetch] [--stream read|starred]
                                      print what would be sent, at most N entries PER STREAM per reader (default 10:
                                      up to 10 starred + 10 read); writes nothing anywhere (no state, no Hister writes;
                                      Hister is only asked which URLs it has, and only if FEED_IMPORT_HISTER_URL is set)
"""
import argparse
import html as htmlmod
import json
import os
import re
import sys
import time
import traceback
from dataclasses import asdict
from datetime import datetime

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
if not os.path.exists(os.path.join(HERE, "web.py")):          # a checkout: smallweb's fetcher sits beside us
    sys.path.insert(1, os.path.join(os.path.dirname(HERE), "smallweb"))

import hister as histermod                                     # noqa: E402
import smolnet                                                 # noqa: E402
import web                                                     # noqa: E402
from feedbin import Feedbin                                    # noqa: E402
from freshrss import FreshRSS                                  # noqa: E402
from miniflux import Miniflux                                  # noqa: E402
from newsblur import NewsBlur                                  # noqa: E402
from readers import Entry, ReaderError, secret_file            # noqa: E402
from store import Store                                        # noqa: E402

VERSION = "0.1.2"
USER_AGENT = "Mozilla/5.0 (compatible; machiya-feed-import/%s; opens what its owner read in a feed reader)" % VERSION
DAYS = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]
WINDOW_RE = re.compile(r"^(%s)[a-z]*\s+([01]?\d|2[0-3]):([0-5]\d)\s*-\s*([01]?\d|2[0-3]):([0-5]\d)$" % "|".join(DAYS), re.I)
TRANSIENT_HTTP = {408, 425, 429, 500, 502, 503, 504, 520, 521, 522, 523, 524}
MIN_COPY = 500          # characters: a reader's copy shorter than this never replaces a fetched original


def pause_window(value):
    """`Sun 02:20-02:50` -> (weekday, (h, m), (h, m)); empty -> None. Within one day, in FEED_IMPORT_TZ."""
    value = (value or "").strip()
    if not value:
        return None
    m = WINDOW_RE.match(value)
    if not m:
        raise SystemExit("feed-import: FEED_IMPORT_PAUSE is like 'Sun 02:20-02:50', not %r" % value)
    start, end = (int(m.group(2)), int(m.group(3))), (int(m.group(4)), int(m.group(5)))
    if not start < end:
        raise SystemExit("feed-import: FEED_IMPORT_PAUSE must end after it starts (%r)" % value)
    return DAYS.index(m.group(1).lower()), start, end


def in_window(window, tz, now=None):
    if window is None:
        return False
    from zoneinfo import ZoneInfo
    day, start, end = window
    t = datetime.fromtimestamp(now or time.time(), ZoneInfo(tz))
    return t.weekday() == day and start <= (t.hour, t.minute) < end


# -- fetching the original --------------------------------------------------------------------------------------------

class RunFailed(Exception):
    """A run whose every entry failed: reported like a reader or Hister failure."""


class Transient(Exception):
    """Worth another try on a later run (a timeout, 429, 5xx)."""


class Permanent(Exception):
    """Won't get better: use the reader's copy."""


class Page:
    def __init__(self, url, title, text, html):
        self.url, self.title, self.text, self.html = url, title, text, html


def socks_addr(value):
    """socks5h://host:port -> "host:port" ("" = direct), as SMALLWEB_SOCKS."""
    value = (value or "").strip()
    if not value:
        return ""
    from urllib.parse import urlsplit
    u = urlsplit(value)
    if u.scheme not in ("socks5h", "socks5") or not u.hostname or not u.port:
        raise SystemExit("feed-import: FEED_IMPORT_SOCKS must look like socks5h://host:port")
    return "%s:%d" % (u.hostname, u.port)


class Fetcher:
    """The original article, fetched like a single page visit through smallweb's checked fetcher (web.py)."""

    def __init__(self, allow="", socks="", user_agent=USER_AGENT):
        self.allow, self.socks, self.ua = web.parse_allow(allow), socks_addr(socks) or None, user_agent
        self.polite = smolnet.Polite(default=web.GAP)

    def __call__(self, url):
        try:
            checked = web.check(url, self.allow)
            r = web.fetch(checked, self.allow, self.socks, self.polite, self.ua)
        except (web.Refused, web.Blocked) as e:
            raise Permanent("refused: %s" % e)
        except web.WebError as e:
            m = re.match(r"HTTP (\d+)", str(e))
            if m and int(m.group(1)) not in TRANSIENT_HTTP:
                raise Permanent(str(e))
            if not m and ("not saved" in str(e) or "larger than" in str(e) or "redirect" in str(e)):
                raise Permanent(str(e))
            raise Transient(str(e))
        title, text, page = web.read_page(r.body, r.mime, r.charset, r.url)
        return Page(r.url, title, text, page)


def plain(fragment):
    """The visible text of a reader's HTML copy."""
    return web.read_page((fragment or "").encode("utf-8"), "text/html", "utf-8", "about:blank")[1]


def copy_page(title, fragment, url):
    """The reader's copy as a page of its own (Hister extracts the text from the HTML, as it does a visited page)."""
    t = htmlmod.escape(title or url)
    return ("<!DOCTYPE html><html><head><meta charset=\"utf-8\"><title>%s</title>"
            "<link rel=\"canonical\" href=\"%s\"></head><body><article><h1>%s</h1>%s</article></body></html>"
            % (t, htmlmod.escape(url, quote=True), t, fragment or ""))


# -- the importer -------------------------------------------------------------------------------------------------------

class Importer:
    def __init__(self, readers, store, hister=None, fetch=None, *, dry_run=False, starred_label="", tag_labels=True,
                 max_tries=3, copy_ratio=1.5, out=print, clock=time.time):
        self.readers, self.store, self.hister, self.fetch = readers, store, hister, fetch
        self.dry_run, self.starred_label, self.tag_labels = dry_run, starred_label, tag_labels
        self.max_tries, self.copy_ratio, self.out, self.clock = max_tries, copy_ratio, out, clock
        self._labels = None
        self.stats, self.errors = {}, []

    # -- helpers ------------------------------------------------------------------------------------------------------

    def log(self, msg):
        self.out("%s %s" % (time.strftime("%Y-%m-%dT%H:%M:%S"), msg))

    def note(self, reader, key):
        self.stats.setdefault(reader, {}).setdefault(key, 0)
        self.stats[reader][key] += 1

    def put(self, reader, id, **kw):
        if not self.dry_run:
            self.store.put(reader, id, **kw)

    def lookup(self, url):
        return self.hister.document(url) if self.hister else None

    def label_for(self, reader, entry):
        if self.tag_labels and entry.tags and self.hister:
            if self._labels is None:
                self._labels = {x.lower(): x for x in self.hister.labels()}
            for tag in entry.tags:
                if tag.strip().lower() in self._labels:
                    return self._labels[tag.strip().lower()]
        return self.starred_label or "starred"

    def label_existing(self, reader, entry, url, doc):
        """A starred entry whose page Hister already has: label it if it has no label; never touch anything else."""
        if doc is None or doc.get("label"):
            return doc.get("label") if doc else ""
        label = self.label_for(reader, entry)
        if self.dry_run:
            self.log("  [dry-run] would label %s: %s" % (url, label))
            return label
        status = self.hister.set_label(url, label)
        if status >= 300:
            self.log("  label refused (%d): %s" % (status, url))
            return ""
        return label

    # -- one entry ------------------------------------------------------------------------------------------------------

    def handle(self, reader, e, stream, backfill):
        r = reader.name
        starred = stream == "starred"
        rec = self.store.get(r, e.id)
        final = ("added", "known", "rejected", "failed") if starred else ("added", "known", "rejected", "skipped",
                                                                           "failed")
        if rec and rec["status"] in final:
            if starred and not rec["starred"]:
                label = rec["label"] or ""
                if rec["status"] in ("added", "known") and rec["url"]:
                    label = self.label_existing(reader, e, rec["url"], self.lookup(rec["url"])) or label
                    self.note(r, "starred later")
                self.put(r, e.id, starred=1, label=label)
            return
        tries = (rec["tries"] if rec else 0) + 1
        first = e.published if backfill and e.published else int(self.clock())   # fixed when first seen: a retry
        if starred and e.starred_at is None:                                       # keeps it
            e.starred_at = first
        if not starred and e.read_at is None:
            e.read_at = first
        url = histermod.norm(e.url)
        if not url.lower().startswith(("http://", "https://")):
            self.put(r, e.id, status="skipped", url=e.url, error="no http(s) URL", starred=int(starred))
            self.note(r, "skipped")
            return

        doc = self.lookup(url)
        if doc is not None:
            self.known(reader, e, url, doc, starred)
            return

        page, why = None, ""
        if self.fetch:
            try:
                page = self.fetch(e.url)
            except Transient as x:
                if tries < self.max_tries:
                    self.put(r, e.id, status="pending", tries=tries, url=url, error=str(x)[:300], starred=int(starred),
                             stream=stream, entry=json.dumps(asdict(e)))
                    self.log("  retry later (%d/%d) %s: %s" % (tries, self.max_tries, url, x))
                    self.note(r, "pending")
                    return
                why = "gave up after %d tries: %s" % (tries, x)
            except Permanent as x:
                why = str(x)
        else:
            why = "not fetched (--no-fetch)"

        doc_url, content = url, "copy"
        if page is not None:
            doc_url = histermod.norm(page.url)
            if doc_url != url:
                doc = self.lookup(doc_url)
                if doc is not None:
                    self.known(reader, e, doc_url, doc, starred)
                    return
            copy_len, orig_len = len(plain(e.html)), len(page.text)
            if copy_len >= MIN_COPY and copy_len > self.copy_ratio * orig_len:
                why = "the original's text (%d characters) is much shorter than the reader's copy (%d)" % (orig_len, copy_len)
            else:
                content = "original"
        if content == "copy" and not plain(e.html).strip():
            self.put(r, e.id, status="skipped", tries=tries, url=url, error=("no content: " + why)[:300],
                     starred=int(starred))
            self.log("  skipped %s: nothing to store (%s)" % (url, why))
            self.note(r, "skipped")
            return

        added = e.starred_at if starred and e.starred_at else (e.read_at or int(self.clock()))
        meta = {"source": r, "client": "feed-import", "via": r}
        meta.update(e.meta)
        meta["%s_stream" % r] = stream
        meta["%s_content" % r] = content
        if content == "copy":
            meta["%s_copy_reason" % r] = why[:300]
        if starred:
            meta["%s_starred" % r] = True
            meta["ignore_skip_rules"] = True
        if doc_url != e.url:
            meta["%s_permalink" % r] = e.url
        label = self.label_for(reader, e) if starred else ""
        title = (page.title if page is not None and content == "original" else "") or e.title or doc_url
        body = page.html if content == "original" else copy_page(e.title, e.html, doc_url)
        doc = {"url": doc_url, "title": title, "html": body, "label": label, "added": int(added), "metadata": meta}

        if self.dry_run:
            self.out(json.dumps({"would_add": {k: v for k, v in doc.items() if k != "html"},
                                 "html_chars": len(body), **({"why_copy": why} if content == "copy" else {})},
                                ensure_ascii=False))
            self.note(r, "would add")
            return
        status, reply = self.hister.add(doc)
        if status in (200, 201):
            self.put(r, e.id, status="added", tries=tries, url=doc_url, label=label, content=content,
                     starred=int(starred), error=None, entry=None)
            self.log("  added (%s%s) %s" % (content, ", label " + label if label else "", doc_url))
            self.note(r, "added " + content)
        elif status == 406:
            self.put(r, e.id, status="skipped", tries=tries, url=doc_url, error="Hister skip rule (406)",
                     starred=int(starred))
            self.note(r, "skipped")
        else:
            self.put(r, e.id, status="rejected", tries=tries, url=doc_url, error=("HTTP %d %s" % (status, reply))[:300],
                     starred=int(starred))
            self.log("  rejected by Hister (%d): %s" % (status, doc_url))
            self.note(r, "rejected")

    def known(self, reader, e, url, doc, starred):
        label = self.label_existing(reader, e, url, doc) if starred else (doc.get("label") or "")
        self.put(reader.name, e.id, status="known", url=url, label=label or "", starred=int(starred), error=None,
                 entry=None)
        self.note(reader.name, "known")
        if self.dry_run:
            self.out(json.dumps({"already_in_hister": url, "label": label or "", "stream": "starred" if starred else "read"}))

    def handle_safely(self, reader, e, stream, backfill):
        """handle(), with anything it raises besides HisterDown and ReaderError (which stop the run) kept to this entry
        (0.1.2, the 2026-10 sweep, MACH-F-3): the error is recorded, the entry tried again on the next runs, max_tries
        runs in all, then `failed` for good. Before, one malformed permalink or odd charset stopped every run."""
        try:
            self.handle(reader, e, stream, backfill)
        except (histermod.HisterDown, ReaderError):
            raise
        except Exception as x:                         # one bad entry never stops the import
            r = reader.name
            error = ("%s: %s" % (type(x).__name__, x))[:300]
            self.note(r, "errors")
            self.errors.append(error)
            rec = self.store.get(r, e.id)
            tries = (rec["tries"] if rec else 0) + 1
            try:
                entry = json.dumps(asdict(e))
            except (TypeError, ValueError):
                entry, tries = None, max(tries, self.max_tries)
            final = tries >= self.max_tries
            self.log("  %s %s: %s" % ("failed for good" if final else "failed (%d/%d), retry later" % (tries, self.max_tries),
                                      (e.url or e.id)[:200], error))
            if not self.dry_run:
                self.store.put(r, e.id, status="failed" if final else "pending", tries=tries, url=(e.url or "")[:2000],
                               error=error, starred=int(stream == "starred"), stream=stream,
                               entry=None if final else entry)

    # -- a run ----------------------------------------------------------------------------------------------------------

    def run(self, streams=("starred", "read"), limit=None):
        """One pass over every reader. Raises HisterDown or ReaderError (nothing half-recorded: the state is written
        per entry)."""
        self.stats, self.errors = {}, []
        for reader in self.readers:
            r = reader.name
            backfill = self.store.count(r) == 0 or self.store.meta("backfill_done:" + r) is None
            for rec in self.store.pending(r):            # retries come from the state, not from the reader's paging
                if rec["stream"] in streams:
                    self.handle_safely(reader, Entry(**json.loads(rec["entry"])), rec["stream"], backfill)
            for stream in streams:
                n = 0
                if stream == "starred":
                    known = lambda id: bool((self.store.get(r, id) or {}).get("starred"))
                    it = reader.starred(known)
                else:
                    known = lambda id: self.store.get(r, id) is not None
                    it = reader.read(known, backfill=backfill)
                for e in it:
                    if limit is not None and n >= limit:
                        break
                    n += 1
                    self.handle_safely(reader, e, stream, backfill)
            if not self.dry_run and limit is None:
                self.store.meta("backfill_done:" + r, int(self.clock()))
        return self.stats


def import_legacy(store, path):
    """Take over the state of server's hister/newsblur-import.py (starred saves only, 2026-09-28): a save it sent
    (`done`) or Hister refused (`rejected`) is recorded as handled, so it is never sent again. One it gave up on
    (`failed`, e.g. a site that blocks the server) or still had `pending` is left out: this importer tries it again
    and can store NewsBlur's copy instead. Returns {status: count} of what was recorded."""
    with open(path) as f:
        legacy = json.load(f)
    done = {}
    for h, st in (legacy.get("stories") or {}).items():
        status = {"done": "known", "rejected": "rejected"}.get(st.get("status"))
        if not status or store.get("newsblur", h):
            continue
        store.put("newsblur", h, status=status, url=st.get("url") or st.get("permalink") or "",
                  label=st.get("label") or "", starred=1, error="from newsblur-import.py (%s)" % st.get("status"))
        done[status] = done.get(status, 0) + 1
    return done


# -- the service ------------------------------------------------------------------------------------------------------

def build(env, dry_run=False, fetch=True):
    readers = []
    names = [x.strip() for x in env.get("FEED_IMPORT_READERS", "newsblur").split(",") if x.strip()]
    for name in names:
        if name == "newsblur":
            if not env.get("FEED_IMPORT_NEWSBLUR_URL"):
                raise SystemExit("feed-import: FEED_IMPORT_NEWSBLUR_URL is required for newsblur: the server the token "
                                 "belongs to (https://newsblur.com, or a self-hosted NewsBlur's address)")
            readers.append(NewsBlur(env["FEED_IMPORT_NEWSBLUR_URL"],
                                    secret_file(env.get("FEED_IMPORT_NEWSBLUR_TOKEN_FILE"), "FEED_IMPORT_NEWSBLUR_TOKEN_FILE")))
        elif name == "miniflux":
            if not env.get("FEED_IMPORT_MINIFLUX_URL"):
                raise SystemExit("feed-import: FEED_IMPORT_MINIFLUX_URL is required for miniflux")
            readers.append(Miniflux(env["FEED_IMPORT_MINIFLUX_URL"],
                                    secret_file(env.get("FEED_IMPORT_MINIFLUX_TOKEN_FILE"), "FEED_IMPORT_MINIFLUX_TOKEN_FILE")))
        elif name == "freshrss":
            if not env.get("FEED_IMPORT_FRESHRSS_URL") or not env.get("FEED_IMPORT_FRESHRSS_USER"):
                raise SystemExit("feed-import: FEED_IMPORT_FRESHRSS_URL and FEED_IMPORT_FRESHRSS_USER are required for freshrss")
            readers.append(FreshRSS(env["FEED_IMPORT_FRESHRSS_URL"], env["FEED_IMPORT_FRESHRSS_USER"],
                                    secret_file(env.get("FEED_IMPORT_FRESHRSS_PASSWORD_FILE"),
                                                "FEED_IMPORT_FRESHRSS_PASSWORD_FILE")))
        elif name == "feedbin":
            if not env.get("FEED_IMPORT_FEEDBIN_USER"):
                raise SystemExit("feed-import: FEED_IMPORT_FEEDBIN_USER is required for feedbin")
            readers.append(Feedbin(env.get("FEED_IMPORT_FEEDBIN_URL", "https://api.feedbin.com"),
                                   env["FEED_IMPORT_FEEDBIN_USER"],
                                   secret_file(env.get("FEED_IMPORT_FEEDBIN_PASSWORD_FILE"),
                                               "FEED_IMPORT_FEEDBIN_PASSWORD_FILE"),
                                   lookback_days=int(env.get("FEED_IMPORT_FEEDBIN_LOOKBACK", "14"))))
        else:
            raise SystemExit("feed-import: unknown reader %r (known: newsblur, miniflux, freshrss, feedbin)" % name)
    if len(set(names)) != len(names):
        raise SystemExit("feed-import: a reader is named twice in FEED_IMPORT_READERS")
    hister_url = env.get("FEED_IMPORT_HISTER_URL", "")
    if not hister_url and not dry_run:
        raise SystemExit("feed-import: FEED_IMPORT_HISTER_URL is required (unset is allowed only with --dry-run)")
    token = histermod.token_file(env.get("FEED_IMPORT_HISTER_TOKEN_FILE"))     # checked at start even for a dry run
    hister = histermod.Hister(hister_url, token) if hister_url else None
    data = env.get("FEED_IMPORT_DATA", "/data")
    store = Store(":memory:" if dry_run else os.path.join(data, "feed-import.sqlite3"))
    fetcher = Fetcher(env.get("FEED_IMPORT_FETCH_ALLOW", ""), env.get("FEED_IMPORT_SOCKS", "")) if fetch else None
    return Importer(readers, store, hister, fetcher, dry_run=dry_run,
                    starred_label=env.get("FEED_IMPORT_STARRED_LABEL", ""),
                    tag_labels=env.get("FEED_IMPORT_TAG_LABELS", "1") not in ("0", "false", "no"),
                    max_tries=max(1, int(env.get("FEED_IMPORT_MAX_TRIES", "3"))),
                    copy_ratio=float(env.get("FEED_IMPORT_COPY_RATIO", "1.5"))), data


def write_status(data, **kw):
    path = os.path.join(data, "status.json")
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(kw, f, indent=1, sort_keys=True)
    os.replace(tmp, path)


def serve(env, once=False):
    imp, data = build(env)
    interval = max(60, int(env.get("FEED_IMPORT_INTERVAL", "600")))
    window, tz = pause_window(env.get("FEED_IMPORT_PAUSE")), env.get("FEED_IMPORT_TZ", "UTC")
    imp.log("feed-import %s: readers %s, Hister %s, every %d s%s, egress %s" % (
        VERSION, ",".join(r.name for r in imp.readers), env.get("FEED_IMPORT_HISTER_URL"), interval,
        (", paused " + env.get("FEED_IMPORT_PAUSE")) if window else "", env.get("FEED_IMPORT_SOCKS") or "direct"))
    last_ok, fails, carried = None, 0, None
    try:                                     # a run that never finished (a crash, a kill) isn't forgotten on restart
        with open(os.path.join(data, "status.json")) as f:
            before = json.load(f)
        if before.get("running"):
            fails = int(before.get("failures_in_a_row") or 0) + 1
            last_ok = before.get("last_success")
            carried = "the last run didn't finish (the service stopped or crashed during it)"
            imp.log("feed-import: " + carried)
    except (OSError, ValueError, TypeError, AttributeError):
        pass

    def healthy():
        """A probe alerts on ok=false (broken), never on counts: false after a failed run, or when no run has
        succeeded for three intervals. Before the first run ends (a long backfill) it is true unless one failed."""
        if last_ok is None:
            return fails == 0
        return fails == 0 or time.time() - last_ok < 3 * interval + 300

    while True:
        started = int(time.time())
        error = None
        write_status(data, version=VERSION, ok=healthy(), running=True, started=started, last_success=last_ok,
                     failures_in_a_row=fails, error=carried, counts=imp.store.counts())
        carried = None
        entry_errors = 0
        if in_window(window, tz):
            imp.log("in the pause window: no run")
        else:
            try:
                stats = imp.run()
                entry_errors = len(imp.errors)
                done = sum(n for r in stats.values() for k, n in r.items() if k != "errors")
                if entry_errors and not done:          # every entry failed: something is broken, not one story
                    raise RunFailed("%d entries failed, none imported; the last: %s" % (entry_errors, imp.errors[-1]))
                last_ok, fails = int(time.time()), 0
                if any(stats.values()):
                    imp.log("run: %s" % json.dumps(stats, sort_keys=True))
            except (histermod.HisterDown, ReaderError, RunFailed) as e:
                fails += 1
                error = str(e)
                imp.log("run failed (%d in a row): %s" % (fails, e))
            except Exception as e:                     # a bug: a failed run, visible in the status; the loop goes on
                fails += 1
                error = ("crashed: %s: %s" % (type(e).__name__, e))[:500]
                imp.log("run %s (%d in a row)" % (error, fails))
                traceback.print_exc()
        write_status(data, version=VERSION, ok=healthy(), running=False, started=started, last_success=last_ok,
                     failures_in_a_row=fails, error=error, entry_errors=entry_errors, counts=imp.store.counts())
        if once:
            return 0 if error is None else 1
        time.sleep(interval)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--once", action="store_true", help="one run, then exit")
    ap.add_argument("--dry-run", action="store_true", help="print what would be sent; write nothing")
    ap.add_argument("--limit", type=int, default=None, help="dry run: at most N entries per stream per reader (default 10, so up to 10 starred + 10 read)")
    ap.add_argument("--no-fetch", action="store_true", help="dry run: don't fetch the originals")
    ap.add_argument("--stream", choices=("read", "starred"), help="dry run: only this stream")
    ap.add_argument("--import-legacy", metavar="STATE_JSON", help="take over newsblur-import.py's state.json, then exit")
    args = ap.parse_args(argv)
    env = dict(os.environ)
    if args.import_legacy:
        store = Store(os.path.join(env.get("FEED_IMPORT_DATA", "/data"), "feed-import.sqlite3"))
        print("imported: %s" % json.dumps(import_legacy(store, args.import_legacy), sort_keys=True))
        return 0
    if args.dry_run:
        imp, _ = build(env, dry_run=True, fetch=not args.no_fetch)
        streams = (args.stream,) if args.stream else ("starred", "read")
        try:
            stats = imp.run(streams=streams, limit=args.limit if args.limit is not None else 10)
        except (histermod.HisterDown, ReaderError) as e:
            print("dry run failed: %s" % e, file=sys.stderr)
            return 1
        print("dry run: %s (nothing was written)" % json.dumps(stats, sort_keys=True))
        return 0
    return serve(env, once=args.once)


if __name__ == "__main__":
    sys.exit(main())
