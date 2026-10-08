"""The NewsBlur reader: GET calls only, with a NewsBlur OAuth access token (`Authorization: Bearer`).

What NewsBlur's API gives (read from its source, apps/reader/views.py and models.py, 2026-10-04):
- GET /reader/read_stories?page=N&limit=L&order=newest: the stories marked read ONE BY ONE, newest read first. NewsBlur
  keeps them as a Redis list (`lRS:<user>`) trimmed to the last 1,001 and stores no read time. A bulk "mark all as read"
  is not in that list. Each page returns L+1 stories (Redis LRANGE is inclusive), so pages overlap by one.
- GET /reader/starred_story_hashes?include_timestamps=true: every starred story as [hash, unix time], newest first
  (the time is the star's, or its last edit's).
- GET /reader/starred_stories?h=<hash>&h=…: those starred stories in full (at most 100 a call), with `user_tags`
  and `starred_timestamp`.
- GET /reader/feeds?flat=true&update_counts=false: feed titles and folders (`flat_folders`).
Every story has story_hash, story_title, story_permalink, story_content (the feed's HTML), story_timestamp,
story_feed_id, story_authors and story_tags. Without a valid token NewsBlur answers 200 with `authenticated: false`.

NewsBlur's OAuth scopes are not checked per call, so the token could change state: this module only ever sends GET.
"""
import json
import time
import urllib.error
import urllib.parse
import urllib.request

from readers import OPENER, Entry, Reader, ReaderError

PAGE = 50              # stories per read_stories page (NewsBlur's default is 10; 1,001 is the most it keeps)
MAX_PAGES = 25         # 25 x 50 covers the whole 1,001-story list
STARRED_BATCH = 100    # NewsBlur's cap for starred_stories?h=
GAP = 2.0              # seconds between calls to NewsBlur (its starred endpoints allow about 50 calls in 5 minutes)


class NewsBlur(Reader):
    name = "newsblur"

    def __init__(self, base, token, page=PAGE, max_pages=MAX_PAGES, gap=GAP, timeout=60, starred_max=1000):
        self.base, self.token = base.rstrip("/"), token
        self.page, self.max_pages, self.gap, self.timeout = page, max_pages, gap, timeout
        self.starred_max = starred_max          # starred stories fetched in full per run (backfill comes in slices)
        self.last_call = 0.0
        self._feeds = None

    # -- HTTP ---------------------------------------------------------------------------------------------------------

    def get(self, path, params=()):
        wait = self.last_call + self.gap - time.time()
        if wait > 0:
            time.sleep(wait)
        url = self.base + path + ("?" + urllib.parse.urlencode(list(params)) if params else "")
        req = urllib.request.Request(url, method="GET", headers={
            "Authorization": "Bearer " + self.token, "Accept": "application/json",
            "User-Agent": "machiya-feed-import (read-only)"})
        try:
            with OPENER.open(req, timeout=self.timeout) as r:      # a redirect is refused, never followed
                body = json.load(r)
        except urllib.error.HTTPError as e:
            e.close()
            raise ReaderError("NewsBlur %s: HTTP %d" % (path, e.code))
        except (urllib.error.URLError, OSError, ValueError) as e:
            raise ReaderError("NewsBlur %s: %s" % (path, e))
        finally:
            self.last_call = time.time()
        if not isinstance(body, dict):
            raise ReaderError("NewsBlur %s: not a JSON object" % path)
        if body.get("authenticated") is False:
            raise ReaderError("NewsBlur refused the token (authenticated: false)")
        return body

    # -- feeds --------------------------------------------------------------------------------------------------------

    def feeds(self):
        """{feed id (str): (title, folder)}; fetched once per instance."""
        if self._feeds is None:
            body = self.get("/reader/feeds", [("flat", "true"), ("update_counts", "false")])
            folder_of = {}
            for folder, ids in (body.get("flat_folders") or {}).items():
                for fid in ids or []:
                    folder_of.setdefault(str(fid), folder.strip() or "")
            feeds = body.get("feeds") or {}
            if isinstance(feeds, list):
                feeds = {str(f.get("id")): f for f in feeds}
            self._feeds = {str(fid): ((f or {}).get("feed_title") or "", folder_of.get(str(fid), ""))
                           for fid, f in feeds.items()}
        return self._feeds

    def _extra_feeds(self, body):
        """Feeds NewsBlur sends along with stories (ones the owner is no longer subscribed to)."""
        extra = body.get("feeds") or {}
        items = extra.values() if isinstance(extra, dict) else extra
        for f in items:
            if isinstance(f, dict) and f.get("id") is not None:
                self.feeds().setdefault(str(f["id"]), (f.get("feed_title") or "", ""))

    def entry(self, s, starred_at=None):
        feed_id = str(s.get("story_feed_id") or "")
        feed, folder = self.feeds().get(feed_id, ("", ""))
        tags = [str(t) for t in (s.get("user_tags") or []) if str(t).strip()]
        if starred_at is None and s.get("starred_timestamp"):
            starred_at = int(s["starred_timestamp"])
        meta = {"newsblur_story_hash": s.get("story_hash") or "", "newsblur_feed_id": feed_id}
        if feed:
            meta["newsblur_feed"] = feed
        if folder:
            meta["newsblur_folder"] = folder
        if tags:
            meta["newsblur_tags"] = tags
        try:
            published = int(s.get("story_timestamp") or 0)
        except (TypeError, ValueError):
            published = 0
        return Entry(id=s.get("story_hash") or "", url=(s.get("story_permalink") or "").strip(),
                     title=" ".join((s.get("story_title") or "").split()), html=s.get("story_content") or "",
                     published=published, starred_at=starred_at, feed=feed, folder=folder, tags=tags, meta=meta)

    # -- the two streams ----------------------------------------------------------------------------------------------

    def read(self, known, backfill=False):
        seen = set()
        for page in range(1, self.max_pages + 1):
            body = self.get("/reader/read_stories", [("page", page), ("limit", self.page), ("order", "newest")])
            stories = [s for s in body.get("stories") or [] if s.get("story_hash")]
            self._extra_feeds(body)
            fresh = [s for s in stories if s["story_hash"] not in seen]
            if not fresh:
                return
            new = False
            for s in fresh:
                seen.add(s["story_hash"])
                if not known(s["story_hash"]):
                    new = True
                    yield self.entry(s)
            if not new and not backfill:
                return                       # a whole page we already have: everything older is done too

    def starred(self, known):
        body = self.get("/reader/starred_story_hashes", [("include_timestamps", "true")])
        pairs = []
        for item in body.get("starred_story_hashes") or []:
            h, ts = (item[0], item[1]) if isinstance(item, (list, tuple)) else (item, None)
            if h and not known(h):
                pairs.append((h, int(ts) if ts else None))
        pairs = pairs[:self.starred_max]
        for i in range(0, len(pairs), STARRED_BATCH):
            chunk = dict(pairs[i:i + STARRED_BATCH])
            body = self.get("/reader/starred_stories", [("h", h) for h in chunk])
            self._extra_feeds(body)
            for s in body.get("stories") or []:
                if s.get("story_hash") in chunk:
                    yield self.entry(s, starred_at=chunk[s["story_hash"]] or None)
