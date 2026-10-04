"""The FreshRSS reader, through its Google Reader API (`<base>/api/greader.php`), with the user's API password
(Profile → API management; not the login password). Read from FreshRSS's p/api/greader.php, 2026-10-04.

- login:   POST /accounts/ClientLogin (Email, Passwd) -> `Auth=<user>/<token>`, then `Authorization: GoogleLogin auth=…`
           (a login, not a change; the password never goes in a URL)
- ids:     GET /reader/api/0/stream/items/ids?s=user/-/state/com.google/read&n=1000&c=<continuation>
           (and s=user/-/state/com.google/starred): decimal item ids, by entry id (when FreshRSS got it), newest first
- items:   POST /reader/api/0/stream/items/contents with i=<id>&i=…  (FreshRSS takes the ids only in a POST body; it
           returns the items and changes nothing)
- feeds:   GET /reader/api/0/subscription/list?output=json: titles and each feed's folder
The read list is in entry order, not read order, and FreshRSS keeps no read or star time: every run diffs the whole id
list against the state, and the time is when an entry was first seen (its date in a backfill). An item's labels
(`user/-/label/X`) other than its feed's folder are the owner's tags. FreshRSS purges read, unstarred entries after
about 3 months by default (keep_period): the backfill reaches that far.
"""
from readers import Client, Entry, Reader, ReaderError

IDS = 1000              # ids per items/ids page
BATCH = 100             # ids per items/contents call
MAX_IDS = 100000


def decimal_id(value):
    """`tag:google.com,2005:reader/item/000000000000001f` or `31` -> "31"."""
    value = str(value)
    if value.isdigit() and not value.startswith("0"):
        return value
    tail = value.rsplit("/", 1)[-1]
    try:
        return str(int(tail, 16))
    except ValueError:
        return value


class FreshRSS(Reader):
    name = "freshrss"

    def __init__(self, base, user, api_password, gap=0.5):
        self.base = base.rstrip("/")
        if not self.base.endswith("/api/greader.php"):
            self.base += "/api/greader.php"
        self.user, self.password, self.gap = user, api_password, gap
        self.http = None
        self._feeds = None

    def login(self):
        if self.http is None:
            text = Client(self.base, gap=0, name="FreshRSS").call(
                "/accounts/ClientLogin", data=[("Email", self.user), ("Passwd", self.password)], raw=True)
            auth = next((line[5:].strip() for line in text.splitlines() if line.startswith("Auth=")), "")
            if not auth:
                raise ReaderError("FreshRSS: ClientLogin gave no Auth token")
            self.http = Client(self.base, {"Authorization": "GoogleLogin auth=" + auth}, gap=self.gap, name="FreshRSS")
        return self.http

    def feeds(self):
        if self._feeds is None:
            body = self.login().call("/reader/api/0/subscription/list", [("output", "json")])
            self._feeds = {}
            for f in (body or {}).get("subscriptions") or []:
                cats = f.get("categories") or []
                self._feeds[f.get("id") or ""] = (f.get("title") or "", (cats[0].get("label") or "") if cats else "")
        return self._feeds

    def ids(self, state):
        out, cont = [], ""
        while len(out) < MAX_IDS:
            params = [("s", "user/-/state/com.google/" + state), ("n", IDS), ("output", "json")]
            if cont:
                params.append(("c", cont))
            body = self.login().call("/reader/api/0/stream/items/ids", params) or {}
            out.extend(decimal_id(x.get("id")) for x in body.get("itemRefs") or [] if x.get("id") not in (None, "0", 0))
            cont = body.get("continuation") or ""
            if not cont or not body.get("itemRefs"):
                return out
        return out

    def items(self, ids):
        for i in range(0, len(ids), BATCH):
            chunk = ids[i:i + BATCH]
            body = self.login().call("/reader/api/0/stream/items/contents", [("output", "json")],
                                     data=[("i", x) for x in chunk]) or {}
            for item in body.get("items") or []:
                yield item

    def entry(self, item):
        feed_id = (item.get("origin") or {}).get("streamId") or ""
        feed, folder = self.feeds().get(feed_id, ((item.get("origin") or {}).get("title") or "", ""))
        labels = [c[len("user/-/label/"):] for c in item.get("categories") or [] if str(c).startswith("user/-/label/")]
        tags = [t for t in labels if t and t != folder]
        url = next((x.get("href") for x in (item.get("canonical") or []) + (item.get("alternate") or []) if x.get("href")), "")
        html = ((item.get("content") or {}).get("content") or (item.get("summary") or {}).get("content") or "")
        iid = decimal_id(item.get("id"))
        meta = {"freshrss_item_id": iid, "freshrss_feed_id": feed_id.replace("feed/", "", 1)}
        if feed:
            meta["freshrss_feed"] = feed
        if folder:
            meta["freshrss_folder"] = folder
        if tags:
            meta["freshrss_tags"] = tags
        return Entry(id=iid, url=url.strip(), title=" ".join((item.get("title") or "").split()), html=html,
                     published=int(item.get("published") or 0), feed=feed, folder=folder, tags=tags, meta=meta)

    def _stream(self, state, known):
        new = [x for x in self.ids(state) if not known(x)]
        for item in self.items(new):
            yield self.entry(item)

    def read(self, known, backfill=False):
        yield from self._stream("read", known)

    def starred(self, known):
        yield from self._stream("starred", known)
