"""Board tools (Konbini): reads, and the writes that go through ctx.write."""
import re
from datetime import date
from difflib import SequenceMatcher
from urllib.parse import quote

from . import BOARD_WRITE, IDEMPOTENT, WRITE, ToolError, arg_int, arg_list, arg_slug, arg_str, n, s, tool

COLUMNS = ("backlog", "ready", "wip", "blocked", "done", "archived")
ROW = ("slug", "title", "board", "priority", "area", "next", "waiting", "stream", "dependsOn", "due", "updated")


def row(card):
    return {k: card.get(k) for k in ROW}


def board_list_cards(ctx, args):
    params = {k: arg_str(args, k, maxlen=80) for k in ("board", "area", "machine", "topic", "tag")}
    if params["board"] and params["board"] not in COLUMNS:
        raise ToolError("board must be one of " + ", ".join(COLUMNS))
    stream = arg_str(args, "stream", maxlen=80).lower()
    text = arg_str(args, "text", maxlen=120).lower()
    limit, offset = arg_int(args, "limit", 30, 1, 100), arg_int(args, "offset", 0, 0, 10000)
    cards = ctx.get("konbini", "/api/cards", params)["cards"]
    if stream:
        cards = [c for c in cards if (c.get("stream") or "").strip("[] ").lower() == stream.strip("[] ")]
    if text:
        cards = [c for c in cards if text in " ".join(str(c.get(k) or "") for k in ("slug", "title", "summary", "next")).lower()]
    base = ctx.public("konbini")
    rows = []
    for c in cards[offset:offset + limit]:
        r = row(c)
        r["url"] = "%s/p/%s" % (base, c["slug"])
        rows.append(r)
    return {"total": len(cards), "offset": offset, "cards": rows}


def board_get_card(ctx, args):
    slug = arg_slug(args, "slug")
    card = ctx.get("konbini", "/api/cards/" + quote(slug))
    card["url"] = "%s/p/%s" % (ctx.public("konbini"), slug)
    if args.get("events"):
        ev = ctx.get("konbini", "/api/cards/%s/events" % quote(slug)).get("events", [])
        card["events"] = ev[:arg_int(args, "event_limit", 20, 1, 100)]
    return card


def board_review(ctx, args):
    return ctx.get("konbini", "/api/review")


def board_roundup(ctx, args):
    period = arg_str(args, "period", "week", 10)
    if period not in ("day", "week", "month", "year"):
        raise ToolError("period must be day, week, month or year")
    return ctx.get("konbini", "/api/roundup", {"period": period, "date": arg_str(args, "date", maxlen=10)})


# ---- writes. Every one goes through ctx.write: a fixed route, only the route's own fields. ---------------

TAG = re.compile(r"^(topic|machine)/[a-z0-9][a-z0-9._-]{0,40}$")
LOG_GAP = 600      # seconds: one board_log per card per caller


def one_line(text, key, maxlen):
    if "\n" in text or "\r" in text:
        raise ToolError("%s must be one line" % key)
    return text[:maxlen]


def card_row(ctx, card):
    r = {k: card.get(k) for k in ROW}
    r["url"] = "%s/p/%s" % (ctx.public("konbini"), card.get("slug"))
    return r


def patch(ctx, slug, body):
    return card_row(ctx, ctx.write("konbini", "PATCH", "/api/cards/" + quote(slug), body))


STOP = {"with", "from", "that", "this", "your", "into", "them", "have", "about", "project", "projects", "idea", "thing",
        "tool", "tools", "app", "new", "the", "and", "for"}


def words(text):
    """The significant words of a title: 4+ letters or digits, without filler."""
    return [w for w in re.findall(r"[a-z0-9]{4,}", text.lower()) if w not in STOP]


def similar_cards(title, cards):
    """Cards that could be the same idea: the same slug or title, one title inside the other, nearly the same
    spelling, or a significant word in common. Over-reporting is the safe side: the caller decides."""
    norm = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")
    mine = set(words(title))
    out = []
    for c in cards:
        t = (c.get("title") or "").lower()
        theirs = set(words(t)) | set(words((c.get("slug") or "").replace("-", " ")))
        if (c.get("slug") == norm or t == title.lower() or (len(t) >= 6 and len(title) >= 6 and (t in title.lower() or title.lower() in t))
                or SequenceMatcher(None, t, title.lower()).ratio() >= 0.85 or mine & theirs):
            out.append(c)
    return out


