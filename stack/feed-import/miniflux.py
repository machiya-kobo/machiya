"""The Miniflux reader: GET /v1/entries only, with an API key (`X-Auth-Token`; Settings → API Keys).

- read:    GET /v1/entries?status=read&order=changed_at&direction=desc&limit=100&offset=N
- starred: GET /v1/entries?starred=true&order=changed_at&direction=desc&limit=100&offset=N
`changed_at` is set whenever an entry's status or star changes, so newest-changed first finds new reads and stars on
the first page (stop at a page with nothing new). It stands in for the read and star time: Miniflux keeps neither.
`order=changed_at` is accepted by Miniflux's validator (internal/validator/entry.go) though the API docs list fewer.
Entries carry no user tags (`tags` are the feed's own categories), so a starred entry never takes a label from them.
Miniflux deletes read entries after CLEANUP_ARCHIVE_READ_DAYS (60 by default): the backfill reaches that far.
"""
from readers import Client, Entry, Reader, unix

PAGE = 100
MAX_PAGES = 100         # 10,000 entries in a backfill


class Miniflux(Reader):
    name = "miniflux"

    def __init__(self, base, token, gap=1.0, page=PAGE, max_pages=MAX_PAGES):
        self.http = Client(base, {"X-Auth-Token": token}, gap=gap, name="Miniflux")
        self.page, self.max_pages = page, max_pages

    def entry(self, x, stream):
        feed = x.get("feed") or {}
        category = (feed.get("category") or {}).get("title") or ""
        changed = unix(x.get("changed_at"))
        meta = {"miniflux_entry_id": str(x.get("id")), "miniflux_feed_id": str(x.get("feed_id") or feed.get("id") or "")}
        if feed.get("title"):
            meta["miniflux_feed"] = feed["title"]
        if category:
            meta["miniflux_category"] = category
        return Entry(id=str(x.get("id")), url=(x.get("url") or "").strip(), title=" ".join((x.get("title") or "").split()),
                     html=x.get("content") or "", published=unix(x.get("published_at")),
                     read_at=(changed or None) if stream == "read" else None,
                     starred_at=(changed or None) if stream == "starred" else None,
                     feed=feed.get("title") or "", folder=category, tags=[], meta=meta)

    def _walk(self, filters, known, stream, backfill):
        for n in range(self.max_pages):
            body = self.http.call("/v1/entries", filters + [("order", "changed_at"), ("direction", "desc"),
                                                            ("limit", self.page), ("offset", n * self.page)])
            items = (body or {}).get("entries") or []
            new = False
            for x in items:
                if x.get("id") is not None and not known(str(x["id"])):
                    new = True
                    yield self.entry(x, stream)
            if len(items) < self.page or (not new and not backfill):
                return

    def read(self, known, backfill=False):
        yield from self._walk([("status", "read")], known, "read", backfill)

    def starred(self, known):
        yield from self._walk([("starred", "true")], known, "starred", False)
