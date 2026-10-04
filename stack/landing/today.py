"""Today, for the launcher at / (docs/services/landing.md): what the rooms say is going on, read from their own APIs.

- Working on: Konbini's WIP cards (GET /api/cards), in its own order (rank, priority), the most recently updated first
  after that; with each card's next step.
- Due soon: cards with a `due` date in the next 14 days (or overdue), not done.
- Notes changed: Kura's newest changes (GET /api/recent).
- Saved & read: Hister's newest pages, vault notes left out, a feed reader's reads and stars marked (the Hister probe's
  search; the owner's token when Hister has users).
- Garden: Niwa's most recently tended published notes (GET /feed.xml), the last 30 days.

Every section is optional: an app that is missing, down or refuses leaves its section out (None), never an error.
Only GETs; the same timeouts, size limit and tokens as the probes.
"""
import datetime
import xml.etree.ElementTree as ET
from email.utils import parsedate_to_datetime

import probes

WORKING, DUE, NOTES, PAGES, GARDEN = 5, 6, 5, 5, 5
DUE_DAYS, GARDEN_DAYS = 14, 30
CLOSED = ("done", "archived")


def day(value):
    try:
        return datetime.date.fromisoformat(str(value)[:10])
    except ValueError:
        return None


def konbini(base, link, today, timeout, headers):
    """(working, due, wip count) from Konbini's /api/cards."""
    d = probes.fetch_json(base + "/api/cards", headers, timeout)
    cards = [c for c in d.get("cards") or [] if isinstance(c, dict) and c.get("slug")]

    def card(c, **extra):
        return dict({"title": probes.text(c.get("title"), 200) or c["slug"], "url": "%s/p/%s" % (link, probes.quote(str(c["slug"]))),
                     "next": probes.text(c.get("next"), 200), "area": probes.text(c.get("area"), 40),
                     "board": probes.text(c.get("board"), 20)}, **extra)

    wip = [c for c in cards if c.get("board") == "wip"]
    wip.sort(key=lambda c: (str(c.get("updated") or "")), reverse=True)
    wip.sort(key=lambda c: (c["rank"] if isinstance(c.get("rank"), (int, float)) else 1e9,
                            c["priority"] if isinstance(c.get("priority"), int) else 9))
    due = []
    for c in cards:
        when = day(c.get("due")) if c.get("due") else None
        if when and c.get("board") not in CLOSED and (when - today).days <= DUE_DAYS:
            due.append(card(c, due=when.isoformat(), days=(when - today).days))
    due.sort(key=lambda c: c["due"])
    return [card(c) for c in wip[:WORKING]], due[:DUE], len(wip)


def kura(base, timeout, headers):
    d = probes.fetch_json(base + "/api/recent?limit=%d" % NOTES, headers, timeout)
    out = []
    for n in (d.get("results") or [])[:NOTES]:
        if isinstance(n, dict) and n.get("url"):
            out.append({"title": probes.text(n.get("title"), 200) or probes.text(n.get("path"), 200),
                        "url": probes.text(n.get("url"), 2000), "folder": probes.text(n.get("folder"), 120),
                        "at": probes.num(n.get("changed"))})
    return out


def niwa(base, now, timeout, headers):
    _, _, body = probes.fetch(base + "/feed.xml", dict(headers, Accept="application/rss+xml, application/xml"), timeout)
    try:
        root = ET.fromstring(body)
    except ET.ParseError:
        raise probes.FetchError("not a feed")
    out = []
    for item in root.iter("item"):
        at = None
        try:
            at = parsedate_to_datetime(item.findtext("pubDate") or "").timestamp()
        except (TypeError, ValueError):
            pass
        if at is not None and now - at > GARDEN_DAYS * 86400:
            continue
        link = (item.findtext("link") or "").strip()
        if link.startswith(("http://", "https://")):
            out.append({"title": probes.text(item.findtext("title"), 200) or link, "url": link, "at": at,
                        "summary": probes.text(item.findtext("description"), 160)})
        if len(out) >= GARDEN:
            break
    return out


def gather(config, apps, now, today=None):
    """{"working", "due", "notes", "pages", "garden", "wip"}: each a list, or None when its app can't say."""
    today = today or datetime.datetime.fromtimestamp(now, config.tz or datetime.timezone.utc).date()   # LANDING_TZ's day
    out = {"working": None, "due": None, "notes": None, "pages": None, "garden": None, "wip": None}

    def target(key):
        a = apps.get(key) or {}
        t = config.targets.get(key, "")
        return t.rstrip("/") if t and a.get("state") not in ("absent", "down") else ""

    t = target("konbini")
    if t:
        try:
            link = (config.links.get("konbini") or t).rstrip("/")
            out["working"], out["due"], out["wip"] = konbini(t, link, today, config.timeout,
                                                             probes.auth_headers("konbini", t, config.token))
        except probes.FetchError:
            pass
    t = target("kura")
    if t:
        try:
            out["notes"] = kura(t, config.timeout, probes.auth_headers("kura", t, config.token))
        except probes.FetchError:
            pass
    t = target("niwa")
    if t:
        try:
            out["garden"] = niwa(t, now, config.timeout, probes.auth_headers("niwa", t, config.token)) or None
        except probes.FetchError:
            pass
    h = apps.get("hister") or {}
    if h.get("state") == "up" and (h.get("data") or {}).get("pages"):
        out["pages"] = h["data"]["pages"][:PAGES]
    return out
