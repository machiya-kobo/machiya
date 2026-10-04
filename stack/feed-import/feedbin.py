"""The Feedbin reader (api.feedbin.com/v2), GET only, with the account's e-mail and password (HTTP Basic; Feedbin
has no API tokens). Read from feedbin/feedbin-api, 2026-10-04.

- read:    GET /v2/entries.json?read=true&since=<ISO 8601>&per_page=100&page=N (newest created first; `since` is the
           entry's creation, not the read) over the last FEED_IMPORT_FEEDBIN_LOOKBACK days (whole history in a
           backfill), plus GET /v2/recently_read_entries.json (ids of posts read for 10 s in Feedbin's web app) for
           older entries read lately
- starred: GET /v2/starred_entries.json (every starred id), then GET /v2/entries.json?ids=…  (at most 100 a call)
- feeds:   GET /v2/subscriptions.json (titles), GET /v2/taggings.json (folders)
Feedbin keeps no read or star time: the time is when an entry was first seen (its date in a backfill). Entries have
no tags of their own (taggings are per feed, i.e. folders).
"""
import time
from datetime import datetime, timezone

from readers import Client, Entry, Reader, basic, unix

PAGE = 100
MAX_PAGES = 100         # 10,000 entries in a backfill
LOOKBACK_DAYS = 14


class Feedbin(Reader):
    name = "feedbin"

    def __init__(self, base, user, password, gap=1.0, lookback_days=LOOKBACK_DAYS, max_pages=MAX_PAGES, clock=time.time):
        self.http = Client(base or "https://api.feedbin.com", {"Authorization": basic(user, password)}, gap=gap,
                           name="Feedbin")
        self.lookback, self.max_pages, self.clock = lookback_days, max_pages, clock
        self._feeds = None

    def feeds(self):
        if self._feeds is None:
            folders = {}
            for t in self.http.call("/v2/taggings.json") or []:
                folders.setdefault(str(t.get("feed_id")), t.get("name") or "")
            self._feeds = {str(s.get("feed_id")): (s.get("title") or "", folders.get(str(s.get("feed_id")), ""))
                           for s in self.http.call("/v2/subscriptions.json") or []}
        return self._feeds

    def entry(self, x):
        feed_id = str(x.get("feed_id") or "")
        feed, folder = self.feeds().get(feed_id, ("", ""))
        meta = {"feedbin_entry_id": str(x.get("id")), "feedbin_feed_id": feed_id}
        if feed:
            meta["feedbin_feed"] = feed
        if folder:
            meta["feedbin_folder"] = folder
        return Entry(id=str(x.get("id")), url=(x.get("url") or "").strip(), title=" ".join((x.get("title") or "").split()),
                     html=x.get("content") or x.get("summary") or "", published=unix(x.get("published")),
                     feed=feed, folder=folder, tags=[], meta=meta)

    def by_ids(self, ids):
        for i in range(0, len(ids), 100):
            for x in self.http.call("/v2/entries.json", [("ids", ",".join(ids[i:i + 100]))], missing=(404,)) or []:
                yield x

    def read(self, known, backfill=False):
        params = [("read", "true"), ("per_page", PAGE)]
        if not backfill:
            since = datetime.fromtimestamp(self.clock() - self.lookback * 86400, timezone.utc)
            params.append(("since", since.strftime("%Y-%m-%dT%H:%M:%S.000000Z")))
        seen = set()
        for page in range(1, self.max_pages + 1):
            items = self.http.call("/v2/entries.json", params + [("page", page)], missing=(404,)) or []
            for x in items:
                seen.add(str(x.get("id")))
                if not known(str(x.get("id"))):
                    yield self.entry(x)
            if len(items) < PAGE:
                break
        recent = [str(i) for i in self.http.call("/v2/recently_read_entries.json") or []]
        late = [i for i in recent if i not in seen and not known(i)]
        for x in self.by_ids(late):
            yield self.entry(x)

    def starred(self, known):
        ids = [str(i) for i in self.http.call("/v2/starred_entries.json") or []]
        for x in self.by_ids([i for i in ids if not known(i)]):
            yield self.entry(x)