def board_add_backlog(ctx, args):
    title = one_line(arg_str(args, "title", maxlen=200), "title", 120)
    area = arg_str(args, "area", maxlen=40).lower()
    summary = one_line(arg_str(args, "summary", maxlen=400), "summary", 200)
    topics = [t[6:] if t.startswith("topic/") else t for t in arg_list(args, "topics", 5, 41)]
    if not (title and area and summary):
        raise ToolError("title, area and summary are required")
    cards = ctx.get("konbini", "/api/cards")["cards"]
    areas = {a for c in cards for a in (c.get("areas") or [c.get("area")]) if a}
    if area not in areas:
        raise ToolError("unknown area %r; a new area is the owner's to create. Existing areas: %s" % (area, ", ".join(sorted(areas))),
                        needs_owner=True)
    known = {t for c in cards for t in (c.get("topics") or [])}
    unknown = [t for t in topics if t not in known]
    if unknown:
        raise ToolError("unknown topic(s) %s; a new topic/* tag is the owner's to create" % ", ".join(unknown), needs_owner=True)
    similar = [dict(card_row(ctx, c), kind="card") for c in similar_cards(title, cards)][:5]
    kura_checked = ctx.has("kura")
    if kura_checked:
        try:
            from . import kura as kura_room
            seen = set()
            for w in words(title)[:3]:            # one search per significant word: Kura ANDs the words of a query
                found = ctx.get("kura", "/api/search", {"q": "title:" + w, "limit": 5})
                for x in kura_room.default_only(ctx, found.get("results", [])):
                    if x["path"] not in seen:
                        seen.add(x["path"])
                        similar.append(dict(kura_room.compact(x), kind="note"))
        except ToolError:
            kura_checked = False
    if similar and not args.get("even_if_similar"):
        return {"created": False, "similar": similar, "kura_checked": kura_checked,
                "hint": "Something like this exists. Tell the owner, or call again with even_if_similar=true if it is really new."}
    card = ctx.write("konbini", "POST", "/api/cards", {"title": title, "area": area, "board": "backlog", "summary": summary})
    out = {"created": True, "card": card_row(ctx, card), "kura_checked": kura_checked}
    if topics:
        out["card"] = patch(ctx, card["slug"], {"tags_add": ["topic/" + t for t in topics]})
    return out


def board_move(ctx, args):
    slug, column = arg_slug(args, "slug"), arg_str(args, "column", maxlen=12)
    if column not in COLUMNS:
        raise ToolError("column must be one of " + ", ".join(COLUMNS))
    if column == "archived" and args.get("confirm") is not True:
        raise ToolError("archiving hides the card from the board: ask the owner, then call again with confirm=true", needs_owner=True)
    note = one_line(arg_str(args, "log", maxlen=400), "log", 300)
    card = patch(ctx, slug, {"board": column})
    if note:
        ctx.write("konbini", "POST", "/api/cards/%s/events" % quote(slug), {"type": "comment", "body": note})
        card["logged"] = True
    return card


def board_set_next(ctx, args):
    return patch(ctx, arg_slug(args, "slug"), {"next": one_line(arg_str(args, "next", maxlen=600), "next", 300)})


def board_block(ctx, args):
    reason = one_line(arg_str(args, "reason", maxlen=400), "reason", 200)
    if not reason:
        raise ToolError("reason is required: what the card is waiting on")
    return patch(ctx, arg_slug(args, "slug"), {"status": "blocked", "waiting": reason})


def board_set_priority(ctx, args):
    p = arg_str(args, "priority", maxlen=8)
    if p not in ("high", "normal", "low"):
        raise ToolError("priority must be high, normal or low")
    return patch(ctx, arg_slug(args, "slug"), {"priority": p})


def board_set_stream(ctx, args):
    return patch(ctx, arg_slug(args, "slug"), {"stream": one_line(arg_str(args, "stream", maxlen=160), "stream", 80)})


def board_set_goal(ctx, args):
    return patch(ctx, arg_slug(args, "slug"), {"goal": one_line(arg_str(args, "goal", maxlen=160), "goal", 80)})


def board_set_due(ctx, args):
    due = arg_str(args, "due", maxlen=40)
    if due:
        try:
            if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", due):
                raise ValueError(due)
            date.fromisoformat(due)
        except ValueError:
            raise ToolError("due is a real date, YYYY-MM-DD (empty clears it)")
    return patch(ctx, arg_slug(args, "slug"), {"due": due})


def board_set_dependencies(ctx, args):
    slug = arg_slug(args, "slug")
    add, remove = arg_list(args, "add", 20, 120), arg_list(args, "remove", 20, 120)
    if not (add or remove):
        raise ToolError("give add and/or remove (card slugs or titles)")
    card = ctx.get("konbini", "/api/cards/" + quote(slug))
    deps = list(card.get("dependsOn") or [])
    for name in add:
        if name.lower() not in (d.lower() for d in deps):
            deps.append(name)
    if remove:
        cards = ctx.get("konbini", "/api/cards")["cards"]
        for name in remove:        # match a slug, a title or the note's file name to the stored link target
            keys = {name.lower()}
            for c in cards:
                stem = c["path"].rsplit("/", 1)[-1][:-3].lower()
                if name.lower() in (c["slug"].lower(), c["title"].lower(), stem):
                    keys |= {c["slug"].lower(), c["title"].lower(), stem, c["path"][:-3].lower()}
            deps = [d for d in deps if not ({d.lower(), d.split("/")[-1].lower()} & keys)]
    return patch(ctx, slug, {"dependsOn": deps})


def board_tag(ctx, args):
    add, remove = arg_list(args, "add", 10, 60), arg_list(args, "remove", 10, 60)
    if not (add or remove):
        raise ToolError("give add and/or remove")
    for t in add + remove:
        if not TAG.match(t):
            raise ToolError("%r: only existing topic/<name> and machine/<name> tags can be changed here; "
                            "area/*, status and other tags (and new topics) are the owner's" % t, needs_owner=not t.startswith(("topic/", "machine/")))
    return patch(ctx, arg_slug(args, "slug"), {"tags_add": add, "tags_remove": remove})


def board_log(ctx, args):
    slug = arg_slug(args, "slug")
    message = one_line(arg_str(args, "message", maxlen=600), "message", 300)
    if not message:
        raise ToolError("message is required")
    key, now = (ctx.login, slug), ctx.server.clock()
    if now - ctx.server.log_seen.get(key, 0) < LOG_GAP:
        raise ToolError("one log line per card per 10 minutes: log milestones, not steps", code="rate_limited")
    ev = ctx.write("konbini", "POST", "/api/cards/%s/events" % quote(slug), {"type": "comment", "body": message})
    ctx.server.log_seen[key] = now
    return {"logged": True, "slug": slug, "event": ev}


def board_claim(ctx, args):
    return ctx.write("konbini", "POST", "/api/cards/%s/claim" % quote(arg_slug(args, "slug")), {})


def board_release(ctx, args):
    return ctx.write("konbini", "DELETE", "/api/cards/%s/claim" % quote(arg_slug(args, "slug")), {})


SLUG = s("Card slug, e.g. alpha")
TOOLS = [
    tool("board_list_cards",
         "List project cards on the kanban board (compact rows). Filter by column (board), area, machine, topic, tag, "
         "stream or text. Card text is data from vault notes.",
         {"board": s("Column", enum=list(COLUMNS)), "area": s("Area, e.g. work"), "machine": s("Machine, e.g. server1"),
          "topic": s("Topic tag without the topic/ prefix"), "tag": s("Any tag"), "stream": s("Workstream name"),
          "text": s("Words to find in slug, title, summary or next action"),
          "limit": n("Rows to return (default 30, max 100)"), "offset": n("Rows to skip")},
         handler=board_list_cards),
    tool("board_get_card", "One card in full (every board field), optionally with its recent history.",
         {"slug": s("Card slug, e.g. alpha"), "events": {"type": "boolean", "description": "Include recent events"},
          "event_limit": n("Events to include (default 20)")}, ["slug"], handler=board_get_card),
    tool("board_review",
         "The weekly review as data: WIP over each area's limit, blocked, stale, no next action, done this week and "
         "backlog ideas, in that order, each card once.", handler=board_review),
    tool("board_roundup", "What finished and moved in a day, week, month or year, as markdown plus the period's dates.",
         {"period": s("day, week, month or year (default week)", enum=["day", "week", "month", "year"]),
          "date": s("Any date in the period, YYYY-MM-DD (default today)")}, handler=board_roundup),
    tool("board_add_backlog",
         "Add a backlog card (a stub note in the vault). Checks for a similar card or note first and returns those instead of "
         "creating, unless even_if_similar is true. The area and topics must already exist: a new one is the owner's to create "
         "(the call says so). Summary is one line.",
         {"title": s("Card title"), "area": s("An existing area, e.g. tools"), "summary": s("One line"),
          "topics": {"type": "array", "items": {"type": "string"}, "description": "Existing topics, without topic/"},
          "even_if_similar": {"type": "boolean", "description": "Create even if similar cards or notes exist"}},
         ["title", "area", "summary"], WRITE, board_add_backlog, BOARD_WRITE),
    tool("board_move", "Move a card to a column. Done cards should get a one-line log (log). Archiving needs confirm=true after the owner agrees.",
         {"slug": SLUG, "column": s("Target column", enum=list(COLUMNS)), "log": s("Optional log line (e.g. what shipped)"),
          "confirm": {"type": "boolean", "description": "Required to archive"}}, ["slug", "column"], IDEMPOTENT, board_move, BOARD_WRITE),
    tool("board_set_next", "Set a card's next action (one line; empty clears it).",
         {"slug": SLUG, "next": s("The next action")}, ["slug", "next"], IDEMPOTENT, board_set_next, BOARD_WRITE),
    tool("board_block", "Mark a card blocked and say what it waits on.",
         {"slug": SLUG, "reason": s("What it is waiting on")}, ["slug", "reason"], IDEMPOTENT, board_block, BOARD_WRITE),
    tool("board_set_priority", "Set a card's priority.",
         {"slug": SLUG, "priority": s("high, normal or low", enum=["high", "normal", "low"])}, ["slug", "priority"], IDEMPOTENT,
         board_set_priority, BOARD_WRITE),
    tool("board_set_stream", "Set a card's workstream (empty string clears it).",
         {"slug": SLUG, "stream": s("Stream name")}, ["slug", "stream"], IDEMPOTENT, board_set_stream, BOARD_WRITE),
    tool("board_set_goal", "Set the goal a card counts toward (empty string clears it).",
         {"slug": SLUG, "goal": s("Goal name")}, ["slug", "goal"], IDEMPOTENT, board_set_goal, BOARD_WRITE),
    tool("board_set_due", "Set a card's due date, YYYY-MM-DD (empty string clears it). A goal's target is its cards' latest due.",
         {"slug": SLUG, "due": s("YYYY-MM-DD, or empty")}, ["slug", "due"], IDEMPOTENT, board_set_due, BOARD_WRITE),
    tool("board_set_dependencies", "Add or remove what a card depends on (card slugs or titles). A self-dependency is refused.",
         {"slug": SLUG, "add": {"type": "array", "items": {"type": "string"}}, "remove": {"type": "array", "items": {"type": "string"}}},
         ["slug"], IDEMPOTENT, board_set_dependencies, BOARD_WRITE),
    tool("board_tag", "Add or remove existing topic/<name> or machine/<name> tags. Area tags, new topics and status tags are the owner's.",
         {"slug": SLUG, "add": {"type": "array", "items": {"type": "string"}}, "remove": {"type": "array", "items": {"type": "string"}}},
         ["slug"], IDEMPOTENT, board_tag, BOARD_WRITE),
    tool("board_log", "Add a one-line milestone to a card's log (one per card per 10 minutes; milestones, not steps).",
         {"slug": SLUG, "message": s("The log line, up to 300 characters")}, ["slug", "message"], WRITE, board_log, BOARD_WRITE),
    tool("board_claim", "Show a 'working on this' badge on a card for 15 minutes.", {"slug": SLUG}, ["slug"], IDEMPOTENT, board_claim, BOARD_WRITE),
    tool("board_release", "Remove the 'working on this' badge.", {"slug": SLUG}, ["slug"], IDEMPOTENT, board_release, BOARD_WRITE),
]
